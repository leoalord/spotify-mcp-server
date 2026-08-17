"""Scalekit authentication and hosted runtime configuration."""

from __future__ import annotations

import asyncio
import contextvars
import logging
import os
import re
from dataclasses import dataclass, replace
from typing import Any, Protocol
from urllib.parse import urlparse

from mcp.server.auth.provider import AccessToken
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import CallNext, HandlerResult, ServerRequestContext
from mcp.server.transport_security import TransportSecuritySettings

from spotify_mcp_server.spotify.config import Settings

logger = logging.getLogger(__name__)
_subject_context: contextvars.ContextVar[str | None] = contextvars.ContextVar(
    "scalekit_subject", default=None
)


class ScalekitClaimsClient(Protocol):
    def validate_access_token_and_get_claims(self, token: str, options: Any) -> dict[str, Any]: ...


@dataclass(frozen=True, slots=True)
class HostedSettings:
    """Required environment for the authenticated public deployment."""

    scalekit_environment_url: str
    scalekit_client_id: str
    scalekit_client_secret: str
    scalekit_resource_id: str
    mcp_server_url: str
    database_url: str
    token_encryption_key: str
    spotify: Settings
    allowed_subjects: frozenset[str]
    host: str = "0.0.0.0"
    port: int = 7860

    @classmethod
    def from_env(cls) -> HostedSettings:
        environment_url = _required("SCALEKIT_ENVIRONMENT_URL").rstrip("/")
        resource_id = _required("SCALEKIT_RESOURCE_ID")
        server_url = _required("MCP_SERVER_URL").rstrip("/")
        _validate_hosted_urls(environment_url, server_url, resource_id)

        parsed_server = urlparse(server_url)
        callback_url = f"{parsed_server.scheme}://{parsed_server.netloc}/spotify/callback"
        spotify = replace(
            Settings.from_env(),
            client_id=_required("SPOTIFY_CLIENT_ID"),
            redirect_uri=callback_url,
        )
        allowed = frozenset(
            value.strip()
            for value in os.environ.get("MCP_ALLOWED_SUBJECTS", "").split(",")
            if value.strip()
        )
        return cls(
            scalekit_environment_url=environment_url,
            scalekit_client_id=_required("SCALEKIT_CLIENT_ID"),
            scalekit_client_secret=_required("SCALEKIT_CLIENT_SECRET"),
            scalekit_resource_id=resource_id,
            mcp_server_url=server_url,
            database_url=_required("DATABASE_URL"),
            token_encryption_key=_required("TOKEN_ENCRYPTION_KEY"),
            spotify=spotify,
            allowed_subjects=allowed,
            port=int(os.environ.get("MCP_PORT", os.environ.get("PORT", "7860"))),
        )

    @property
    def authorization_server_url(self) -> str:
        return f"{self.scalekit_environment_url}/resources/{self.scalekit_resource_id}"

    @property
    def public_origin(self) -> str:
        parsed = urlparse(self.mcp_server_url)
        return f"{parsed.scheme}://{parsed.netloc}"

    def auth_settings(self) -> AuthSettings:
        return AuthSettings(
            issuer_url=self.authorization_server_url,
            resource_server_url=self.mcp_server_url,
            required_scopes=[],
        )

    def transport_security(self) -> TransportSecuritySettings:
        hostname = urlparse(self.mcp_server_url).hostname
        assert hostname is not None
        return TransportSecuritySettings(
            enable_dns_rebinding_protection=True,
            allowed_hosts=[hostname, f"{hostname}:*", "127.0.0.1:*", "localhost:*"],
            allowed_origins=[self.public_origin],
        )


class ScalekitTokenVerifier:
    """Validate Scalekit JWTs and expose their stable user subject to MCP handlers."""

    def __init__(
        self,
        settings: HostedSettings,
        *,
        client: ScalekitClaimsClient | None = None,
    ) -> None:
        self.settings = settings
        if client is None:
            from scalekit import ScalekitClient
            from scalekit.common.scalekit import TokenValidationOptions

            self.client = ScalekitClient(
                env_url=settings.scalekit_environment_url,
                client_id=settings.scalekit_client_id,
                client_secret=settings.scalekit_client_secret,
            )
            self.options = TokenValidationOptions(
                issuer=settings.scalekit_environment_url,
                audience=[settings.mcp_server_url],
            )
        else:
            self.client = client
            self.options = _ValidationOptions(
                issuer=settings.scalekit_environment_url,
                audience=[settings.mcp_server_url],
            )

    async def verify_token(self, token: str) -> AccessToken | None:
        try:
            claims = await asyncio.to_thread(
                self.client.validate_access_token_and_get_claims,
                token,
                self.options,
            )
            subject = claims.get("sub")
            if not isinstance(subject, str) or not subject:
                return None
            return AccessToken(
                token=token,
                client_id=_client_id(claims),
                scopes=_scopes(claims),
                expires_at=_integer_claim(claims, "exp"),
                resource=self.settings.mcp_server_url,
                subject=subject,
                claims=claims,
            )
        except Exception:
            logger.warning("Scalekit rejected an MCP bearer token")
            return None


class AuthenticatedSubjectMiddleware:
    """Make the authenticated subject available to static resource handlers."""

    async def __call__(
        self,
        ctx: ServerRequestContext[Any, Any],
        call_next: CallNext,
    ) -> HandlerResult:
        request = ctx.request
        user = getattr(request, "user", None)
        access_token = getattr(user, "access_token", None)
        subject = getattr(access_token, "subject", None)
        token = _subject_context.set(subject if isinstance(subject, str) else None)
        try:
            return await call_next(ctx)
        finally:
            _subject_context.reset(token)


def current_authenticated_subject() -> str:
    subject = _subject_context.get()
    if not subject:
        raise RuntimeError("Authenticated Scalekit user subject is unavailable")
    return subject


@dataclass(frozen=True, slots=True)
class _ValidationOptions:
    issuer: str
    audience: list[str]
    required_scopes: list[str] | None = None


def _required(name: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise ValueError(f"{name} is required in hosted mode")
    return value


def _validate_hosted_urls(environment_url: str, server_url: str, resource_id: str) -> None:
    for name, value in (
        ("SCALEKIT_ENVIRONMENT_URL", environment_url),
        ("MCP_SERVER_URL", server_url),
    ):
        parsed = urlparse(value)
        if parsed.scheme != "https" or not parsed.hostname or parsed.query or parsed.fragment:
            raise ValueError(f"{name} must be a clean HTTPS URL")
    if urlparse(server_url).path != "/mcp":
        raise ValueError("MCP_SERVER_URL must end with /mcp")
    if not re.fullmatch(r"res_[A-Za-z0-9]+", resource_id):
        raise ValueError("SCALEKIT_RESOURCE_ID is invalid")


def _client_id(claims: dict[str, Any]) -> str:
    for name in ("client_id", "azp"):
        value = claims.get(name)
        if isinstance(value, str) and value:
            return value
    return "scalekit-mcp-client"


def _scopes(claims: dict[str, Any]) -> list[str]:
    scopes = claims.get("scopes")
    if isinstance(scopes, list):
        return [scope for scope in scopes if isinstance(scope, str) and scope]
    scope = claims.get("scope")
    if isinstance(scope, str):
        return [value for value in scope.split() if value]
    return []


def _integer_claim(claims: dict[str, Any], name: str) -> int | None:
    value = claims.get(name)
    return value if isinstance(value, int) else None
