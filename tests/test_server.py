"""Initial import smoke test."""

from spotify_mcp_server.server import mcp


def test_server_is_defined() -> None:
    assert mcp is not None
