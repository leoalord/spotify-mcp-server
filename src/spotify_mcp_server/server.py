"""MCP v2 registration and loopback Streamable HTTP startup."""

from __future__ import annotations

from mcp.server.mcpserver import MCPServer
from mcp.types import ToolAnnotations

from spotify_mcp_server.prompts import register_prompts
from spotify_mcp_server.resources import register_resources
from spotify_mcp_server.spotify.auth import SpotifyTokenProvider
from spotify_mcp_server.spotify.client import SpotifyClient
from spotify_mcp_server.spotify.config import Settings
from spotify_mcp_server.tools.common import ToolResponse
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


def build_service(settings: Settings | None = None) -> SpotifyService:
    """Build the production service without initiating OAuth or a Spotify request."""

    resolved = settings or Settings.from_env()
    provider = SpotifyTokenProvider(resolved)
    client = SpotifyClient(
        provider, base_url=resolved.api_base_url, max_retries=resolved.max_retries
    )
    return SpotifyService(client)


def create_server(service: SpotifyService | None = None) -> MCPServer:
    """Create the server with an injectable service for account-free protocol tests."""

    spotify = service or build_service()
    server = MCPServer(
        "Spotify MCP Server",
        version="0.1.0",
        instructions=(
            "Use these bundled Spotify tools to minimize conversational turns. Prefer parallel "
            "read bundles. Treat player, playlist, and library modifications as user-visible "
            "writes; preserve caller order and inspect structured warnings for partial success."
        ),
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
    async def search_catalog(request: SearchCatalogInput) -> ToolResponse:
        return await spotify.search_catalog(request)

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
    async def get_item(request: GetItemInput) -> ToolResponse:
        return await spotify.get_item(request)

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
    async def player_status(request: PlayerStatusInput) -> ToolResponse:
        return await spotify.player_status(request)

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
    async def player_control(request: PlayerControlInput) -> ToolResponse:
        return await spotify.player_control(request)

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
    async def playlist_read(request: PlaylistReadInput) -> ToolResponse:
        return await spotify.playlist_read(request)

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
    async def playlist_modify(request: PlaylistModifyInput) -> ToolResponse:
        return await spotify.playlist_modify(request)

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
    async def library_read(request: LibraryReadInput) -> ToolResponse:
        return await spotify.library_read(request)

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
    async def library_modify(request: LibraryModifyInput) -> ToolResponse:
        return await spotify.library_modify(request)

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
    async def listening_activity(request: ListeningActivityInput) -> ToolResponse:
        return await spotify.listening_activity(request)

    register_prompts(server)
    register_resources(server, spotify.client)

    return server


mcp = create_server()


def main() -> None:
    """Run stateless Streamable HTTP on an explicitly loopback-only address."""

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
