"""Hosted Spotify OAuth and encrypted per-user refresh-token storage."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
import os
import secrets
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from dataclasses import dataclass
from typing import Any, Protocol
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

import asyncpg
import httpx2
from cryptography.exceptions import InvalidTag
from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from mcp.server.mcpserver import MCPServer
from starlette.requests import Request
from starlette.responses import JSONResponse, PlainTextResponse, RedirectResponse, Response

from spotify_mcp_server.server_auth import HostedSettings
from spotify_mcp_server.spotify.auth import (
    AuthenticationError,
    RefreshTokenStore,
    SpotifyTokenProvider,
)
from spotify_mcp_server.spotify.client import SpotifyClient
from spotify_mcp_server.spotify.config import SCOPES
from spotify_mcp_server.tools.service import SpotifyService

OAUTH_TICKET_TTL_SECONDS = 600


class EncryptedTokenRepository(Protocol):
    async def load(self, subject: str) -> bytes | None: ...

    async def save(self, subject: str, ciphertext: bytes) -> None: ...


class HostedTokenRepository(EncryptedTokenRepository, Protocol):
    async def initialize(self) -> None: ...

    async def close(self) -> None: ...


class NeonTokenRepository:
    """Minimal Neon-backed store containing ciphertext and no Spotify content."""

    def __init__(self, database_url: str) -> None:
        self.database_url = _asyncpg_dsn(database_url)
        self.pool: asyncpg.Pool | None = None

    async def initialize(self) -> None:
        self.pool = await asyncpg.create_pool(
            dsn=self.database_url,
            min_size=1,
            max_size=5,
            command_timeout=10,
            statement_cache_size=0,
        )
        await self.pool.execute(
            """
            CREATE TABLE IF NOT EXISTS spotify_credentials (
                scalekit_subject TEXT PRIMARY KEY,
                encrypted_refresh_token BYTEA NOT NULL,
                updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )

    async def close(self) -> None:
        if self.pool is not None:
            await self.pool.close()
            self.pool = None

    async def load(self, subject: str) -> bytes | None:
        pool = self._pool()
        value = await pool.fetchval(
            "SELECT encrypted_refresh_token FROM spotify_credentials WHERE scalekit_subject = $1",
            subject,
        )
        return bytes(value) if value is not None else None

    async def save(self, subject: str, ciphertext: bytes) -> None:
        pool = self._pool()
        await pool.execute(
            """
            INSERT INTO spotify_credentials (
                scalekit_subject, encrypted_refresh_token, updated_at
            ) VALUES ($1, $2, NOW())
            ON CONFLICT (scalekit_subject) DO UPDATE SET
                encrypted_refresh_token = EXCLUDED.encrypted_refresh_token,
                updated_at = NOW()
            """,
            subject,
            ciphertext,
        )

    def _pool(self) -> asyncpg.Pool:
        if self.pool is None:
            raise RuntimeError("Neon token repository has not been initialized")
        return self.pool


class SecretCipher:
    """Encrypt refresh tokens and short-lived Spotify OAuth state tickets."""

    def __init__(self, key: str) -> None:
        try:
            master_key = base64.urlsafe_b64decode(key.encode("ascii"))
            if len(master_key) != 32:
                raise ValueError("invalid key length")
        except (ValueError, UnicodeEncodeError) as exc:
            raise ValueError("TOKEN_ENCRYPTION_KEY must be a valid Fernet key") from exc

        refresh_key = _derive_key(master_key, b"spotify-mcp-refresh-token-v1")
        ticket_key = _derive_key(master_key, b"spotify-mcp-oauth-ticket-v1")
        self.refresh_cipher = AESGCM(refresh_key)
        self.ticket_cipher = Fernet(base64.urlsafe_b64encode(ticket_key))

    def encrypt_refresh_token(self, subject: str, refresh_token: str) -> bytes:
        nonce = os.urandom(12)
        ciphertext = self.refresh_cipher.encrypt(
            nonce,
            refresh_token.encode("utf-8"),
            subject.encode("utf-8"),
        )
        return b"\x01" + nonce + ciphertext

    def decrypt_refresh_token(self, subject: str, ciphertext: bytes) -> str:
        try:
            if len(ciphertext) < 30 or ciphertext[0] != 1:
                raise ValueError("unsupported ciphertext")
            return self.refresh_cipher.decrypt(
                ciphertext[1:13],
                ciphertext[13:],
                subject.encode("utf-8"),
            ).decode("utf-8")
        except (InvalidTag, UnicodeDecodeError, ValueError) as exc:
            raise AuthenticationError("Stored Spotify credential could not be decrypted") from exc

    def seal_ticket(self, ticket: OAuthTicket) -> str:
        payload = json.dumps(
            {
                "subject": ticket.subject,
                "verifier": ticket.verifier,
                "nonce": ticket.nonce,
            },
            separators=(",", ":"),
            sort_keys=True,
        ).encode("utf-8")
        return self.ticket_cipher.encrypt(payload).decode("ascii")

    def open_ticket(self, value: str) -> OAuthTicket:
        try:
            payload = json.loads(
                self.ticket_cipher.decrypt(
                    value.encode("ascii"),
                    ttl=OAUTH_TICKET_TTL_SECONDS,
                )
            )
            return OAuthTicket(
                subject=_nonempty_string(payload, "subject"),
                verifier=_nonempty_string(payload, "verifier"),
                nonce=_nonempty_string(payload, "nonce"),
            )
        except (InvalidToken, UnicodeEncodeError, UnicodeDecodeError, ValueError, TypeError) as exc:
            raise AuthenticationError("Spotify authorization link is invalid or expired") from exc


