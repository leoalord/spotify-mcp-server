from __future__ import annotations

import socket
import threading
from urllib.error import HTTPError
from urllib.request import urlopen

import httpx2
import pytest

import spotify_mcp_server.spotify.auth as auth_module
from spotify_mcp_server.spotify.auth import (
    AuthenticationError,
    KeyringRefreshTokenStore,
    SpotifyTokenProvider,
    _pkce_pair,
    authorize,
)
from spotify_mcp_server.spotify.config import Settings

pytestmark = pytest.mark.anyio


def settings() -> Settings:
    return Settings(
        client_id="client",
        redirect_uri="http://127.0.0.1:8765/callback",
        accounts_base_url="https://accounts.test",
    )


class MemoryStore:
    def __init__(self, refresh_token: str | None = None) -> None:
        self.refresh_token = refresh_token
        self.saved: list[str] = []
        self.load_thread: int | None = None
        self.save_threads: list[int] = []

    def load(self) -> str | None:
        self.load_thread = threading.get_ident()
        return self.refresh_token

    def save(self, refresh_token: str) -> None:
        self.save_threads.append(threading.get_ident())
        self.refresh_token = refresh_token
        self.saved.append(refresh_token)


class FakeKeyring:
    def __init__(self) -> None:
        self.values: dict[tuple[str, str], str] = {}

    def get_password(self, service_name: str, username: str) -> str | None:
        return self.values.get((service_name, username))

    def set_password(self, service_name: str, username: str, password: str) -> None:
        self.values[(service_name, username)] = password


def test_keyring_store_round_trip_contains_only_refresh_token() -> None:
    backend = FakeKeyring()
    store = KeyringRefreshTokenStore("spotify-mcp", "client", backend=backend)
    assert store.load() is None
    store.save("refresh")
    assert store.load() == "refresh"
    assert backend.values == {("spotify-mcp", "spotify-client:client"): "refresh"}


def test_keyring_store_normalizes_backend_failures() -> None:
    class BrokenKeyring(FakeKeyring):
        def get_password(self, service_name: str, username: str) -> str | None:
            raise RuntimeError("backend details")

        def set_password(self, service_name: str, username: str, password: str) -> None:
            raise RuntimeError("backend details")

    store = KeyringRefreshTokenStore("spotify-mcp", "client", backend=BrokenKeyring())
    with pytest.raises(AuthenticationError, match="read"):
        store.load()
    with pytest.raises(AuthenticationError, match="save"):
        store.save("secret")


