from __future__ import annotations

from typing import Any, cast

import pytest

from spotify_mcp_server.spotify.client import SpotifyAPIError, SpotifyClient
from spotify_mcp_server.tools.common import OffsetPaging, SpotifyReferenceInput
from spotify_mcp_server.tools.models import (
    GetItemInput,
    LibraryReadInput,
    LibraryReadRequest,
    ListeningActivityInput,
    PlayerStatusInput,
    PlaylistReadInput,
    PlaylistReadRequest,
    SearchCatalogInput,
)
from spotify_mcp_server.tools.service import SpotifyService

pytestmark = pytest.mark.anyio


class StubClient:
    def __init__(self) -> None:
        self.requests: list[tuple[str, str, dict[str, Any], dict[str, Any] | None]] = []
        self.pages: list[tuple[str, dict[str, Any], int, str | None]] = []
        self.fail_paths: set[str] = set()
        self.fail_containers: set[str] = set()

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        self.requests.append((method, path, params or {}, json))
        if path in self.fail_paths:
            raise SpotifyAPIError(403, f"denied {path}")
        if path == "/me/library/contains":
            return [True, False]
        if path == "/me/player":
            return {"is_playing": True}
        if path.endswith("/devices"):
            return {"devices": [{"id": "d"}]}
        if path.endswith("/queue"):
            return {"queue": []}
        return {"id": path.rsplit("/", 1)[-1], "type": path.split("/")[1].rstrip("s")}

    async def paged(
        self,
        path: str,
        *,
        params: dict[str, Any],
        max_pages: int,
        container_key: str | None = None,
    ) -> dict[str, Any]:
        self.pages.append((path, params, max_pages, container_key))
        if path in self.fail_paths or container_key in self.fail_containers:
            raise SpotifyAPIError(429, "slow", retry_after_seconds=2)
        return {"items": [{"id": path}], "next": None, "pages_fetched": 1}


def service(stub: StubClient) -> SpotifyService:
    return SpotifyService(cast(SpotifyClient, stub))


async def test_search_catalog_total_failure_is_error() -> None:
    stub = StubClient()
    stub.fail_paths.add("/search")
    result = await service(stub).search_catalog(
        SearchCatalogInput(query="focus", types=["track", "album"], max_pages_per_type=3)
    )
    assert result.status == "error"
    assert result.data["results"] == {}
    assert len(result.warnings) == 2
    assert result.warnings[0].retry_after_seconds == 2
    assert all(page[2] == 3 for page in stub.pages)


async def test_search_catalog_retains_successful_type_as_partial() -> None:
    stub = StubClient()
    stub.fail_containers.add("albums")
    result = await service(stub).search_catalog(
        SearchCatalogInput(query="focus", types=["track", "album"])
    )
    assert result.status == "partial"
    assert set(result.data["results"]) == {"track"}
    assert [warning.code for warning in result.warnings] == ["search_failed_album"]


async def test_search_catalog_groups_successful_types() -> None:
    stub = StubClient()
    result = await service(stub).search_catalog(
        SearchCatalogInput(query="focus", types=["track", "audiobook"])
    )
    assert result.status == "ok"
    assert set(result.data["results"]) == {"track", "audiobook"}
    assert {page[3] for page in stub.pages} == {"tracks", "audiobooks"}


async def test_get_item_mixed_expansions_and_invalid_partial() -> None:
    stub = StubClient()
    result = await service(stub).get_item(
        GetItemInput(
            items=[
                SpotifyReferenceInput(value="spotify:album:a1"),
                SpotifyReferenceInput(value="bare"),
            ],
            expansions=["album_tracks", "show_episodes"],
            expansion_paging=OffsetPaging(limit=10, max_pages=2),
        )
    )
    assert result.status == "partial"
    assert result.data["results"][0]["expansions"]["album_tracks"]["pages_fetched"] == 1
    assert result.warnings[0].request_index == 1
    assert stub.pages[0][0] == "/albums/a1/tracks"


async def test_player_status_bundles_and_retains_partial() -> None:
    stub = StubClient()
    stub.fail_paths.add("/me/player/queue")
    result = await service(stub).player_status(PlayerStatusInput())
    assert result.status == "partial"
    assert result.data["current_playback"]["is_playing"] is True
    assert result.data["queue"] is None
    assert len(stub.requests) == 3


async def test_player_status_total_failure_is_error() -> None:
    stub = StubClient()
    stub.fail_paths.update({"/me/player", "/me/player/devices", "/me/player/queue"})
    result = await service(stub).player_status(PlayerStatusInput())
    assert result.status == "error"
    assert all(value is None for value in result.data.values())
    assert len(result.warnings) == 3


async def test_playlist_read_list_and_contents() -> None:
    stub = StubClient()
    result = await service(stub).playlist_read(
        PlaylistReadInput(
            requests=[
                PlaylistReadRequest(
                    operation="list_current_playlists", paging=OffsetPaging(max_pages=2)
                ),
                PlaylistReadRequest(
                    operation="get_playlists",
                    playlists=[SpotifyReferenceInput(value="spotify:playlist:p1")],
                    include_items=True,
                ),
            ]
        )
    )
    assert result.status == "ok"
    assert [page[0] for page in stub.pages] == ["/me/playlists", "/playlists/p1/items"]
    assert any(path == "/playlists/p1" for _, path, _, _ in stub.requests)


async def test_playlist_read_partial_on_access_rule() -> None:
    stub = StubClient()
    stub.fail_paths.add("/playlists/p1/items")
    result = await service(stub).playlist_read(
        PlaylistReadInput(
            requests=[
                PlaylistReadRequest(
                    operation="get_playlists",
                    playlists=[SpotifyReferenceInput(value="spotify:playlist:p1")],
                    include_items=True,
                )
            ]
        )
    )
    assert result.status == "error"
    assert result.warnings[0].request_index == 0


async def test_library_read_all_operations() -> None:
    stub = StubClient()
    result = await service(stub).library_read(
        LibraryReadInput(
            requests=[
                LibraryReadRequest(operation="list_saved", type="audiobook"),
                LibraryReadRequest(operation="list_followed_artists", after="cursor"),
                LibraryReadRequest(
                    operation="contains",
                    items=[
                        SpotifyReferenceInput(value="spotify:track:t1"),
                        SpotifyReferenceInput(value="spotify:playlist:p1"),
                    ],
                ),
            ]
        )
    )
    assert result.status == "ok"
    assert stub.pages[0][0] == "/me/audiobooks"
    assert stub.pages[1][3] == "artists"
    assert result.data["results"][2]["items"][1]["saved"] is False


async def test_library_read_returns_warning_for_bad_reference() -> None:
    stub = StubClient()
    result = await service(stub).library_read(
        LibraryReadInput(
            requests=[
                LibraryReadRequest(
                    operation="contains", items=[SpotifyReferenceInput(value="bare")]
                )
            ]
        )
    )
    assert result.status == "error"
    assert result.warnings[0].code == "library_read_failed"


async def test_listening_activity_fans_out_ranges_and_partial() -> None:
    stub = StubClient()
    stub.fail_paths.add("/me/top/artists")
    result = await service(stub).listening_activity(
        ListeningActivityInput(
            include=["recent_tracks", "top_tracks", "top_artists"],
            time_ranges=["short_term", "long_term"],
            max_pages=2,
        )
    )
    assert result.status == "partial"
    assert set(result.data["top_tracks"]) == {"short_term", "long_term"}
    assert result.data["recent_tracks"]["pages_fetched"] == 1
    assert len(result.warnings) == 2
