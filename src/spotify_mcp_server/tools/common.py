"""Shared strict request and response contracts for Spotify MCP tools."""

from __future__ import annotations

import re
from enum import StrEnum
from typing import Any
from urllib.parse import urlparse

from pydantic import BaseModel, ConfigDict, Field


class StrictModel(BaseModel):
    """Base for MCP-owned structures: reject misspelled and unknown fields."""

    model_config = ConfigDict(extra="forbid", use_enum_values=True)


class SpotifyType(StrEnum):
    ALBUM = "album"
    ARTIST = "artist"
    AUDIOBOOK = "audiobook"
    CHAPTER = "chapter"
    EPISODE = "episode"
    PLAYLIST = "playlist"
    SHOW = "show"
    TRACK = "track"


class SpotifyReferenceInput(StrictModel):
    """A Spotify ID, URI, or open.spotify.com URL plus type when it cannot be inferred."""

    value: str = Field(min_length=1, description="Spotify ID, URI, or open.spotify.com URL.")
    type: SpotifyType | None = Field(
        default=None, description="Required only when value is a bare Spotify ID."
    )

    def normalized(self, *, allowed: set[SpotifyType] | None = None) -> SpotifyReference:
        value = self.value.strip()
        item_type = self.type
        item_id = value

        uri_match = re.fullmatch(r"spotify:([a-z]+):([^:?/]+)", value)
        if uri_match:
            item_type = SpotifyType(uri_match.group(1))
            item_id = uri_match.group(2)
        elif value.startswith("http://") or value.startswith("https://"):
            parsed = urlparse(value)
            if parsed.hostname not in {"open.spotify.com", "www.open.spotify.com"}:
                raise ValueError("Spotify URLs must use open.spotify.com")
            parts = [part for part in parsed.path.split("/") if part]
            if parts and parts[0].startswith("intl-"):
                parts = parts[1:]
            if len(parts) < 2:
                raise ValueError("Spotify URL must contain an item type and ID")
            item_type = SpotifyType(parts[0])
            item_id = parts[1]
        elif item_type is None:
            raise ValueError("type is required when value is a bare Spotify ID")

        if not re.fullmatch(r"[A-Za-z0-9]+", item_id):
            raise ValueError("Spotify ID contains invalid characters")
        if allowed is not None and item_type not in allowed:
            values = ", ".join(sorted(allowed_type.value for allowed_type in allowed))
            raise ValueError(f"Spotify item type must be one of: {values}")
        assert item_type is not None
        return SpotifyReference(id=item_id, type=item_type, uri=f"spotify:{item_type}:{item_id}")


class SpotifyReference(StrictModel):
    id: str
    type: SpotifyType
    uri: str


class OffsetPaging(StrictModel):
    limit: int = Field(default=20, ge=1, le=50)
    offset: int = Field(default=0, ge=0)
    max_pages: int = Field(default=1, ge=1, le=10)


class ContractWarning(StrictModel):
    code: str
    message: str
    request_index: int | None = None
    action_index: int | None = None
    retry_after_seconds: float | None = None


class ToolResponse(StrictModel):
    """Common structured result. Partial successes always retain usable Spotify data."""

    status: str = Field(pattern="^(ok|partial|error)$")
    data: dict[str, Any] = Field(default_factory=dict)
    warnings: list[ContractWarning] = Field(default_factory=list)


class FailureMode(StrEnum):
    STOP = "stop"
    CONTINUE = "continue"


class PriorActionReference(StrictModel):
    """Reference an object produced by an earlier action in the same ordered request."""

    action_index: int = Field(ge=0)


def one_of(*values: Any) -> int:
    """Return the number of non-None alternatives for model validators."""

    return sum(value is not None for value in values)