class EncryptedRefreshTokenStore(RefreshTokenStore):
    def __init__(
        self,
        repository: EncryptedTokenRepository,
        cipher: SecretCipher,
        subject: str,
    ) -> None:
        self.repository = repository
        self.cipher = cipher
        self.subject = subject

    async def load(self) -> str | None:
        ciphertext = await self.repository.load(self.subject)
        if ciphertext is None:
            return None
        return self.cipher.decrypt_refresh_token(self.subject, ciphertext)

    async def save(self, refresh_token: str) -> None:
        await self.repository.save(
            self.subject,
            self.cipher.encrypt_refresh_token(self.subject, refresh_token),
        )


@dataclass(frozen=True, slots=True)
class OAuthTicket:
    subject: str
    verifier: str
    nonce: str


class SpotifyConnectionRequired(AuthenticationError):
    def __init__(self, connect_url: str) -> None:
        self.connect_url = connect_url
        super().__init__(f"Connect Spotify to continue: {connect_url}")


class SpotifyUserNotAllowed(AuthenticationError):
    pass


class HostedSpotifyServices:
    """Resolve one memory-only Spotify client per authenticated Scalekit subject."""

    def __init__(
        self,
        settings: HostedSettings,
        *,
        repository: HostedTokenRepository | None = None,
        http_client: httpx2.AsyncClient | None = None,
    ) -> None:
        self.settings = settings
        self.repository = repository or NeonTokenRepository(settings.database_url)
        self.cipher = SecretCipher(settings.token_encryption_key)
        self.http = http_client or httpx2.AsyncClient(timeout=30)
        self._owns_http = http_client is None
        self._services: dict[str, SpotifyService] = {}
        self._service_lock = asyncio.Lock()

    @asynccontextmanager
    async def lifespan(self, _: MCPServer) -> AsyncIterator[HostedSpotifyServices]:
        await self.repository.initialize()
        try:
            yield self
        finally:
            await self.repository.close()
            if self._owns_http:
                await self.http.aclose()

    async def service_for(self, subject: str) -> SpotifyService:
        if self.settings.allowed_subjects and subject not in self.settings.allowed_subjects:
            raise SpotifyUserNotAllowed("This Scalekit user is not allowed to use this server")
        cached = self._services.get(subject)
        if cached is not None:
            return cached

        store = EncryptedRefreshTokenStore(self.repository, self.cipher, subject)
        if await store.load() is None:
            raise SpotifyConnectionRequired(self.spotify_connect_url(subject))

        async with self._service_lock:
            cached = self._services.get(subject)
            if cached is not None:
                return cached
            provider = SpotifyTokenProvider(self.settings.spotify, client=self.http, store=store)
            client = SpotifyClient(
                provider,
                base_url=self.settings.spotify.api_base_url,
                client=self.http,
                max_retries=self.settings.spotify.max_retries,
            )
            service = SpotifyService(client)
            self._services[subject] = service
            return service

    def spotify_connect_url(self, subject: str) -> str:
        ticket = OAuthTicket(
            subject=subject,
            verifier=secrets.token_urlsafe(64),
            nonce=secrets.token_urlsafe(16),
        )
        sealed = self.cipher.seal_ticket(ticket)
        return f"{self.settings.public_origin}/spotify/connect?{urlencode({'ticket': sealed})}"

    def spotify_authorization_url(self, sealed_ticket: str) -> str:
        if not self.settings.spotify.client_id:
            raise AuthenticationError("SPOTIFY_CLIENT_ID is required in hosted mode")
        ticket = self.cipher.open_ticket(sealed_ticket)
        digest = hashlib.sha256(ticket.verifier.encode("ascii")).digest()
        challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
        query = urlencode(
            {
                "client_id": self.settings.spotify.client_id,
                "response_type": "code",
                "redirect_uri": self.settings.spotify.redirect_uri,
                "scope": " ".join(SCOPES),
                "code_challenge_method": "S256",
                "code_challenge": challenge,
                "state": sealed_ticket,
            }
        )
        return f"{self.settings.spotify.accounts_base_url}/authorize?{query}"

    async def complete_spotify_authorization(self, code: str, sealed_ticket: str) -> None:
        ticket = self.cipher.open_ticket(sealed_ticket)
        response = await self.http.post(
            f"{self.settings.spotify.accounts_base_url}/api/token",
            data={
                "client_id": self.settings.spotify.client_id,
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": self.settings.spotify.redirect_uri,
                "code_verifier": ticket.verifier,
            },
        )
        if response.is_error:
            raise AuthenticationError(f"Spotify token exchange failed ({response.status_code})")
        payload = response.json()
        refresh_token = payload.get("refresh_token")
        if not isinstance(refresh_token, str) or not refresh_token:
            raise AuthenticationError("Spotify did not return a refresh token")
        store = EncryptedRefreshTokenStore(self.repository, self.cipher, ticket.subject)
        await store.save(refresh_token)


