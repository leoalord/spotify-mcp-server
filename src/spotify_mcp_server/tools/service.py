"""Bundled Spotify operations backing the nine public MCP tools."""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from functools import partial
from typing import Any

from spotify_mcp_server.spotify.client import SpotifyAPIError, SpotifyClient
from spotify_mcp_server.tools.common import (
    ContractWarning,
    FailureMode,
    PriorActionReference,
    SpotifyReferenceInput,
    SpotifyType,
    ToolResponse,
)
from spotify_mcp_server.tools.models import (
    GetItemInput,
    LibraryModifyInput,
    LibraryReadInput,
    ListeningActivityInput,
    PlayerAction,
    PlayerControlInput,
    PlayerStatusInput,
    PlaylistAction,
    PlaylistModifyInput,
    PlaylistReadInput,
    SearchCatalogInput,
)

ITEM_PATHS = {
    SpotifyType.TRACK: "/tracks/{id}",
    SpotifyType.ALBUM: "/albums/{id}",
    SpotifyType.ARTIST: "/artists/{id}",
    SpotifyType.SHOW: "/shows/{id}",
    SpotifyType.EPISODE: "/episodes/{id}",
    SpotifyType.AUDIOBOOK: "/audiobooks/{id}",
    SpotifyType.CHAPTER: "/chapters/{id}",
}

EXPANSIONS: dict[tuple[SpotifyType, str], str] = {
    (SpotifyType.ALBUM, "album_tracks"): "/albums/{id}/tracks",
    (SpotifyType.ARTIST, "artist_albums"): "/artists/{id}/albums",
    (SpotifyType.SHOW, "show_episodes"): "/shows/{id}/episodes",
    (SpotifyType.AUDIOBOOK, "audiobook_chapters"): "/audiobooks/{id}/chapters",
}


