from __future__ import annotations

import pytest
from pydantic import ValidationError

from spotify_mcp_server.tools.common import SpotifyReferenceInput, SpotifyType
from spotify_mcp_server.tools.models import (
    GetItemInput,
    LibraryAction,
    ListeningActivityInput,
    PlayerAction,
    PlayerStatusInput,
    PlaylistAction,
    PlaylistReadRequest,
    SearchCatalogInput,
)


@pytest.mark.parametrize(
    ("supplied", "expected_type", "expected_id"),
    [
        (
            SpotifyReferenceInput(value="spotify:track:abc123"),
            SpotifyType.TRACK,
            "abc123",
        ),
        (
            SpotifyReferenceInput(value="https://open.spotify.com/album/def456?si=x"),
            SpotifyType.ALBUM,
            "def456",
        ),
        (
            SpotifyReferenceInput(value="https://open.spotify.com/intl-fr/show/ghi789"),
            SpotifyType.SHOW,
            "ghi789",
        ),
        (SpotifyReferenceInput(value="jkl012", type="artist"), SpotifyType.ARTIST, "jkl012"),
    ],
)
def test_reference_normalization(supplied, expected_type, expected_id) -> None:
    result = supplied.normalized()
    assert result.type == expected_type
    assert result.id == expected_id
    assert result.uri == f"spotify:{expected_type}:{expected_id}"


@pytest.mark.parametrize(
    "supplied",
    [
        SpotifyReferenceInput(value="bareid"),
        SpotifyReferenceInput(value="https://example.com/track/abc"),
        SpotifyReferenceInput(value="https://open.spotify.com/abc"),
        SpotifyReferenceInput(value="spotify:track:bad-id"),
    ],
)
def test_reference_rejects_ambiguous_or_invalid_values(supplied) -> None:
    with pytest.raises(ValueError):
        supplied.normalized()


def test_reference_rejects_disallowed_type() -> None:
    with pytest.raises(ValueError, match="must be one of"):
        SpotifyReferenceInput(value="spotify:track:abc").normalized(allowed={SpotifyType.ALBUM})


def test_strict_search_contract_and_duplicate_types() -> None:
    with pytest.raises(ValidationError, match="extra_forbidden"):
        SearchCatalogInput(query="ambient", types=["track"], typo=True)
    with pytest.raises(ValidationError, match="duplicates"):
        SearchCatalogInput(query="ambient", types=["track", "track"])
    with pytest.raises(ValidationError):
        SearchCatalogInput(query="ambient", types=["track"], limit=11)


def test_action_specific_validation() -> None:
    with pytest.raises(ValidationError, match="position_ms"):
        PlayerAction(action="seek")
    with pytest.raises(ValidationError, match="not both"):
        PlayerAction(action="start_playback", context_uri="spotify:album:a", uris=["x"])
    with pytest.raises(ValidationError, match="one offset"):
        PlayerAction(action="start_playback", offset_uri="x", offset_position=1)
    with pytest.raises(ValidationError, match="name is required"):
        PlaylistAction(action="create_playlist")
    with pytest.raises(ValidationError, match="playlist is required"):
        PlaylistAction(action="add_items", uris=[])
    with pytest.raises(ValidationError, match="range_start"):
        PlaylistAction(
            action="reorder_items",
            playlist=SpotifyReferenceInput(value="spotify:playlist:p"),
            range_start=1,
        )
    with pytest.raises(ValidationError, match="at least one detail"):
        PlaylistAction(
            action="change_playlist_details",
            playlist=SpotifyReferenceInput(value="spotify:playlist:p"),
        )
    with pytest.raises(ValidationError, match="must not be empty"):
        PlaylistAction(
            action="remove_items",
            playlist=SpotifyReferenceInput(value="spotify:playlist:p"),
            uris=[],
        )


def test_library_and_read_validation() -> None:
    with pytest.raises(ValidationError, match="does not support"):
        LibraryAction(action="follow", items=[SpotifyReferenceInput(value="spotify:track:t")])
    with pytest.raises(ValidationError, match="required for get_playlists"):
        PlaylistReadRequest(operation="get_playlists")
    with pytest.raises(ValidationError, match="mutually exclusive"):
        ListeningActivityInput(before=1, after=2)


def test_duplicate_read_sections_are_rejected() -> None:
    with pytest.raises(ValidationError, match="expansions"):
        GetItemInput(
            items=[SpotifyReferenceInput(value="spotify:album:a")],
            expansions=["album_tracks", "album_tracks"],
        )
    with pytest.raises(ValidationError, match="include"):
        PlayerStatusInput(include=["devices", "devices"])
    with pytest.raises(ValidationError, match="time_ranges"):
        ListeningActivityInput(time_ranges=["short_term", "short_term"])