async def test_provider_loads_refresh_token_but_keeps_access_token_in_memory() -> None:
    store = MemoryStore("keep")
    calls = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal calls
        calls += 1
        assert request.url.path == "/api/token"
        assert b"refresh_token=keep" in request.content
        return httpx2.Response(
            200, json={"access_token": "access", "expires_in": 3600, "scope": "scope"}
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        provider = SpotifyTokenProvider(settings(), client=http, store=store)
        assert await provider.access_token() == "access"
        assert await provider.access_token() == "access"

    assert calls == 1
    assert store.saved == []
    assert store.refresh_token == "keep"
    assert store.load_thread != threading.get_ident()


async def test_provider_persists_only_rotated_refresh_token() -> None:
    store = MemoryStore("old")

    async def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            200,
            json={
                "access_token": "new-access",
                "refresh_token": "rotated",
                "expires_in": 3600,
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        provider = SpotifyTokenProvider(settings(), client=http, store=store)
        assert await provider.access_token(force_refresh=True) == "new-access"

    assert store.saved == ["rotated"]
    assert "new-access" not in store.saved
    assert store.save_threads[0] != threading.get_ident()


async def test_provider_requires_prior_authorization() -> None:
    async with httpx2.AsyncClient() as http:
        provider = SpotifyTokenProvider(settings(), client=http, store=MemoryStore())
        with pytest.raises(AuthenticationError, match="spotify-mcp-auth"):
            await provider.access_token()


async def test_provider_normalizes_refresh_failure() -> None:
    async def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(400, json={"error": "invalid_grant"})

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        provider = SpotifyTokenProvider(settings(), client=http, store=MemoryStore("expired"))
        with pytest.raises(AuthenticationError, match="refresh failed"):
            await provider.access_token()


async def test_authorize_persists_refresh_token_only(monkeypatch: pytest.MonkeyPatch) -> None:
    store = MemoryStore()

    class FakeClient:
        async def __aenter__(self) -> FakeClient:
            return self

        async def __aexit__(self, *args: object) -> None:
            return None

        async def post(self, url: str, *, data: dict[str, str]) -> httpx2.Response:
            assert url == "https://accounts.test/api/token"
            assert data["grant_type"] == "authorization_code"
            assert data["code"] == "callback-code"
            return httpx2.Response(
                200,
                json={
                    "access_token": "must-not-persist",
                    "refresh_token": "persist-me",
                    "expires_in": 3600,
                },
            )

    monkeypatch.setattr(auth_module, "_receive_callback", lambda *_: "callback-code")
    monkeypatch.setattr(auth_module.httpx2, "AsyncClient", lambda **_: FakeClient())
    await authorize(settings(), open_browser=False, store=store)
    assert store.saved == ["persist-me"]
    assert "must-not-persist" not in store.saved


async def test_authorize_requires_client_id() -> None:
    config = settings()
    config = Settings(client_id="", redirect_uri=config.redirect_uri)
    with pytest.raises(AuthenticationError, match="SPOTIFY_CLIENT_ID"):
        await authorize(config, open_browser=False, store=MemoryStore())


def test_pkce_pair_is_url_safe() -> None:
    verifier, challenge = _pkce_pair()
    assert len(verifier) >= 43
    assert "=" not in challenge
    assert verifier != challenge


def _callback_target(monkeypatch: pytest.MonkeyPatch) -> tuple[str, threading.Event]:
    with socket.socket() as available:
        available.bind(("127.0.0.1", 0))
        port = available.getsockname()[1]

    ready = threading.Event()
    original_server = auth_module.HTTPServer

    def make_server(*args: object, **kwargs: object):
        server = original_server(*args, **kwargs)
        ready.set()
        return server

    monkeypatch.setattr(auth_module, "HTTPServer", make_server)
    return f"http://127.0.0.1:{port}/callback", ready


def test_receive_callback_rejects_non_loopback_redirect() -> None:
    with pytest.raises(AuthenticationError, match="loopback"):
        auth_module._receive_callback("https://example.com/callback", "state")


def test_callback_handler_rejects_mismatched_state(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect, ready = _callback_target(monkeypatch)
    outcome: list[str | Exception] = []

    def receive() -> None:
        try:
            outcome.append(auth_module._receive_callback(redirect, "expected"))
        except Exception as exc:
            outcome.append(exc)

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    with pytest.raises(HTTPError) as raised:
        urlopen(f"{redirect}?code=abc&state=forged", timeout=2)
    assert raised.value.code == 400
    assert b"authorization failed" in raised.value.read()
    thread.join(timeout=2)

    assert not thread.is_alive()
    assert isinstance(outcome[0], AuthenticationError)
    assert "state" in str(outcome[0])


def test_callback_handler_ignores_unrelated_path(monkeypatch: pytest.MonkeyPatch) -> None:
    redirect, ready = _callback_target(monkeypatch)
    outcome: list[str | Exception] = []

    def receive() -> None:
        try:
            outcome.append(auth_module._receive_callback(redirect, "expected"))
        except Exception as exc:
            outcome.append(exc)

    thread = threading.Thread(target=receive, daemon=True)
    thread.start()
    assert ready.wait(timeout=2)
    with pytest.raises(HTTPError) as raised:
        urlopen(redirect.replace("/callback", "/favicon.ico"), timeout=2)
    assert raised.value.code == 404
    body = urlopen(f"{redirect}?code=abc&state=expected", timeout=2).read()
    thread.join(timeout=2)

    assert b"authorization received" in body
    assert not thread.is_alive()
    assert outcome == ["abc"]
