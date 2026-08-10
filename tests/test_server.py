"""Protocol-level tests for the server shell."""

import pytest
from mcp import Client

from spotify_mcp_server.server import mcp

pytestmark = pytest.mark.anyio


async def test_server_discovers_current_protocol() -> None:
    async with Client(mcp) as client:
        assert client.protocol_version == "2026-07-28"
        assert client.server_info is not None
        assert client.server_info.name == "Spotify MCP Server"


async def test_server_starts_with_no_product_tools() -> None:
    async with Client(mcp) as client:
        tools = await client.list_tools()

    assert tools.tools == []
