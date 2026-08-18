"""Hosted Scalekit, Neon credential, and Spotify OAuth behavior."""

from __future__ import annotations

import base64
import json
from types import SimpleNamespace
from typing import Any
from urllib.parse import parse_qs, urlparse

import httpx2
import jwt
import pytest
from cryptography.fernet import Fernet
from cryptography.hazmat.primitives.asymmetric import rsa
from mcp.server.auth.provider import AccessToken

from spotify_mcp_server.server import create_server
from spotify_mcp_server.server_auth import (
    HostedSettings,
    ScalekitTokenVerifier,
    _ScalekitJWTClaimsClient,
    _ThrottledJWKSClient,
    _ValidationOptions,
)
from spotify_mcp_server.spotify.auth import AuthenticationError
from spotify_mcp_server.spotify.config import SCOPES, Settings
from spotify_mcp_server.spotify.hosted import (
    EncryptedRefreshTokenStore,
    HostedSpotifyServices,
    SecretCipher,
    SpotifyConnectionRequired,
    SpotifyUserNotAllowed,
    _asyncpg_dsn,
)

pytestmark = pytest.mark.anyio


class MemoryRepository:
    def __init__(self) -> None:
        self.values: dict[str, bytes] = {}
        self.initialized = False

    async def initialize(self) -> None:
        self.initialized = True

    async def close(self) -> None:
        self.initialized = False

    async def load(self, subject: str) -> bytes | None:
        return self.values.get(subject)

    async def save(self, subject: str, ciphertext: bytes) -> None:
        self.values[subject] = ciphertext


def hosted_settings(*, allowed_subjects: frozenset[str] = frozenset()) -> HostedSettings:
    return HostedSettings(
        scalekit_environment_url="https://tenant.scalekit.dev",
        scalekit_resource_id="res_123",
        mcp_server_url="https://spotify.example/mcp",
        database_url="postgresql://user:pass@db.example/spotify?sslmode=require",
        token_encryption_key=Fernet.generate_key().decode("ascii"),
        spotify=Settings(
            client_id="spotify-client",
            redirect_uri="https://spotify.example/spotify/callback",
            accounts_base_url="https://accounts.spotify.test",
        ),
        allowed_subjects=allowed_subjects,
    )


