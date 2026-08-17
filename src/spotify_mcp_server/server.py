"""MCP v2 construction, registration, and transport startup."""

from __future__ import annotations

import os
from collections.abc import Awaitable, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any

from mcp.server.auth.provider import TokenVerifier
from mcp.server.auth.settings import AuthSettings
from mcp.server.context import ServerMiddleware
from mcp.server.mcpserver import Context, MCPServer
from mcp.types import ToolAnnotations

from spotify_mcp_server.prompts import register_prompts
from spotify_mcp_server.resources import register_resources
from spotify_mcp_server.server_auth import (
    AuthenticatedSubjectMiddleware,
    HostedSettings,
    ScalekitTokenVerifier,
    current_authenticated_subject,
)
from spotify_mcp_server.spotify.auth import AuthenticationError, SpotifyTokenProvider
from spotify_mcp_server.spotify.client import SpotifyClient
from spotify_mcp_server.spotify.config import Settings
from spotify_mcp_server.spotify.hosted import (
    HostedSpotifyServices,
    SpotifyConnectionRequired,
    SpotifyUserNotAllowed,
    register_hosted_routes,
)
from spotify_mcp_server.tools.common import ContractWarning, ToolResponse
from spotify_mcp_server.tools.models import (
    GetItemInput,
    LibraryModifyInput,
    LibraryReadInput,
    ListeningActivityInput,
    PlayerControlInput,
    PlayerStatusInput,
    PlaylistModifyInput,
    PlaylistReadInput,
    SearchCatalogInput,
)
from spotify_mcp_server.tools.service import SpotifyService

READ_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=True,
    destructiveHint=False,
    idempotentHint=True,
    openWorldHint=True,
)
PLAYER_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=False,
    idempotentHint=False,
    openWorldHint=True,
)
PLAYLIST_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=False,
    openWorldHint=True,
)
LIBRARY_WRITE_ANNOTATIONS = ToolAnnotations(
    readOnlyHint=False,
    destructiveHint=True,
    idempotentHint=True,
    openWorldHint=True,
)

ServiceResolver = Callable[[Context], Awaitable[SpotifyService]]
ResourceClientResolver = Callable[[], Awaitable[SpotifyClient]]
ServerLifespan = Callable[[MCPServer], AbstractAsyncContextManager[Any]]


def build_service(settings: Settings | None = None) -> SpotifyService:
    """Build the production service without initiating OAuth or a Spotify request."""

    resolved = settings or Settings.from_env()
    provider = SpotifyTokenProvider(resolved)
    client = SpotifyClient(
        provider, base_url=resolved.api_base_url, max_retries=resolved.max_retries
    )
    return SpotifyService(client)


