# Spotify MCP Server

A personal-use Model Context Protocol (MCP) server for Spotify, designed around a small set of consolidated tools for music, podcasts, audiobooks, playback, playlists, library management, and listening history.

> Status: early scaffold. The server shell exists; Spotify authentication and product tools are not implemented yet.

## Product direction

The project targets:

- MCP specification `2026-07-28`
- The official Python `mcp` v2 SDK
- Spotify's post-February 2026 Web API surface
- Stateless Streamable HTTP bound to `127.0.0.1`
- Graceful text/JSON fallbacks for MCP Apps-enabled tools

The planned v1 surface contains eight consolidated tools:

1. `search_catalog`
2. `get_item`
3. `player_status`
4. `player_control`
5. `playlist_manage`
6. `library_manage`
7. `listening_history`
8. `podcast_progress`

See the [Spotify MCP Server PRD](https://app.notion.com/p/Spotify-MCP-Server-PRD-3b72bade7f3181d38201dc480d7c5d3c) for requirements, constraints, and the build sequence.

## Repository structure

```text
.
├── src/spotify_mcp_server/
│   ├── __init__.py
│   └── server.py
├── tests/
│   └── test_server.py
├── .env.example
├── .gitignore
├── pyproject.toml
└── README.md
```

## Getting started

Prerequisites:

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/) (recommended)
- A Spotify developer application

Create the environment and install the package:

```bash
uv sync --extra dev
cp .env.example .env
```

Start the local Streamable HTTP server:

```bash
uv run spotify-mcp-server
```

The MCP endpoint is available at `http://127.0.0.1:8000/mcp`.

Run the initial smoke test:

```bash
uv run pytest
```

## Configuration

Copy `.env.example` to `.env` and fill in local values as features are implemented. Never commit Spotify credentials, OAuth tokens, or the local MCP bearer token.

Spotify refresh tokens will be stored in the OS keychain or an encrypted file. Access tokens will remain in memory only. Spotify OAuth credentials and MCP client authentication are separate security boundaries and must never share tokens.

## Initial roadmap

- Verify `server/discover` for MCP `2026-07-28`
- Add the shared Spotify HTTP client, pagination, and retry handling
- Implement Spotify Authorization Code + PKCE
- Build the read-only tool slice
- Ship `podcast_progress` and `catch_up_on_podcasts` as the first demoable workflow
- Add write tools, prompts, the `spotify://me` resource, and MCP Apps widgets

## Development notes

- Use Python logging, which writes to stderr; do not use `print()` in server code.
- Do not add Spotify endpoints removed in the November 2024 or February 2026 API changes.
- Do not add deprecated MCP primitives such as Roots, Sampling, or server-initiated protocol logging.
- Keep tool results useful as structured text/JSON even when the client does not support MCP Apps.