def test_hosted_settings_require_complete_https_configuration(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    values = {
        "SCALEKIT_ENVIRONMENT_URL": "https://tenant.scalekit.dev",
        "SCALEKIT_RESOURCE_ID": "res_123",
        "MCP_SERVER_URL": "https://spotify.example/mcp",
        "DATABASE_URL": "postgresql://database",
        "TOKEN_ENCRYPTION_KEY": Fernet.generate_key().decode("ascii"),
        "SPOTIFY_CLIENT_ID": "spotify-client",
    }
    for name, value in values.items():
        monkeypatch.setenv(name, value)

    resolved = HostedSettings.from_env()
    assert resolved.spotify.redirect_uri == "https://spotify.example/spotify/callback"
    assert resolved.authorization_server_url == "https://tenant.scalekit.dev/resources/res_123"

    monkeypatch.setenv("MCP_SERVER_URL", "http://spotify.example/mcp")
    with pytest.raises(ValueError, match="HTTPS"):
        HostedSettings.from_env()


def test_scalekit_jwt_client_validates_signature_issuer_and_audience() -> None:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    token = jwt.encode(
        {
            "sub": "usr_123",
            "iss": "https://tenant.scalekit.dev",
            "aud": "https://spotify.example/mcp",
            "exp": 2_000_000_000,
        },
        private_key,
        algorithm="RS256",
        headers={"kid": "key-1"},
    )

    class SigningKeyClient:
        def get_signing_key_from_jwt(self, encoded: str) -> SimpleNamespace:
            assert encoded == token
            return SimpleNamespace(key=private_key.public_key())

    client = _ScalekitJWTClaimsClient(
        "https://tenant.scalekit.dev",
        jwks_client=SigningKeyClient(),
    )
    claims = client.validate_access_token_and_get_claims(
        token,
        _ValidationOptions(
            issuer="https://tenant.scalekit.dev",
            audience=["https://spotify.example/mcp"],
        ),
    )

    assert claims["sub"] == "usr_123"
    with pytest.raises(jwt.InvalidAudienceError):
        client.validate_access_token_and_get_claims(
            token,
            _ValidationOptions(
                issuer="https://tenant.scalekit.dev",
                audience=["https://other.example/mcp"],
            ),
        )


class RecordingJWKSClient:
    """Stands in for PyJWKClient, counting the fetches a caller actually provokes."""

    def __init__(self, kid: str = "key-1") -> None:
        self.key = SimpleNamespace(key_id=kid)
        self.fetches = 0
        self.refreshes = 0

    def get_signing_keys(self, refresh: bool = False) -> list[Any]:
        self.fetches += 1
        if refresh:
            self.refreshes += 1
        return [self.key]

    @staticmethod
    def match_kid(signing_keys: list[Any], kid: str) -> Any | None:
        return next((key for key in signing_keys if key.key_id == kid), None)


def _unsigned_token(headers: dict[str, Any]) -> str:
    """Build a token for its header alone; key resolution never inspects the signature."""

    def segment(payload: dict[str, Any]) -> str:
        raw = json.dumps(payload, separators=(",", ":")).encode()
        return base64.urlsafe_b64encode(raw).rstrip(b"=").decode()

    return f"{segment(headers)}.{segment({'sub': 'usr_123'})}.not-a-signature"


def test_forged_key_ids_cannot_drive_repeated_jwks_refreshes() -> None:
    inner = RecordingJWKSClient()
    clock = SimpleNamespace(value=1_000.0)
    client = _ThrottledJWKSClient(
        "https://tenant.scalekit.dev/keys",
        client=inner,
        min_refresh_interval=30.0,
        now=lambda: clock.value,
    )

    for index in range(5):
        with pytest.raises(jwt.PyJWKClientError):
            client.get_signing_key_from_jwt(
                _unsigned_token({"alg": "RS256", "kid": f"forged-{index}"})
            )

    # Five unauthenticated requests, one refresh: the rest are refused by the throttle.
    assert inner.refreshes == 1

    # Once the interval elapses a genuine rotation is still picked up.
    clock.value += 31.0
    with pytest.raises(jwt.PyJWKClientError):
        client.get_signing_key_from_jwt(_unsigned_token({"alg": "RS256", "kid": "forged-late"}))
    assert inner.refreshes == 2


def test_known_key_id_resolves_without_refreshing() -> None:
    inner = RecordingJWKSClient()
    client = _ThrottledJWKSClient("https://tenant.scalekit.dev/keys", client=inner)

    key = client.get_signing_key_from_jwt(_unsigned_token({"alg": "RS256", "kid": "key-1"}))

    assert key is inner.key
    assert inner.refreshes == 0


def test_disallowed_algorithm_is_rejected_before_any_key_fetch() -> None:
    inner = RecordingJWKSClient()
    client = _ThrottledJWKSClient("https://tenant.scalekit.dev/keys", client=inner)

    with pytest.raises(jwt.InvalidAlgorithmError):
        client.get_signing_key_from_jwt(_unsigned_token({"alg": "HS256", "kid": "key-1"}))
    with pytest.raises(jwt.PyJWKClientError):
        client.get_signing_key_from_jwt(_unsigned_token({"alg": "RS256"}))

    assert inner.fetches == 0


async def test_scalekit_verifier_validates_audience_and_exposes_subject() -> None:
    class ClaimsClient:
        def validate_access_token_and_get_claims(self, token: str, options: Any) -> dict[str, Any]:
            assert token == "signed"
            assert options.issuer == "https://tenant.scalekit.dev"
            assert options.audience == ["https://spotify.example/mcp"]
            return {
                "sub": "usr_123",
                "client_id": "https://client.example/metadata.json",
                "iss": "https://tenant.scalekit.dev",
                "exp": 2_000_000_000,
                "scopes": ["spotify:use"],
            }

    verifier = ScalekitTokenVerifier(hosted_settings(), client=ClaimsClient())
    verified = await verifier.verify_token("signed")

    assert isinstance(verified, AccessToken)
    assert verified.subject == "usr_123"
    assert verified.client_id == "https://client.example/metadata.json"
    assert verified.scopes == ["spotify:use"]


async def test_scalekit_verifier_rejects_invalid_tokens_without_exposing_details() -> None:
    class RejectingClient:
        def validate_access_token_and_get_claims(self, token: str, options: Any) -> dict[str, Any]:
            raise RuntimeError(f"sensitive failure for {token} and {options}")

    verifier = ScalekitTokenVerifier(hosted_settings(), client=RejectingClient())
    assert await verifier.verify_token("do-not-log") is None


async def test_refresh_token_is_encrypted_before_repository_storage() -> None:
    repository = MemoryRepository()
    cipher = SecretCipher(Fernet.generate_key().decode("ascii"))
    store = EncryptedRefreshTokenStore(repository, cipher, "usr_123")

    await store.save("spotify-refresh-token")

    assert b"spotify-refresh-token" not in repository.values["usr_123"]
    assert await store.load() == "spotify-refresh-token"

    repository.values["usr_other"] = repository.values["usr_123"]
    swapped = EncryptedRefreshTokenStore(repository, cipher, "usr_other")
    with pytest.raises(AuthenticationError, match="could not be decrypted"):
        await swapped.load()


async def test_hosted_spotify_oauth_uses_pkce_and_persists_only_refresh_token() -> None:
    repository = MemoryRepository()
    settings = hosted_settings()

    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert str(request.url) == "https://accounts.spotify.test/api/token"
        assert b"grant_type=authorization_code" in request.content
        assert b"code_verifier=" in request.content
        return httpx2.Response(
            200,
            json={
                "access_token": "memory-only-access",
                "refresh_token": "persisted-refresh",
                "expires_in": 3600,
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        services = HostedSpotifyServices(
            settings,
            repository=repository,
            http_client=http,
        )
        connect_url = services.spotify_connect_url("usr_123")
        sealed_ticket = parse_qs(urlparse(connect_url).query)["ticket"][0]
        authorization_url = services.spotify_authorization_url(sealed_ticket)
        authorization_query = parse_qs(urlparse(authorization_url).query)

        assert authorization_query["state"] == [sealed_ticket]
        assert authorization_query["code_challenge_method"] == ["S256"]
        assert authorization_query["scope"][0].split() == list(SCOPES)

        await services.complete_spotify_authorization("spotify-code", sealed_ticket)

    ciphertext = repository.values["usr_123"]
    assert b"persisted-refresh" not in ciphertext
    assert b"memory-only-access" not in ciphertext
    store = EncryptedRefreshTokenStore(repository, services.cipher, "usr_123")
    assert await store.load() == "persisted-refresh"


async def test_hosted_service_requires_connection_and_honors_subject_allowlist() -> None:
    repository = MemoryRepository()
    services = HostedSpotifyServices(
        hosted_settings(allowed_subjects=frozenset({"usr_allowed"})),
        repository=repository,
        http_client=httpx2.AsyncClient(),
    )
    try:
        with pytest.raises(SpotifyUserNotAllowed):
            await services.service_for("usr_denied")
        with pytest.raises(SpotifyConnectionRequired, match="/spotify/connect"):
            await services.service_for("usr_allowed")
    finally:
        await services.http.aclose()


async def test_protected_resource_metadata_and_bearer_challenge() -> None:
    class NeverCalledVerifier:
        async def verify_token(self, token: str) -> AccessToken | None:
            raise AssertionError(f"unexpected token: {token}")

    settings = hosted_settings()
    server = create_server(
        service=object(),  # type: ignore[arg-type]
        token_verifier=NeverCalledVerifier(),
        auth=settings.auth_settings(),
    )
    app = server.streamable_http_app(
        stateless_http=True, json_response=True, host="spotify.example"
    )

    async with httpx2.AsyncClient(
        transport=httpx2.ASGITransport(app=app),
        base_url="https://spotify.example",
    ) as client:
        metadata = await client.get("/.well-known/oauth-protected-resource/mcp")
        challenge = await client.post("/mcp", json={})

    assert metadata.status_code == 200
    assert metadata.json()["resource"] == "https://spotify.example/mcp"
    assert metadata.json()["authorization_servers"] == [
        "https://tenant.scalekit.dev/resources/res_123"
    ]
    assert challenge.status_code == 401
    assert (
        'resource_metadata="https://spotify.example/.well-known/oauth-protected-resource/mcp"'
        in challenge.headers["www-authenticate"]
    )


def test_neon_dsn_removes_libpq_only_channel_binding() -> None:
    source = "postgresql://user:pass@pooler.example/db?sslmode=require&channel_binding=require"
    assert _asyncpg_dsn(source) == "postgresql://user:pass@pooler.example/db?sslmode=require"


async def test_cipher_rejects_tampered_spotify_ticket() -> None:
    http = httpx2.AsyncClient()
    services = HostedSpotifyServices(
        hosted_settings(),
        repository=MemoryRepository(),
        http_client=http,
    )
    try:
        with pytest.raises(AuthenticationError, match="invalid or expired"):
            services.spotify_authorization_url("tampered")
    finally:
        await http.aclose()