def create_server(
    service: SpotifyService | None = None,
    *,
    resolve_service: ServiceResolver | None = None,
    resolve_resource_client: ResourceClientResolver | None = None,
    token_verifier: TokenVerifier | None = None,
    auth: AuthSettings | None = None,
    lifespan: ServerLifespan | None = None,
    middleware: Sequence[ServerMiddleware[Any]] | None = None,
) -> MCPServer:
    """Create the server with an injectable service for account-free protocol tests."""

    if service is not None and resolve_service is not None:
        raise ValueError("Provide either a static service or a request service resolver")
    spotify = service or (None if resolve_service is not None else build_service())

    async def resolve(_: Context) -> SpotifyService:
        assert spotify is not None
        return spotify

    service_for = resolve_service or resolve
    server = MCPServer(
        "Spotify MCP Server",
        version="0.1.0",
        instructions=(
            "Use these bundled Spotify tools to minimize conversational turns. Prefer parallel "
            "read bundles. Treat player, playlist, and library modifications as user-visible "
            "writes; preserve caller order and inspect structured warnings for partial success."
        ),
        token_verifier=token_verifier,
        auth=auth,
        lifespan=lifespan,
        middleware=middleware,
    )

    @server.tool(
        name="search_catalog",
        title="Search Spotify catalog",
        description=(
            "Search one or more Spotify catalog types in parallel. Returns native type-grouped "
            "Spotify paging objects, bounded by Spotify's current limit of 10 results per page."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )
    async def search_catalog(request: SearchCatalogInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "search_catalog", request)

    @server.tool(
        name="get_item",
        title="Get Spotify items",
        description=(
            "Fetch 1-25 heterogeneous Spotify items by ID, URI, or web URL. Optionally include "
            "natural child collections such as album tracks or show episodes in the same call."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )
    async def get_item(request: GetItemInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "get_item", request)

    @server.tool(
        name="player_status",
        title="Get Spotify player status",
        description=(
            "Fetch any combination of current playback, available devices, and the queue in "
            "parallel. Returns null current playback naturally when nothing is active."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )
    async def player_status(request: PlayerStatusInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "player_status", request)

    @server.tool(
        name="player_control",
        title="Control Spotify playback",
        description=(
            "Execute 1-20 explicitly ordered playback actions in one call, including transfer, "
            "play, pause, navigation, seek, repeat, volume, shuffle, and queue operations."
        ),
        annotations=PLAYER_WRITE_ANNOTATIONS,
        structured_output=True,
    )
    async def player_control(request: PlayerControlInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "player_control", request)

    @server.tool(
        name="playlist_read",
        title="Read Spotify playlists",
        description=(
            "List the current user's playlists or fetch up to 20 referenced playlists. Playlist "
            "item contents are optional and follow Spotify's owner/collaborator access rule."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )
    async def playlist_read(request: PlaylistReadInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "playlist_read", request)

    @server.tool(
        name="playlist_modify",
        title="Modify Spotify playlists",
        description=(
            "Execute 1-20 ordered playlist creates or mutations. Later actions may reference a "
            "playlist created earlier in the same call; item actions mirror Spotify's 100-URI cap."
        ),
        annotations=PLAYLIST_WRITE_ANNOTATIONS,
        structured_output=True,
    )
    async def playlist_modify(request: PlaylistModifyInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "playlist_modify", request)

    @server.tool(
        name="library_read",
        title="Read Spotify library",
        description=(
            "Bundle saved tracks, albums, shows, episodes, or audiobooks; followed artists; and "
            "heterogeneous library membership checks of up to 40 Spotify URIs."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )
    async def library_read(request: LibraryReadInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "library_read", request)

    @server.tool(
        name="library_modify",
        title="Modify Spotify library",
        description=(
            "Run 1-20 ordered save, remove, follow, or unfollow actions through Spotify's generic "
            "library endpoint. Each idempotent action accepts up to 40 typed Spotify references."
        ),
        annotations=LIBRARY_WRITE_ANNOTATIONS,
        structured_output=True,
    )
    async def library_modify(request: LibraryModifyInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "library_modify", request)

    @server.tool(
        name="listening_activity",
        title="Read Spotify listening activity",
        description=(
            "Fetch recently played tracks and top tracks or artists across short-, medium-, and "
            "long-term ranges. Spotify does not expose podcast listening history here."
        ),
        annotations=READ_ANNOTATIONS,
        structured_output=True,
    )
    async def listening_activity(request: ListeningActivityInput, ctx: Context) -> ToolResponse:
        return await _dispatch(service_for, ctx, "listening_activity", request)

    register_prompts(server)

    async def static_client() -> SpotifyClient:
        assert spotify is not None
        return spotify.client

    client_for_resource = resolve_resource_client or static_client
    register_resources(server, client_for_resource)

    return server


def create_hosted_server(settings: HostedSettings) -> MCPServer:
    """Create the public server with Scalekit identity and per-user Spotify services."""

    services = HostedSpotifyServices(settings)

    async def resolve_service(ctx: Context) -> SpotifyService:
        return await services.service_for(_authenticated_subject(ctx))

    async def resolve_resource_client() -> SpotifyClient:
        return (await services.service_for(current_authenticated_subject())).client

    server = create_server(
        resolve_service=resolve_service,
        resolve_resource_client=resolve_resource_client,
        token_verifier=ScalekitTokenVerifier(settings),
        auth=settings.auth_settings(),
        lifespan=services.lifespan,
        middleware=[AuthenticatedSubjectMiddleware()],
    )
    register_hosted_routes(server, services)
    return server


async def _dispatch(
    resolve_service: ServiceResolver,
    ctx: Context,
    method: str,
    request: object,
) -> ToolResponse:
    try:
        service = await resolve_service(ctx)
        handler = getattr(service, method)
        return await handler(request)
    except AuthenticationError as exc:
        if isinstance(exc, SpotifyConnectionRequired):
            code = "spotify_authorization_required"
        elif isinstance(exc, SpotifyUserNotAllowed):
            code = "spotify_user_not_allowed"
        else:
            code = "spotify_authentication_failed"
        return ToolResponse(
            status="error",
            warnings=[ContractWarning(code=code, message=str(exc))],
        )


def _authenticated_subject(ctx: Context) -> str:
    request = ctx.request_context.request
    user = getattr(request, "user", None)
    access_token = getattr(user, "access_token", None)
    subject = getattr(access_token, "subject", None)
    if not isinstance(subject, str) or not subject:
        raise AuthenticationError("Authenticated Scalekit user subject is unavailable")
    return subject


mcp = create_server()


def main() -> None:
    """Run local loopback mode or the explicitly configured authenticated hosted mode."""

    if os.environ.get("MCP_DEPLOYMENT_MODE") == "hosted":
        hosted = HostedSettings.from_env()
        runtime = create_hosted_server(hosted)
        runtime.run(
            transport="streamable-http",
            host=hosted.host,
            port=hosted.port,
            stateless_http=True,
            json_response=True,
            transport_security=hosted.transport_security(),
        )
        return

    settings = Settings.from_env()
    if settings.host not in {"127.0.0.1", "localhost", "::1"}:
        raise ValueError("MCP_HOST must be a loopback address")
    runtime = create_server(build_service(settings))
    runtime.run(
        transport="streamable-http",
        host=settings.host,
        port=settings.port,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