def register_hosted_routes(server: MCPServer, services: HostedSpotifyServices) -> None:
    @server.custom_route("/", methods=["GET"], include_in_schema=False)
    async def index(_: Request) -> Response:
        return JSONResponse(
            {
                "name": "Spotify MCP Server",
                "mcp_endpoint": "/mcp",
                "authentication": "OAuth 2.1 via Scalekit",
            }
        )

    @server.custom_route("/healthz", methods=["GET"], include_in_schema=False)
    async def health(_: Request) -> Response:
        return JSONResponse({"status": "ok"})

    @server.custom_route("/spotify/connect", methods=["GET"], include_in_schema=False)
    async def spotify_connect(request: Request) -> Response:
        ticket = request.query_params.get("ticket", "")
        try:
            return RedirectResponse(services.spotify_authorization_url(ticket), status_code=302)
        except AuthenticationError as exc:
            return PlainTextResponse(str(exc), status_code=400)

    @server.custom_route("/spotify/callback", methods=["GET"], include_in_schema=False)
    async def spotify_callback(request: Request) -> Response:
        error = request.query_params.get("error")
        code = request.query_params.get("code", "")
        state = request.query_params.get("state", "")
        if error:
            return PlainTextResponse("Spotify authorization was declined.", status_code=400)
        if not code or not state:
            return PlainTextResponse(
                "Spotify callback was missing required values.", status_code=400
            )
        try:
            await services.complete_spotify_authorization(code, state)
        except AuthenticationError as exc:
            return PlainTextResponse(str(exc), status_code=400)
        return PlainTextResponse(
            "Spotify is connected. Return to your MCP client and retry the request."
        )


def _asyncpg_dsn(database_url: str) -> str:
    """Remove libpq-only Neon parameters that asyncpg would send as server settings."""

    parsed = urlsplit(database_url)
    query = [(key, value) for key, value in parse_qsl(parsed.query) if key != "channel_binding"]
    return urlunsplit(
        (parsed.scheme, parsed.netloc, parsed.path, urlencode(query), parsed.fragment)
    )


def _nonempty_string(payload: Any, key: str) -> str:
    if not isinstance(payload, dict):
        raise ValueError("ticket payload is not an object")
    value = payload.get(key)
    if not isinstance(value, str) or not value:
        raise ValueError(f"ticket payload has no {key}")
    return value


def _derive_key(master_key: bytes, purpose: bytes) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=32,
        salt=None,
        info=purpose,
    ).derive(master_key)
