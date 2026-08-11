from __future__ import annotations

from typing import Any, cast

import pytest

from spotify_mcp_server.spotify.client import SpotifyAPIError, SpotifyClient
from spotify_mcp_server.tools.common import PriorActionReference, SpotifyReferenceInput
from spotify_mcp_server.tools.models import (
    LibraryAction,
    LibraryModifyInput,
    PlayerAction,
    PlayerControlInput,
    PlaylistAction,
    PlaylistModifyInput,
)
from spotify_mcp_server.tools.service import SpotifyService

pytestmark = pytest.mark.anyio


class WriteClient:
    def __init__(self, fail_at: int | None = None) -> None:
        self.calls: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = []
        self.fail_at = fail_at

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        index = len(self.calls)
        self.calls.append((method, path, params or {}, json))
        if self.fail_at == index:
            raise SpotifyAPIError(403, "denied")
        if path == "/me/playlists":
            return {"id": "created", "uri": "spotify:playlist:created"}
        if path == "/me/player":
            return {"is_playing": True}
        if path.endswith("/devices"):
            return {"devices": []}
        if path.endswith("/queue"):
            return {"queue": []}
        if path.endswith("/items"):
            return {"snapshot_id": f"s{index}"}
        return None

    async def paged(self, *args: Any, **kwargs: Any) -> dict[str, Any]:
        raise AssertionError("writes should not paginate")


def service(client: WriteClient) -> SpotifyService:
    return SpotifyService(cast(SpotifyClient, client))


async def test_player_control_maps_every_action_and_final_status() -> None:
    client = WriteClient()
    result = await service(client).player_control(
        PlayerControlInput(
            actions=[
                PlayerAction(action="transfer_playback", device_ids=["d"], play=True),
                PlayerAction(
                    action="start_playback",
                    uris=["spotify:track:t"],
                    offset_position=0,
                    position_ms=10,
                ),
                PlayerAction(action="pause_playback"),
                PlayerAction(action="skip_next"),
                PlayerAction(action="skip_previous"),
                PlayerAction(action="seek", position_ms=100),
                PlayerAction(action="set_repeat", state="context"),
                PlayerAction(action="set_volume", volume_percent=25),
                PlayerAction(action="set_shuffle", enabled=True),
                PlayerAction(action="add_to_queue", uri="spotify:track:q"),
            ]
        )
    )
    assert result.status == "ok"
    assert len(result.data["actions"]) == 10
    assert [call[1] for call in client.calls[:10]] == [
        "/me/player",
        "/me/player/play",
        "/me/player/pause",
        "/me/player/next",
        "/me/player/previous",
        "/me/player/seek",
        "/me/player/repeat",
        "/me/player/volume",
        "/me/player/shuffle",
        "/me/player/queue",
    ]
    assert len(client.calls) == 13


async def test_player_control_stop_and_continue_failure_modes() -> None:
    stopped = WriteClient(fail_at=0)
    result = await service(stopped).player_control(
        PlayerControlInput(
            actions=[PlayerAction(action="pause_playback"), PlayerAction(action="skip_next")],
            include_final_status=False,
        )
    )
    assert result.status == "error"
    assert len(stopped.calls) == 1

    continued = WriteClient(fail_at=0)
    result = await service(continued).player_control(
        PlayerControlInput(
            actions=[PlayerAction(action="pause_playback"), PlayerAction(action="skip_next")],
            failure_mode="continue",
            include_final_status=False,
        )
    )
    assert result.status == "partial"
    assert len(continued.calls) == 2


async def test_playlist_modify_all_actions_and_prior_reference() -> None:
    client = WriteClient()
    playlist = SpotifyReferenceInput(value="spotify:playlist:existing")
    result = await service(client).playlist_modify(
        PlaylistModifyInput(
            actions=[
                PlaylistAction(action="create_playlist", name="Drive", public=False),
                PlaylistAction(
                    action="change_playlist_details",
                    playlist=PriorActionReference(action_index=0),
                    description="Updated",
                ),
                PlaylistAction(action="add_items", playlist=playlist, uris=["spotify:track:t"]),
                PlaylistAction(
                    action="remove_items",
                    playlist=playlist,
                    uris=["spotify:episode:e"],
                    snapshot_id="s",
                ),
                PlaylistAction(action="replace_items", playlist=playlist, uris=[]),
                PlaylistAction(
                    action="reorder_items",
                    playlist=playlist,
                    range_start=0,
                    insert_before=2,
                    range_length=1,
                ),
            ]
        )
    )
    assert result.status == "ok"
    assert [call[0:2] for call in client.calls] == [
        ("POST", "/me/playlists"),
        ("PUT", "/playlists/created"),
        ("POST", "/playlists/existing/items"),
        ("DELETE", "/playlists/existing/items"),
        ("PUT", "/playlists/existing/items"),
        ("PUT", "/playlists/existing/items"),
    ]
    assert client.calls[3][3]["items"] == [{"uri": "spotify:episode:e"}]


async def test_playlist_modify_invalid_prior_reference_is_structured() -> None:
    client = WriteClient()
    result = await service(client).playlist_modify(
        PlaylistModifyInput(
            actions=[
                PlaylistAction(action="create_playlist", name="x"),
                PlaylistAction(
                    action="add_items",
                    playlist=PriorActionReference(action_index=1),
                    uris=["spotify:track:t"],
                ),
            ]
        )
    )
    assert result.status == "partial"
    assert result.warnings[0].action_index == 1


async def test_library_modify_uses_generic_library_endpoint() -> None:
    client = WriteClient()
    result = await service(client).library_modify(
        LibraryModifyInput(
            actions=[
                LibraryAction(
                    action="save", items=[SpotifyReferenceInput(value="spotify:track:t")]
                ),
                LibraryAction(
                    action="unfollow",
                    items=[SpotifyReferenceInput(value="spotify:playlist:p")],
                ),
            ]
        )
    )
    assert result.status == "ok"
    assert [(call[0], call[1]) for call in client.calls] == [
        ("PUT", "/me/library"),
        ("DELETE", "/me/library"),
    ]
    assert client.calls[0][2]["uris"] == "spotify:track:t"


async def test_library_modify_continue_returns_partial() -> None:
    client = WriteClient(fail_at=0)
    result = await service(client).library_modify(
        LibraryModifyInput(
            actions=[
                LibraryAction(
                    action="save", items=[SpotifyReferenceInput(value="spotify:track:t")]
                ),
                LibraryAction(
                    action="remove", items=[SpotifyReferenceInput(value="spotify:album:a")]
                ),
            ],
            failure_mode="continue",
        )
    )
    assert result.status == "partial"
    assert len(client.calls) == 2
