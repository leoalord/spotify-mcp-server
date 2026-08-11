"""Async Spotify Web API client with bounded retries and payload compatibility filtering."""

from __future__ import annotations

import asyncio
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from typing import Any, Protocol

import httpx2

DEPRECATED_FIELDS_BY_TYPE = {
    "album": {"album_group", "available_markets", "genres", "label", "popularity"},
    "artist": {"followers", "genres", "popularity"},
    "audiobook": {"available_markets", "publisher"},
    "chapter": {"audio_preview_url", "available_markets"},
    "episode": {"audio_preview_url", "language"},
    "playlist": {"tracks"},
    "show": {"available_markets", "publisher"},
    "track": {"available_markets", "linked_from", "popularity", "preview_url"},
}

# This is deliberately narrower than Spotify's API. It is the audited Phase 1 surface and blocks
# accidental reintroduction of deprecated bulk or type-specific mutation endpoints.
ALLOWED_OPERATIONS: tuple[tuple[str, re.Pattern[str]], ...] = tuple(
    (method, re.compile(pattern))
    for method, pattern in (
        ("GET", r"/search"),
        ("GET", r"/me"),
        ("GET", r"/(tracks|albums|artists|shows|episodes|audiobooks|chapters)/[^/]+"),
        ("GET", r"/albums/[^/]+/tracks"),
        ("GET", r"/artists/[^/]+/albums"),
        ("GET", r"/shows/[^/]+/episodes"),
        ("GET", r"/audiobooks/[^/]+/chapters"),
        ("GET", r"/me/player"),
        ("PUT", r"/me/player"),
        ("GET", r"/me/player/(devices|queue|recently-played)"),
        ("PUT", r"/me/player/(play|pause|seek|repeat|volume|shuffle)"),
        ("POST", r"/me/player/(next|previous|queue)"),
        ("GET", r"/me/playlists"),
        ("POST", r"/me/playlists"),
        ("GET", r"/playlists/[^/]+"),
        ("PUT", r"/playlists/[^/]+"),
        ("GET", r"/playlists/[^/]+/items"),
        ("POST", r"/playlists/[^/]+/items"),
        ("PUT", r"/playlists/[^/]+/items"),
        ("DELETE", r"/playlists/[^/]+/items"),
        ("GET", r"/me/(tracks|albums|shows|episodes|audiobooks)"),
        ("GET", r"/me/following"),
        ("GET", r"/me/library/contains"),
        ("PUT", r"/me/library"),
        ("DELETE", r"/me/library"),
        ("GET", r"/me/top/(tracks|artists)"),
    )
)


class TokenProvider(Protocol):
    async def access_token(self, *, force_refresh: bool = False) -> str: ...


@dataclass(slots=True)
class SpotifyAPIError(RuntimeError):
    status_code: int
    message: str
    reason: str | None = None
    retry_after_seconds: float | None = None

    def __str__(self) -> str:
        return f"Spotify API error {self.status_code}: {self.message}"


def sanitize_spotify_payload(value: Any) -> Any:
    """Remove formally deprecated fields while preserving Spotify's object structure."""

    if isinstance(value, list):
        return [sanitize_spotify_payload(item) for item in value]
    if isinstance(value, dict):
        cleaned: dict[str, Any] = {}
        item_type = value.get("type")
        deprecated = (
            DEPRECATED_FIELDS_BY_TYPE.get(item_type, set()) if isinstance(item_type, str) else set()
        )
        for key, item in value.items():
            if key in deprecated:
                continue
            cleaned[key] = sanitize_spotify_payload(item)
        return cleaned
    return value