class SpotifyService:
    """High-level orchestration; reads fan out and writes remain explicitly ordered."""

    def __init__(self, client: SpotifyClient) -> None:
        self.client = client

    async def search_catalog(self, request: SearchCatalogInput) -> ToolResponse:
        """Search multiple Spotify catalog types in parallel and preserve type-grouped pages."""

        async def search_one(item_type: str) -> tuple[str, Any, ContractWarning | None]:
            params = {
                "q": request.query,
                "type": item_type,
                "market": request.market,
                "limit": request.limit,
                "offset": request.offset,
                "include_external": request.include_external,
            }
            try:
                page = await self.client.paged(
                    "/search",
                    params=params,
                    max_pages=request.max_pages_per_type,
                    container_key=f"{item_type}s",
                )
                return item_type, page, None
            except SpotifyAPIError as exc:
                return item_type, None, _warning(exc, f"search_failed_{item_type}")

        resolved = await asyncio.gather(*(search_one(value) for value in request.types))
        warnings = [warning for _, _, warning in resolved if warning is not None]
        results = {item_type: page for item_type, page, _ in resolved if page is not None}
        return _response({"results": results}, warnings)

    async def get_item(self, request: GetItemInput) -> ToolResponse:
        """Resolve heterogeneous Spotify references with optional bounded natural expansions."""

        async def get_one(index: int, supplied: SpotifyReferenceInput) -> tuple[Any, Any]:
            try:
                ref = supplied.normalized(allowed=set(ITEM_PATHS))
                params = {"market": request.market}
                item = await self.client.request(
                    "GET", ITEM_PATHS[ref.type].format(id=ref.id), params=params
                )
                expanded: dict[str, Any] = {}
                for expansion in request.expansions:
                    path = EXPANSIONS.get((ref.type, expansion))
                    if path is not None:
                        expanded[expansion] = await self.client.paged(
                            path.format(id=ref.id),
                            params={
                                "market": request.market,
                                "limit": request.expansion_paging.limit,
                                "offset": request.expansion_paging.offset,
                            },
                            max_pages=request.expansion_paging.max_pages,
                        )
                return {
                    "request_index": index,
                    "reference": ref.model_dump(),
                    "item": item,
                    "expansions": expanded,
                }, None
            except (SpotifyAPIError, ValueError) as exc:
                return None, _warning(exc, "item_failed", request_index=index)

        resolved = await asyncio.gather(
            *(get_one(index, item) for index, item in enumerate(request.items))
        )
        warnings = [warning for _, warning in resolved if warning is not None]
        results = [item for item, _ in resolved if item is not None]
        return _response({"results": results}, warnings)

    async def player_status(self, request: PlayerStatusInput) -> ToolResponse:
        """Fetch current playback, devices, and queue together when requested."""

        paths = {
            "current_playback": ("/me/player", {"market": request.market}),
            "devices": ("/me/player/devices", {}),
            "queue": ("/me/player/queue", {}),
        }

        async def get_part(name: str) -> tuple[str, Any, ContractWarning | None]:
            path, params = paths[name]
            try:
                return name, await self.client.request("GET", path, params=params), None
            except SpotifyAPIError as exc:
                return name, None, _warning(exc, f"player_{name}_failed")

        resolved = await asyncio.gather(*(get_part(name) for name in request.include))
        warnings = [warning for _, _, warning in resolved if warning is not None]
        data = {name: value for name, value, _ in resolved}
        return _response(data, warnings)

    async def player_control(self, request: PlayerControlInput) -> ToolResponse:
        """Execute up to twenty playback actions in order and optionally return final status."""

        results: list[dict[str, Any]] = []
        warnings: list[ContractWarning] = []
        for index, action in enumerate(request.actions):
            try:
                payload = await self._player_action(action)
                results.append({"action_index": index, "action": action.action, "result": payload})
            except SpotifyAPIError as exc:
                warnings.append(_warning(exc, "player_action_failed", action_index=index))
                if request.failure_mode == FailureMode.STOP:
                    break
        data: dict[str, Any] = {"actions": results}
        if request.include_final_status:
            status = await self.player_status(PlayerStatusInput())
            data["final_status"] = status.data
            warnings.extend(status.warnings)
        return _response(data, warnings)

    async def _player_action(self, action: PlayerAction) -> Any:
        device = {"device_id": action.device_id}
        if action.action == "transfer_playback":
            return await self.client.request(
                "PUT",
                "/me/player",
                json=_compact({"device_ids": action.device_ids, "play": action.play}),
            )
        if action.action == "start_playback":
            body: dict[str, Any] = {}
            if action.context_uri is not None:
                body["context_uri"] = action.context_uri
            if action.uris is not None:
                body["uris"] = action.uris
            if action.offset_uri is not None:
                body["offset"] = {"uri": action.offset_uri}
            if action.offset_position is not None:
                body["offset"] = {"position": action.offset_position}
            if action.position_ms is not None:
                body["position_ms"] = action.position_ms
            return await self.client.request("PUT", "/me/player/play", params=device, json=body)
        if action.action == "pause_playback":
            return await self.client.request("PUT", "/me/player/pause", params=device)
        if action.action == "skip_next":
            return await self.client.request("POST", "/me/player/next", params=device)
        if action.action == "skip_previous":
            return await self.client.request("POST", "/me/player/previous", params=device)
        if action.action == "seek":
            return await self.client.request(
                "PUT", "/me/player/seek", params={**device, "position_ms": action.position_ms}
            )
        if action.action == "set_repeat":
            return await self.client.request(
                "PUT", "/me/player/repeat", params={**device, "state": action.state}
            )
        if action.action == "set_volume":
            return await self.client.request(
                "PUT",
                "/me/player/volume",
                params={**device, "volume_percent": action.volume_percent},
            )
        if action.action == "set_shuffle":
            return await self.client.request(
                "PUT", "/me/player/shuffle", params={**device, "state": action.enabled}
            )
        return await self.client.request(
            "POST", "/me/player/queue", params={**device, "uri": action.uri}
        )

    async def playlist_read(self, request: PlaylistReadInput) -> ToolResponse:
        """List the user's playlists or fetch owned/collaborative playlist contents."""

        results: list[dict[str, Any]] = []
        warnings: list[ContractWarning] = []
        for index, item in enumerate(request.requests):
            try:
                if item.operation == "list_current_playlists":
                    page = await self.client.paged(
                        "/me/playlists",
                        params={"limit": item.paging.limit, "offset": item.paging.offset},
                        max_pages=item.paging.max_pages,
                    )
                    results.append({"request_index": index, "playlists": page})
                    continue
                playlists = []
                for supplied in item.playlists:
                    ref = supplied.normalized(allowed={SpotifyType.PLAYLIST})
                    playlist = await self.client.request("GET", f"/playlists/{ref.id}")
                    entry: dict[str, Any] = {"playlist": playlist}
                    if item.include_items:
                        entry["items"] = await self.client.paged(
                            f"/playlists/{ref.id}/items",
                            params={
                                "market": request.market,
                                "limit": item.paging.limit,
                                "offset": item.paging.offset,
                            },
                            max_pages=item.paging.max_pages,
                        )
                    playlists.append(entry)
                results.append({"request_index": index, "playlists": playlists})
            except (SpotifyAPIError, ValueError) as exc:
                warnings.append(_warning(exc, "playlist_read_failed", request_index=index))
        return _response({"results": results}, warnings)

    async def playlist_modify(self, request: PlaylistModifyInput) -> ToolResponse:
        """Execute bounded playlist creates and mutations with prior-action references."""

        results: list[dict[str, Any]] = []
        warnings: list[ContractWarning] = []
        for index, action in enumerate(request.actions):
            try:
                result = await self._playlist_action(action, index, results)
                results.append({"action_index": index, "action": action.action, "result": result})
            except (SpotifyAPIError, ValueError) as exc:
                warnings.append(_warning(exc, "playlist_action_failed", action_index=index))
                if request.failure_mode == FailureMode.STOP:
                    break
        return _response({"actions": results}, warnings)

    async def _playlist_action(
        self, action: PlaylistAction, index: int, prior_results: list[dict[str, Any]]
    ) -> Any:
        if action.action == "create_playlist":
            return await self.client.request(
                "POST",
                "/me/playlists",
                json=_compact(
                    {
                        "name": action.name,
                        "public": action.public,
                        "collaborative": action.collaborative,
                        "description": action.description,
                    }
                ),
            )
        playlist_id = _playlist_id(action.playlist, index, prior_results)
        path = f"/playlists/{playlist_id}"
        if action.action == "change_playlist_details":
            return await self.client.request(
                "PUT",
                path,
                json=_compact(
                    {
                        "name": action.name,
                        "public": action.public,
                        "collaborative": action.collaborative,
                        "description": action.description,
                    }
                ),
            )
        items_path = f"{path}/items"
        if action.action == "add_items":
            return await self.client.request(
                "POST",
                items_path,
                json=_compact({"uris": action.uris, "position": action.position}),
            )
        if action.action == "remove_items":
            return await self.client.request(
                "DELETE",
                items_path,
                json=_compact(
                    {
                        "items": [{"uri": uri} for uri in (action.uris or [])],
                        "snapshot_id": action.snapshot_id,
                    }
                ),
            )
        if action.action == "replace_items":
            return await self.client.request("PUT", items_path, json={"uris": action.uris})
        return await self.client.request(
            "PUT",
            items_path,
            json=_compact(
                {
                    "range_start": action.range_start,
                    "insert_before": action.insert_before,
                    "range_length": action.range_length,
                    "snapshot_id": action.snapshot_id,
                }
            ),
        )

    async def library_read(self, request: LibraryReadInput) -> ToolResponse:
        """Bundle saved-item pages, followed artists, and heterogeneous contains checks."""

        results: list[dict[str, Any]] = []
        warnings: list[ContractWarning] = []
        for index, item in enumerate(request.requests):
            try:
                if item.operation == "list_saved":
                    page = await self.client.paged(
                        f"/me/{item.type}s",
                        params={
                            "market": request.market,
                            "limit": item.paging.limit,
                            "offset": item.paging.offset,
                        },
                        max_pages=item.paging.max_pages,
                    )
                    results.append({"request_index": index, "type": item.type, "items": page})
                elif item.operation == "list_followed_artists":
                    page = await self.client.paged(
                        "/me/following",
                        params={"type": "artist", "after": item.after, "limit": item.paging.limit},
                        max_pages=item.paging.max_pages,
                        container_key="artists",
                    )
                    results.append({"request_index": index, "artists": page})
                else:
                    refs = [
                        supplied.normalized(
                            allowed={
                                SpotifyType.TRACK,
                                SpotifyType.ALBUM,
                                SpotifyType.EPISODE,
                                SpotifyType.SHOW,
                                SpotifyType.AUDIOBOOK,
                                SpotifyType.ARTIST,
                                SpotifyType.PLAYLIST,
                            }
                        )
                        for supplied in item.items
                    ]
                    contains = await self.client.request(
                        "GET",
                        "/me/library/contains",
                        params={"uris": ",".join(r.uri for r in refs)},
                    )
                    results.append(
                        {
                            "request_index": index,
                            "items": [
                                {"reference": ref.model_dump(), "saved": saved}
                                for ref, saved in zip(refs, contains, strict=True)
                            ],
                        }
                    )
            except (SpotifyAPIError, ValueError) as exc:
                warnings.append(_warning(exc, "library_read_failed", request_index=index))
        return _response({"results": results}, warnings)

    async def library_modify(self, request: LibraryModifyInput) -> ToolResponse:
        """Save/remove/follow/unfollow heterogeneous Spotify URIs in ordered batches."""

        results: list[dict[str, Any]] = []
        warnings: list[ContractWarning] = []
        for index, action in enumerate(request.actions):
            try:
                refs = [item.normalized() for item in action.items]
                method = "PUT" if action.action in {"save", "follow"} else "DELETE"
                await self.client.request(
                    method, "/me/library", params={"uris": ",".join(ref.uri for ref in refs)}
                )
                results.append(
                    {
                        "action_index": index,
                        "action": action.action,
                        "references": [ref.model_dump() for ref in refs],
                    }
                )
            except (SpotifyAPIError, ValueError) as exc:
                warnings.append(_warning(exc, "library_action_failed", action_index=index))
                if request.failure_mode == FailureMode.STOP:
                    break
        return _response({"actions": results}, warnings)

    async def listening_activity(self, request: ListeningActivityInput) -> ToolResponse:
        """Fetch recent tracks and top tracks/artists across requested Spotify time ranges."""

        calls: list[tuple[str, str, Callable[[], Awaitable[dict[str, Any]]]]] = []
        if "recent_tracks" in request.include:
            calls.append(
                (
                    "recent_tracks",
                    "recent",
                    partial(
                        self.client.paged,
                        "/me/player/recently-played",
                        params={
                            "limit": request.recent_limit,
                            "before": request.before,
                            "after": request.after,
                        },
                        max_pages=request.max_pages,
                    ),
                )
            )
        for item_type in ("tracks", "artists"):
            include_name = f"top_{item_type}"
            if include_name not in request.include:
                continue
            for time_range in request.time_ranges:
                calls.append(
                    (
                        include_name,
                        time_range,
                        partial(
                            self.client.paged,
                            f"/me/top/{item_type}",
                            params={
                                "time_range": time_range,
                                "limit": request.top_limit,
                                "offset": request.top_offset,
                            },
                            max_pages=request.max_pages,
                        ),
                    )
                )

        async def resolve(
            name: str, qualifier: str, call: Callable[[], Awaitable[dict[str, Any]]]
        ) -> tuple[str, str, Any, ContractWarning | None]:
            try:
                return name, qualifier, await call(), None
            except SpotifyAPIError as exc:
                return name, qualifier, None, _warning(exc, f"{name}_{qualifier}_failed")

        resolved = await asyncio.gather(*(resolve(*call) for call in calls))
        data: dict[str, Any] = {}
        warnings: list[ContractWarning] = []
        for name, qualifier, value, warning in resolved:
            if warning:
                warnings.append(warning)
            elif name == "recent_tracks":
                data[name] = value
            else:
                data.setdefault(name, {})[qualifier] = value
        return _response(data, warnings)


