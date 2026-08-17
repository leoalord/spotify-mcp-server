"""Spotify Authorization Code with PKCE and memory-only access tokens."""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import importlib
import logging
import secrets
import time
import webbrowser
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Protocol
from urllib.parse import parse_qs, urlencode, urlparse

import httpx2

from spotify_mcp_server.spotify.config import SCOPES, Settings

logger = logging.getLogger(__name__)


class AuthenticationError(RuntimeError):
    """Raised when Spotify authorization is missing, rejected, or cannot refresh."""


class RefreshTokenStore(Protocol):
    """Minimal secret-store boundary; access tokens must never cross it."""

    async def load(self) -> str | None: ...

    async def save(self, refresh_token: str) -> None: ...


class KeyringBackend(Protocol):
    def get_password(self, service_name: str, username: str) -> str | None: ...

    def set_password(self, service_name: str, username: str, password: str) -> None: ...


class KeyringRefreshTokenStore:
    """Store only Spotify's refresh token in the operating system credential backend."""

    def __init__(
        self,
        service_name: str,
        client_id: str,
        *,
        backend: KeyringBackend | None = None,
    ) -> None:
        self.service_name = service_name
        self.username = f"spotify-client:{client_id}"
        self._backend = backend

    def _keyring(self) -> KeyringBackend:
        if self._backend is not None:
            return self._backend
        try:
            backend = importlib.import_module("keyring")
        except ImportError as exc:  # pragma: no cover - packaging guarantees the dependency
            raise AuthenticationError("The keyring package is required for Spotify OAuth") from exc
        return backend  # type: ignore[no-any-return]

    async def load(self) -> str | None:
        try:
            return await asyncio.to_thread(
                self._keyring().get_password, self.service_name, self.username
            )
        except Exception as exc:
            raise AuthenticationError(
                "Could not read the Spotify refresh token from keyring"
            ) from exc

    async def save(self, refresh_token: str) -> None:
        try:
            await asyncio.to_thread(
                self._keyring().set_password,
                self.service_name,
                self.username,
                refresh_token,
            )
        except Exception as exc:
            raise AuthenticationError(
                "Could not save the Spotify refresh token to keyring"
            ) from exc


@dataclass(slots=True)
class TokenSet:
    """Ephemeral token state. The access token is intentionally never serialized."""

    access_token: str
    refresh_token: str | None
    expires_at: float
    scope: str

    @classmethod
    def from_payload(cls, payload: dict[str, Any], previous: TokenSet | None = None) -> TokenSet:
        return cls(
            access_token=str(payload["access_token"]),
            refresh_token=payload.get("refresh_token")
            or (previous.refresh_token if previous else None),
            expires_at=time.time() + int(payload.get("expires_in", 3600)) - 30,
            scope=str(payload.get("scope") or (previous.scope if previous else "")),
        )