class SpotifyClient:
    """Small allowlisted HTTP client used by bundled MCP operations."""

    def __init__(
        self,
        token_provider: TokenProvider,
        *,
        base_url: str = "https://api.spotify.com/v1",
        client: httpx2.AsyncClient | None = None,
        max_retries: int = 3,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.token_provider = token_provider
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx2.AsyncClient(timeout=30)
        self.max_retries = max_retries
        self.sleep = sleep
        self._owns_client = client is None

    async def request(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json: dict[str, Any] | None = None,
    ) -> Any:
        _ensure_allowed(method, path)
        token = await self.token_provider.access_token()
        refreshed = False
        retry_count = 0
        while True:
            try:
                response = await self.client.request(
                    method,
                    f"{self.base_url}{path}",
                    params={
                        key: value for key, value in (params or {}).items() if value is not None
                    },
                    json=json,
                    headers={"Authorization": f"Bearer {token}"},
                )
            except httpx2.RequestError as exc:
                if retry_count < self.max_retries:
                    await self.sleep(0.5 * 2**retry_count)
                    retry_count += 1
                    continue
                raise SpotifyAPIError(0, f"Spotify network request failed: {exc}") from exc
            if response.status_code == 401 and not refreshed:
                token = await self.token_provider.access_token(force_refresh=True)
                refreshed = True
                continue
            retry_after = _retry_after(response)
            if response.status_code == 429 or response.status_code >= 500:
                if retry_count < self.max_retries:
                    await self.sleep(
                        retry_after if retry_after is not None else 0.5 * 2**retry_count
                    )
                    retry_count += 1
                    continue
            if response.is_error:
                raise _api_error(response, retry_after)
            if response.status_code == 204 or not response.content:
                return None
            return sanitize_spotify_payload(response.json())

    async def paged(
        self,
        path: str,
        *,
        params: dict[str, Any],
        max_pages: int,
        container_key: str | None = None,
    ) -> dict[str, Any]:
        """Follow Spotify `next` URLs up to the caller's bounded page budget."""

        page = await self.request("GET", path, params=params)
        container = _paging_container(page, container_key)
        combined = dict(container)
        combined["items"] = list(container.get("items", []))
        pages_fetched = 1
        next_url = container.get("next")
        while next_url and pages_fetched < max_pages:
            parsed = httpx2.URL(next_url)
            next_page = await self.request(
                "GET", parsed.path.removeprefix("/v1"), params=dict(parsed.params.multi_items())
            )
            next_container = _paging_container(next_page, container_key)
            combined["items"].extend(next_container.get("items", []))
            combined["next"] = next_container.get("next")
            pages_fetched += 1
            next_url = next_container.get("next")
        combined["pages_fetched"] = pages_fetched
        return combined

    async def aclose(self) -> None:
        if self._owns_client:
            await self.client.aclose()


def _retry_after(response: httpx2.Response) -> float | None:
    value = response.headers.get("Retry-After")
    if value is None:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            return max(0.0, parsedate_to_datetime(value).timestamp() - time.time())
        except (TypeError, ValueError, OverflowError):
            return None


def _paging_container(page: Any, container_key: str | None) -> dict[str, Any]:
    if not isinstance(page, dict):
        raise SpotifyAPIError(502, "Spotify returned no paging object")
    container = page.get(container_key) if container_key else page
    name = f"{container_key!r} " if container_key else ""
    if not isinstance(container, dict):
        raise SpotifyAPIError(502, f"Spotify response is missing the {name}paging object")
    if not isinstance(container.get("items"), list):
        raise SpotifyAPIError(502, f"Spotify {name}paging object is missing an items list")
    return container


def _ensure_allowed(method: str, path: str) -> None:
    normalized_method = method.upper()
    if any(
        normalized_method == allowed_method and pattern.fullmatch(path)
        for allowed_method, pattern in ALLOWED_OPERATIONS
    ):
        return
    raise ValueError(
        f"Spotify operation is not in the audited allowlist: {normalized_method} {path}"
    )


def _api_error(response: httpx2.Response, retry_after: float | None) -> SpotifyAPIError:
    message = response.text or response.reason_phrase
    reason = None
    try:
        payload = response.json()
        error = payload.get("error", payload)
        if isinstance(error, dict):
            message = str(error.get("message", message))
            reason = error.get("reason")
    except ValueError:
        pass
    return SpotifyAPIError(response.status_code, message, reason, retry_after)