def _compact(value: dict[str, Any]) -> dict[str, Any]:
    return {key: item for key, item in value.items() if item is not None}


def _playlist_id(
    target: SpotifyReferenceInput | PriorActionReference | None,
    current_index: int,
    prior_results: list[dict[str, Any]],
) -> str:
    if isinstance(target, SpotifyReferenceInput):
        return target.normalized(allowed={SpotifyType.PLAYLIST}).id
    if isinstance(target, PriorActionReference):
        if target.action_index >= current_index:
            raise ValueError("prior action reference must point to an earlier action")
        match = next(
            (entry for entry in prior_results if entry["action_index"] == target.action_index), None
        )
        result = match and match.get("result")
        if not isinstance(result, dict) or not result.get("id"):
            raise ValueError("referenced action did not produce a playlist ID")
        return str(result["id"])
    raise ValueError("playlist target is required")


def _warning(
    exc: Exception,
    code: str,
    *,
    request_index: int | None = None,
    action_index: int | None = None,
) -> ContractWarning:
    return ContractWarning(
        code=code,
        message=str(exc),
        request_index=request_index,
        action_index=action_index,
        retry_after_seconds=exc.retry_after_seconds if isinstance(exc, SpotifyAPIError) else None,
    )


def _response(data: dict[str, Any], warnings: list[ContractWarning]) -> ToolResponse:
    if warnings and _has_usable_data(data):
        status = "partial"
    elif warnings:
        status = "error"
    else:
        status = "ok"
    return ToolResponse(status=status, data=data, warnings=warnings)


def _has_usable_data(data: dict[str, Any]) -> bool:
    for key, value in data.items():
        if key in {"actions", "results"}:
            if value:
                return True
        elif isinstance(value, dict):
            if _has_usable_data(value):
                return True
        elif value is not None:
            return True
    return False
