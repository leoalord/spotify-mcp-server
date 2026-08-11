"""Low-computation Spotify context exposed as MCP resources."""

from spotify_mcp_server.resources.profile import register_resources

__all__ = ["register_resources"]
