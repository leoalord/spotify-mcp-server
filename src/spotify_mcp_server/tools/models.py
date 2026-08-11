"""Pydantic v2 input contracts for all nine public MCP tools."""

from __future__ import annotations

from typing import Literal

from pydantic import Field, model_validator

from spotify_mcp_server.tools.common import (
    FailureMode,
    OffsetPaging,
    PriorActionReference,
    SpotifyReferenceInput,
    SpotifyType,
    StrictModel,
    one_of,
)


class SearchCatalogInput(StrictModel):
    query: str = Field(min_length=1, description="Spotify search query, including field filters.")
    types: list[Literal["album", "artist", "playlist", "track", "show", "episode", "audiobook"]] = (
        Field(min_length=1, description="Distinct Spotify catalog types to search in parallel.")
    )
    market: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    limit: int = Field(default=5, ge=1, le=10)
    offset: int = Field(default=0, ge=0, le=1000)
    include_external: Literal["audio"] | None = None
    max_pages_per_type: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def unique_types(self) -> SearchCatalogInput:
        if len(self.types) != len(set(self.types)):
            raise ValueError("types must not contain duplicates")
        return self


class GetItemInput(StrictModel):
    items: list[SpotifyReferenceInput] = Field(min_length=1, max_length=25)
    market: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")
    expansions: list[
        Literal["album_tracks", "artist_albums", "show_episodes", "audiobook_chapters"]
    ] = Field(default_factory=list)
    expansion_paging: OffsetPaging = Field(default_factory=OffsetPaging)

    @model_validator(mode="after")
    def unique_expansions(self) -> GetItemInput:
        if len(self.expansions) != len(set(self.expansions)):
            raise ValueError("expansions must not contain duplicates")
        return self


class PlayerStatusInput(StrictModel):
    include: list[Literal["current_playback", "devices", "queue"]] = Field(
        default=["current_playback", "devices", "queue"], min_length=1
    )
    market: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")

    @model_validator(mode="after")
    def unique_sections(self) -> PlayerStatusInput:
        if len(self.include) != len(set(self.include)):
            raise ValueError("include must not contain duplicates")
        return self


class PlayerAction(StrictModel):
    action: Literal[
        "transfer_playback",
        "start_playback",
        "pause_playback",
        "skip_next",
        "skip_previous",
        "seek",
        "set_repeat",
        "set_volume",
        "set_shuffle",
        "add_to_queue",
    ]
    device_id: str | None = None
    device_ids: list[str] | None = Field(default=None, min_length=1, max_length=1)
    play: bool | None = None
    context_uri: str | None = None
    uris: list[str] | None = Field(default=None, min_length=1, max_length=100)
    offset_uri: str | None = None
    offset_position: int | None = Field(default=None, ge=0)
    position_ms: int | None = Field(default=None, ge=0)
    state: Literal["track", "context", "off"] | None = None
    volume_percent: int | None = Field(default=None, ge=0, le=100)
    enabled: bool | None = None
    uri: str | None = None

    @model_validator(mode="after")
    def validate_action_fields(self) -> PlayerAction:
        required: dict[str, tuple[object, str]] = {
            "transfer_playback": (self.device_ids, "device_ids"),
            "seek": (self.position_ms, "position_ms"),
            "set_repeat": (self.state, "state"),
            "set_volume": (self.volume_percent, "volume_percent"),
            "set_shuffle": (self.enabled, "enabled"),
            "add_to_queue": (self.uri, "uri"),
        }
        if self.action in required and required[self.action][0] is None:
            raise ValueError(f"{required[self.action][1]} is required for {self.action}")
        if self.action == "start_playback":
            if self.context_uri is not None and self.uris is not None:
                raise ValueError("start_playback accepts context_uri or uris, not both")
            if self.offset_uri is not None and self.offset_position is not None:
                raise ValueError("start_playback accepts one offset form")
        return self


class PlayerControlInput(StrictModel):
    actions: list[PlayerAction] = Field(min_length=1, max_length=20)
    failure_mode: FailureMode = FailureMode.STOP
    include_final_status: bool = True


class PlaylistReadRequest(StrictModel):
    operation: Literal["list_current_playlists", "get_playlists"]
    playlists: list[SpotifyReferenceInput] = Field(default_factory=list, max_length=20)
    include_items: bool = False
    paging: OffsetPaging = Field(default_factory=OffsetPaging)

    @model_validator(mode="after")
    def playlists_for_get(self) -> PlaylistReadRequest:
        if self.operation == "get_playlists" and not self.playlists:
            raise ValueError("playlists is required for get_playlists")
        if self.operation == "list_current_playlists" and self.playlists:
            raise ValueError("playlists is not used by list_current_playlists")
        return self