class SpotifyTokenProvider:
    """Supply memory-only access tokens and refresh them through an OS-backed secret store."""

    def __init__(
        self,
        settings: Settings,
        *,
        client: httpx2.AsyncClient | None = None,
        store: RefreshTokenStore | None = None,
    ) -> None:
        self.settings = settings
        self.store = store or KeyringRefreshTokenStore(settings.keyring_service, settings.client_id)
        self.client = client or httpx2.AsyncClient(timeout=20)
        self._owns_client = client is None
        self._lock = asyncio.Lock()
        self._token: TokenSet | None = None

    async def access_token(self, *, force_refresh: bool = False) -> str:
        async with self._lock:
            if (
                self._token is not None
                and not force_refresh
                and self._token.expires_at > time.time()
            ):
                return self._token.access_token

            refresh_token = self._token.refresh_token if self._token else await self.store.load()
            if not refresh_token:
                raise AuthenticationError("Run `spotify-mcp-auth` before using Spotify tools")
            previous = self._token or TokenSet("", refresh_token, 0, "")
            self._token = await self._refresh(previous)
            return self._token.access_token

    async def _refresh(self, token: TokenSet) -> TokenSet:
        if not token.refresh_token:
            raise AuthenticationError("Stored Spotify token has no refresh token; authorize again")
        response = await self.client.post(
            f"{self.settings.accounts_base_url}/api/token",
            data={
                "grant_type": "refresh_token",
                "refresh_token": token.refresh_token,
                "client_id": self.settings.client_id,
            },
        )
        if response.is_error:
            raise AuthenticationError(
                f"Spotify token refresh failed ({response.status_code}): {response.text}"
            )
        refreshed = TokenSet.from_payload(response.json(), previous=token)
        if refreshed.refresh_token and refreshed.refresh_token != token.refresh_token:
            await self.store.save(refreshed.refresh_token)
        return refreshed

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def _pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def _receive_callback(redirect_uri: str, expected_state: str) -> str:
    parsed = urlparse(redirect_uri)
    if parsed.hostname not in {"127.0.0.1", "localhost"} or parsed.port is None:
        raise AuthenticationError("Redirect URI must be a loopback URL with an explicit port")
    result: dict[str, str] = {}

    class CallbackHandler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            requested = urlparse(self.path)
            if requested.path != (parsed.path or "/"):
                self.send_response(404)
                self.end_headers()
                return
            query = parse_qs(requested.query)
            if query.get("state", [""])[0] != expected_state:
                result["error"] = "OAuth state did not match"
                status = 400
            elif "error" in query:
                result["error"] = query["error"][0]
                status = 400
            else:
                result["code"] = query.get("code", [""])[0]
                status = 200
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.end_headers()
            message = (
                "Spotify authorization received. You may close this window."
                if status == 200
                else f"Spotify authorization failed: {result.get('error', 'unknown error')}"
            )
            self.wfile.write(message.encode("utf-8"))

        def log_message(self, format: str, *args: object) -> None:
            return

    server = HTTPServer((parsed.hostname, parsed.port), CallbackHandler)
    deadline = time.monotonic() + 180
    try:
        while not result and time.monotonic() < deadline:
            server.timeout = max(0.0, deadline - time.monotonic())
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise AuthenticationError(result["error"])
    if not result.get("code"):
        raise AuthenticationError("Timed out waiting for Spotify authorization")
    return result["code"]


async def authorize(
    settings: Settings,
    *,
    open_browser: bool = True,
    store: RefreshTokenStore | None = None,
) -> None:
    if not settings.client_id:
        raise AuthenticationError("Set SPOTIFY_CLIENT_ID to your Spotify application client ID")
    verifier, challenge = _pkce_pair()
    state = secrets.token_urlsafe(24)
    query = urlencode(
        {
            "client_id": settings.client_id,
            "response_type": "code",
            "redirect_uri": settings.redirect_uri,
            "scope": " ".join(SCOPES),
            "code_challenge_method": "S256",
            "code_challenge": challenge,
            "state": state,
        }
    )
    url = f"{settings.accounts_base_url}/authorize?{query}"
    logger.info("Open this URL to authorize Spotify:\n%s", url)
    if open_browser:
        webbrowser.open(url)
    code = await asyncio.to_thread(_receive_callback, settings.redirect_uri, state)
    async with httpx2.AsyncClient(timeout=20) as client:
        response = await client.post(
            f"{settings.accounts_base_url}/api/token",
            data={
                "client_id": settings.client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": settings.redirect_uri,
                "code_verifier": verifier,
            },
        )
    if response.is_error:
        raise AuthenticationError(
            f"Spotify token exchange failed ({response.status_code}): {response.text}"
        )
    token = TokenSet.from_payload(response.json())
    if not token.refresh_token:
        raise AuthenticationError("Spotify did not return a refresh token")
    resolved_store = store or KeyringRefreshTokenStore(settings.keyring_service, settings.client_id)
    await resolved_store.save(token.refresh_token)
    logger.info("Authorization complete. Refresh token saved to the operating system keyring.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description="Authorize the Spotify MCP server using PKCE.")
    parser.add_argument(
        "--no-browser", action="store_true", help="Print the URL without opening it."
    )
    args = parser.parse_args()
    asyncio.run(authorize(Settings.from_env(), open_browser=not args.no_browser))
