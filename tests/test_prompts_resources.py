"""Discovery and rendering tests for curated prompts and the profile resource."""

from __future__ import annotations

import json

import httpx2
import pytest
from mcp import Client

from spotify_mcp_server.server import create_server
from spotify_mcp_server.spotify.client import SpotifyClient
from spotify_mcp_server.tools.service import SpotifyService

pytestmark = pytest.mark.anyio

PROMPT_NAMES = [
    "catch_up_on_podcasts",
    "weekly_music_recap",
    "build_playlist_for_mood",
    "now_playing_briefing",
]


class Tokens:
    async def access_token(self, *, force_refresh: bool = False) -> str:
        return "token"


async def test_prompt_catalog_and_arguments_are_discoverable() -> None:
    server = create_server()
    async with Client(server) as client:
        prompts = (await client.list_prompts()).prompts

    assert [prompt.name for prompt in prompts] == PROMPT_NAMES
    arguments = {prompt.name: prompt.arguments or [] for prompt in prompts}
    assert [argument.name for argument in arguments["catch_up_on_podcasts"]] == ["show_filter"]
    assert arguments["catch_up_on_podcasts"][0].required is False
    assert [argument.name for argument in arguments["build_playlist_for_mood"]] == ["mood"]
    assert arguments["build_playlist_for_mood"][0].required is True


async def test_prompts_render_current_tool_workflows_and_boundaries() -> None:
    server = create_server()
    async with Client(server) as client:
        podcast = await client.get_prompt("catch_up_on_podcasts", {"show_filter": "design"})
        playlist = await client.get_prompt("build_playlist_for_mood", {"mood": "late-night focus"})
        recap = await client.get_prompt("weekly_music_recap")
        now_playing = await client.get_prompt("now_playing_briefing")

    podcast_text = podcast.messages[0].content.text
    playlist_text = playlist.messages[0].content.text
    recap_text = recap.messages[0].content.text
    now_playing_text = now_playing.messages[0].content.text
    assert "library_read" in podcast_text
    assert "get_item" in podcast_text
    assert "design" in podcast_text
    assert "transcripts" in podcast_text
    assert "search_catalog" in playlist_text
    assert "playlist_modify" in playlist_text
    assert "late-night focus" in playlist_text
    assert "audio features" in playlist_text
    assert "listening_activity" in recap_text
    assert "complete week" in recap_text
    assert "player_status" in now_playing_text
    assert "partial-result warning" in now_playing_text


async def test_spotify_me_resource_reads_and_filters_current_profile() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.method == "GET"
        assert request.url.path == "/v1/me"
        assert request.headers["Authorization"] == "Bearer token"
        return httpx2.Response(
            200,
            json={
                "display_name": "Leo",
                "account_id": "account-123",
                "id": "legacy-user-id",
                "email": "not-exposed@example.com",
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        spotify = SpotifyClient(Tokens(), client=http)
        server = create_server(SpotifyService(spotify))
        async with Client(server) as client:
            resources = (await client.list_resources()).resources
            result = await client.read_resource("spotify://me")

    assert [str(resource.uri) for resource in resources] == ["spotify://me"]
    assert resources[0].mime_type == "application/json"
    content = result.contents[0]
    assert content.mime_type == "application/json"
    assert json.loads(content.text) == {
        "display_name": "Leo",
        "account_id": "account-123",
    }
