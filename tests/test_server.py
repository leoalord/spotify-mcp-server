"""Protocol-level discovery, schema, dispatch, and transport tests."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, cast

import pytest
from mcp import Client

from spotify_mcp_server.server import create_server, main, mcp
from spotify_mcp_server.tools.common import ToolResponse
from spotify_mcp_server.tools.service import SpotifyService

pytestmark = pytest.mark.anyio

TOOL_NAMES = [
    "search_catalog",
    "get_item",
    "player_status",
    "player_control",
    "playlist_read",
    "playlist_modify",
    "library_read",
    "library_modify",
    "listening_activity",
]


def load_snapshot() -> object:
    return json.loads(Path("schemas/tools.json").read_text(encoding="utf-8"))


async def test_server_discovers_current_protocol_and_exact_tool_catalog() -> None:
    async with Client(mcp) as client:
        assert client.protocol_version == "2026-07-28"
        assert client.server_info is not None
        assert client.server_info.name == "Spotify MCP Server"
        tools = await client.list_tools()

    assert [tool.name for tool in tools.tools] == TOOL_NAMES


async def test_discovery_exposes_strict_structured_contracts_and_annotations() -> None:
    tools = await mcp.list_tools()
    for tool in tools:
        assert tool.description
        request_name = tool.input_schema["properties"]["request"]["$ref"].rsplit("/", 1)[-1]
        request_schema = tool.input_schema["$defs"][request_name]
        assert request_schema["additionalProperties"] is False
        assert tool.output_schema is not None
        assert tool.output_schema["additionalProperties"] is False
        assert tool.annotations and tool.annotations.open_world_hint is True


async def test_committed_schema_snapshot_matches_discovery() -> None:
    actual = [
        tool.model_dump(by_alias=True, exclude_none=True, mode="json")
        for tool in await mcp.list_tools()
    ]
    assert actual == load_snapshot()


class RecordingService:
    def __init__(self) -> None:
        self.called: list[str] = []

    def __getattr__(self, name: str) -> Any:
        async def call(_: object) -> ToolResponse:
            self.called.append(name)
            return ToolResponse(status="ok", data={"tool": name})

        return call


async def test_every_registered_handler_validates_and_dispatches() -> None:
    service = RecordingService()
    server = create_server(cast(SpotifyService, service))
    payloads = {
        "search_catalog": {"query": "focus", "types": ["track"]},
        "get_item": {"items": [{"value": "spotify:track:t"}]},
        "player_status": {},
        "player_control": {"actions": [{"action": "pause_playback"}]},
        "playlist_read": {"requests": [{"operation": "list_current_playlists"}]},
        "playlist_modify": {"actions": [{"action": "create_playlist", "name": "x"}]},
        "library_read": {"requests": [{"operation": "list_saved", "type": "track"}]},
        "library_modify": {
            "actions": [
                {
                    "action": "save",
                    "items": [{"value": "spotify:track:t"}],
                }
            ]
        },
        "listening_activity": {},
    }

    async with Client(server) as client:
        for name, request in payloads.items():
            response = await client.call_tool(name, {"request": request})
            assert response.is_error is False
            assert response.structured_content is not None
            assert response.structured_content["status"] == "ok"
    assert service.called == TOOL_NAMES


def test_main_rejects_non_loopback_host(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MCP_HOST", "0.0.0.0")
    with pytest.raises(ValueError, match="loopback"):
        main()


def test_main_runs_stateless_streamable_http(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    def run(*, transport: str, **kwargs: object) -> None:
        captured.update(transport=transport, **kwargs)

    monkeypatch.setenv("MCP_HOST", "127.0.0.1")
    monkeypatch.setenv("MCP_PORT", "8123")
    monkeypatch.setattr(mcp, "run", run)
    main()
    assert captured == {
        "transport": "streamable-http",
        "host": "127.0.0.1",
        "port": 8123,
        "stateless_http": True,
        "json_response": True,
    }
