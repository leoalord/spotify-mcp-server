from __future__ import annotations

import httpx2
import pytest

import spotify_mcp_server.spotify.client as client_module
from spotify_mcp_server.spotify.client import (
    SpotifyAPIError,
    SpotifyClient,
    _retry_after,
    sanitize_spotify_payload,
)

pytestmark = pytest.mark.anyio


class Tokens:
    def __init__(self) -> None:
        self.calls: list[bool] = []

    async def access_token(self, *, force_refresh: bool = False) -> str:
        self.calls.append(force_refresh)
        return "refreshed" if force_refresh else "initial"


def test_sanitize_preserves_shapes_and_removes_deprecated_fields() -> None:
    payload = {
        "type": "playlist",
        "tracks": {"items": []},
        "items": [
            {
                "type": "track",
                "name": "Song",
                "available_markets": ["US"],
                "external_ids": {"isrc": "x"},
                "album": {"type": "album", "label": "label", "name": "Album"},
            }
        ],
    }
    assert sanitize_spotify_payload(payload) == {
        "type": "playlist",
        "items": [
            {
                "type": "track",
                "name": "Song",
                "external_ids": {"isrc": "x"},
                "album": {"type": "album", "name": "Album"},
            }
        ],
    }
    assert sanitize_spotify_payload({"type": "user", "followers": {"total": 3}}) == {
        "type": "user",
        "followers": {"total": 3},
    }


async def test_client_blocks_deprecated_or_unapproved_operation() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(204))
    ) as http:
        client = SpotifyClient(Tokens(), client=http)
        with pytest.raises(ValueError, match="not in the audited allowlist"):
            await client.request("PUT", "/me/tracks", params={"ids": "legacy"})


async def test_request_refresh_retry_and_no_content() -> None:
    seen = 0

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal seen
        seen += 1
        if seen == 1:
            return httpx2.Response(401, json={"error": {"message": "expired"}})
        assert request.headers["Authorization"] == "Bearer refreshed"
        return httpx2.Response(204)

    tokens = Tokens()
    transport = httpx2.MockTransport(handler)
    async with httpx2.AsyncClient(transport=transport) as http:
        client = SpotifyClient(tokens, client=http, max_retries=0)
        assert await client.request("PUT", "/me/player/pause") is None
    assert tokens.calls == [False, True]


async def test_request_retries_rate_limit_then_returns_payload() -> None:
    sleeps: list[float] = []
    seen = 0

    async def handler(_: httpx2.Request) -> httpx2.Response:
        nonlocal seen
        seen += 1
        if seen == 1:
            return httpx2.Response(429, headers={"Retry-After": "0"}, json={"error": {}})
        return httpx2.Response(200, json={"type": "track", "name": "ok", "popularity": 100})

    async def sleep(value: float) -> None:
        sleeps.append(value)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SpotifyClient(Tokens(), client=http, max_retries=1, sleep=sleep)
        assert await client.request("GET", "/tracks/x") == {"type": "track", "name": "ok"}
    assert sleeps == [0.0]


async def test_request_raises_structured_error() -> None:
    async def handler(_: httpx2.Request) -> httpx2.Response:
        return httpx2.Response(
            429,
            headers={"Retry-After": "nonsense"},
            json={"error": {"message": "slow", "reason": "QUOTA_EXCEEDED"}},
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SpotifyClient(Tokens(), client=http, max_retries=0)
        with pytest.raises(SpotifyAPIError) as raised:
            await client.request("GET", "/search")
    assert raised.value.reason == "QUOTA_EXCEEDED"
    assert raised.value.retry_after_seconds is None


def test_retry_after_parses_http_date(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(client_module.time, "time", lambda: 1_000.0)
    response = httpx2.Response(429, headers={"Retry-After": "Thu, 01 Jan 1970 00:17:10 GMT"})
    assert _retry_after(response) == 30.0


async def test_request_retries_network_error_then_structures_failure() -> None:
    attempts = 0
    sleeps: list[float] = []

    async def handler(request: httpx2.Request) -> httpx2.Response:
        nonlocal attempts
        attempts += 1
        raise httpx2.ConnectError("offline", request=request)

    async def sleep(value: float) -> None:
        sleeps.append(value)

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SpotifyClient(Tokens(), client=http, max_retries=1, sleep=sleep)
        with pytest.raises(SpotifyAPIError, match="network request failed") as raised:
            await client.request("GET", "/tracks/x")
    assert raised.value.status_code == 0
    assert attempts == 2
    assert sleeps == [0.5]


async def test_paged_combines_items_and_honors_budget() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        offset = request.url.params.get("offset", "0")
        if offset == "0":
            return httpx2.Response(
                200,
                json={
                    "items": [{"id": "one"}],
                    "next": "https://api.spotify.com/v1/me/tracks?offset=1",
                    "total": 3,
                },
            )
        return httpx2.Response(
            200,
            json={
                "items": [{"id": "two"}],
                "next": "https://api.spotify.com/v1/me/tracks?offset=2",
                "total": 3,
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SpotifyClient(Tokens(), client=http)
        page = await client.paged("/me/tracks", params={"offset": 0}, max_pages=2)
    assert [item["id"] for item in page["items"]] == ["one", "two"]
    assert page["pages_fetched"] == 2
    assert page["next"].endswith("offset=2")


async def test_paged_combines_nested_search_containers() -> None:
    async def handler(request: httpx2.Request) -> httpx2.Response:
        offset = request.url.params.get("offset", "0")
        return httpx2.Response(
            200,
            json={
                "albums": {
                    "items": [{"id": offset}],
                    "next": (
                        "https://api.spotify.com/v1/search?q=x&type=album&offset=1"
                        if offset == "0"
                        else None
                    ),
                }
            },
        )

    async with httpx2.AsyncClient(transport=httpx2.MockTransport(handler)) as http:
        client = SpotifyClient(Tokens(), client=http)
        page = await client.paged(
            "/search",
            params={"q": "x", "type": "album", "offset": 0},
            max_pages=2,
            container_key="albums",
        )
    assert [item["id"] for item in page["items"]] == ["0", "1"]
    assert page["pages_fetched"] == 2
    assert page["next"] is None


async def test_paged_normalizes_missing_initial_container() -> None:
    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: httpx2.Response(200, json={}))
    ) as http:
        client = SpotifyClient(Tokens(), client=http)
        with pytest.raises(SpotifyAPIError, match=r"albums.*paging object"):
            await client.paged(
                "/search", params={"q": "x", "type": "album"}, max_pages=1, container_key="albums"
            )


async def test_paged_normalizes_missing_next_container() -> None:
    responses = [
        httpx2.Response(
            200,
            json={
                "albums": {
                    "items": [{"id": "one"}],
                    "next": "https://api.spotify.com/v1/search?offset=1",
                }
            },
        ),
        httpx2.Response(200, json={}),
    ]

    async with httpx2.AsyncClient(
        transport=httpx2.MockTransport(lambda _: responses.pop(0))
    ) as http:
        client = SpotifyClient(Tokens(), client=http)
        with pytest.raises(SpotifyAPIError, match=r"albums.*paging object"):
            await client.paged(
                "/search", params={"q": "x", "type": "album"}, max_pages=2, container_key="albums"
            )