class PlaylistReadInput(StrictModel):
    requests: list[PlaylistReadRequest] = Field(min_length=1, max_length=20)
    market: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class PlaylistAction(StrictModel):
    action: Literal[
        "create_playlist",
        "change_playlist_details",
        "add_items",
        "remove_items",
        "replace_items",
        "reorder_items",
    ]
    playlist: SpotifyReferenceInput | PriorActionReference | None = None
    name: str | None = Field(default=None, min_length=1)
    public: bool | None = None
    collaborative: bool | None = None
    description: str | None = None
    uris: list[str] | None = Field(default=None, max_length=100)
    position: int | None = Field(default=None, ge=0)
    snapshot_id: str | None = None
    range_start: int | None = Field(default=None, ge=0)
    insert_before: int | None = Field(default=None, ge=0)
    range_length: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def validate_playlist_action(self) -> PlaylistAction:
        if self.action == "create_playlist":
            if self.name is None:
                raise ValueError("name is required for create_playlist")
        elif self.playlist is None:
            raise ValueError("playlist is required for this action")
        if self.action == "change_playlist_details" and all(
            value is None
            for value in (self.name, self.public, self.collaborative, self.description)
        ):
            raise ValueError("at least one detail is required for change_playlist_details")
        if self.action in {"add_items", "remove_items", "replace_items"} and self.uris is None:
            raise ValueError("uris is required for item mutations")
        if self.action in {"add_items", "remove_items"} and not self.uris:
            raise ValueError("uris must not be empty for add_items or remove_items")
        if self.action == "reorder_items" and one_of(self.range_start, self.insert_before) != 2:
            raise ValueError("range_start and insert_before are required for reorder_items")
        return self


class PlaylistModifyInput(StrictModel):
    actions: list[PlaylistAction] = Field(min_length=1, max_length=20)
    failure_mode: FailureMode = FailureMode.STOP


class LibraryReadRequest(StrictModel):
    operation: Literal["list_saved", "list_followed_artists", "contains"]
    type: Literal["track", "album", "show", "episode", "audiobook"] | None = None
    items: list[SpotifyReferenceInput] = Field(default_factory=list, max_length=40)
    paging: OffsetPaging = Field(default_factory=OffsetPaging)
    after: str | None = None

    @model_validator(mode="after")
    def validate_library_read(self) -> LibraryReadRequest:
        if self.operation == "list_saved" and self.type is None:
            raise ValueError("type is required for list_saved")
        if self.operation == "contains" and not self.items:
            raise ValueError("items is required for contains")
        return self


class LibraryReadInput(StrictModel):
    requests: list[LibraryReadRequest] = Field(min_length=1, max_length=10)
    market: str | None = Field(default=None, pattern=r"^[A-Z]{2}$")


class LibraryAction(StrictModel):
    action: Literal["save", "remove", "follow", "unfollow"]
    items: list[SpotifyReferenceInput] = Field(min_length=1, max_length=40)

    @model_validator(mode="after")
    def validate_item_types(self) -> LibraryAction:
        types = {item.normalized().type for item in self.items}
        save_types = {
            SpotifyType.TRACK,
            SpotifyType.ALBUM,
            SpotifyType.EPISODE,
            SpotifyType.SHOW,
            SpotifyType.AUDIOBOOK,
        }
        follow_types = {SpotifyType.ARTIST, SpotifyType.PLAYLIST}
        allowed = save_types if self.action in {"save", "remove"} else follow_types
        if not types <= allowed:
            raise ValueError(f"{self.action} does not support one or more supplied item types")
        return self


class LibraryModifyInput(StrictModel):
    actions: list[LibraryAction] = Field(min_length=1, max_length=20)
    failure_mode: FailureMode = FailureMode.STOP


class ListeningActivityInput(StrictModel):
    include: list[Literal["recent_tracks", "top_tracks", "top_artists"]] = Field(
        default=["recent_tracks", "top_tracks", "top_artists"],
        min_length=1,
    )
    time_ranges: list[Literal["short_term", "medium_term", "long_term"]] = Field(
        default=["short_term", "medium_term", "long_term"], min_length=1
    )
    recent_limit: int = Field(default=20, ge=1, le=50)
    top_limit: int = Field(default=20, ge=1, le=50)
    before: int | None = Field(default=None, ge=0)
    after: int | None = Field(default=None, ge=0)
    top_offset: int = Field(default=0, ge=0)
    max_pages: int = Field(default=1, ge=1, le=10)

    @model_validator(mode="after")
    def cursor_choice(self) -> ListeningActivityInput:
        if self.before is not None and self.after is not None:
            raise ValueError("before and after are mutually exclusive")
        if len(self.include) != len(set(self.include)):
            raise ValueError("include must not contain duplicates")
        if len(self.time_ranges) != len(set(self.time_ranges)):
            raise ValueError("time_ranges must not contain duplicates")
        return self
