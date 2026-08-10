"""Application entry point for the Spotify MCP server."""

from mcp.server.mcpserver import MCPServer

mcp = MCPServer(
    "Spotify MCP Server",
    version="0.1.0",
    instructions=(
        "Personal Spotify tools for catalog search, playback, playlists, library, "
        "listening history, and podcast progress."
    ),
)


def main() -> None:
    """Run the loopback-only, stateless Streamable HTTP server."""
    mcp.run(
        transport="streamable-http",
        host="127.0.0.1",
        port=8000,
        stateless_http=True,
        json_response=True,
    )


if __name__ == "__main__":
    main()
