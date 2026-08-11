"""Pydantic contracts exposed by the Spotify MCP server."""

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

__all__ = [
    "GetItemInput",
    "LibraryModifyInput",
    "LibraryReadInput",
    "ListeningActivityInput",
    "PlayerControlInput",
    "PlayerStatusInput",
    "PlaylistModifyInput",
    "PlaylistReadInput",
    "SearchCatalogInput",
    "ToolResponse",
]
