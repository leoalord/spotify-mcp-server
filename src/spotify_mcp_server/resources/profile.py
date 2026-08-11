"""Current-user profile resource."""

from __future__ import annotations

from typing import Any

from mcp.server.mcpserver import MCPServer

from spotify_mcp_server.spotify.client import SpotifyClient


async def current_user_profile(client: SpotifyClient) -> dict[str, Any]:
    """Return only the stable current-user context approved for auto-attachment."""

    profile = await client.request("GET", "/me")
    if not isinstance(profile, dict):
        raise TypeError("Spotify current-user profile was not a JSON object")
    account_id = profile.get("account_id")
    display_name = profile.get("display_name")
    if not isinstance(account_id, str) or not account_id:
        raise TypeError("Spotify current-user profile has no valid account_id")
    if display_name is not None and not isinstance(display_name, str):
        raise TypeError("Spotify current-user profile has an invalid display_name")
    return {
        "display_name": display_name,
        "account_id": account_id,
    }


def register_resources(server: MCPServer, client: SpotifyClient) -> None:
    """Register the intentionally small Spotify resource catalog."""

    @server.resource(
        "spotify://me",
        name="spotify_current_user",
        title="Spotify current user",
        description="Current Spotify display name and stable pseudoanonymous account identifier.",
        mime_type="application/json",
    )
    async def spotify_me() -> dict[str, Any]:
        return await current_user_profile(client)
