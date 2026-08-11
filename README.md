# Spotify MCP Server

A personal, Pydantic-first MCP v2 server that bundles Spotify Web API operations into nine
agent-friendly tools. It preserves Spotify object shapes, filters formally deprecated fields,
returns usable partial results with structured warnings, and keeps reads frictionless while making
writes explicit and bounded.

The server targets MCP `2026-07-28`, uses the official Python `mcp` v2 SDK, and serves stateless
Streamable HTTP on loopback only.

## Tools

| Tool | Purpose | Side effects |
| --- | --- | --- |
| `search_catalog` | Search albums, artists, playlists, tracks, shows, episodes, and audiobooks | None |
| `get_item` | Fetch heterogeneous items with natural child expansions | None |
| `player_status` | Fetch playback, devices, and queue together | None |
| `player_control` | Run up to 20 ordered playback actions | Changes playback |
| `playlist_read` | List playlists and retrieve owned/collaborative contents | None |
| `playlist_modify` | Create and mutate playlists in an ordered batch | Changes playlists |
| `library_read` | Read saved items, followed artists, and membership | None |
| `library_modify` | Save/remove/follow/unfollow up to 40 URIs per action | Changes library |
| `listening_activity` | Read recent tracks and top tracks/artists | None |

Podcast transcripts and inferred podcast listening history are intentionally out of scope because
Spotify does not expose them through the supported Web API. The server does not embed, train on,
download, or persist Spotify content.

## Setup

Prerequisites:

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A Spotify developer application

1. In the Spotify developer dashboard, register `http://127.0.0.1:8765/callback` as a redirect URI.
2. Install the locked project environment and create local configuration:

   ```bash
   uv sync --locked --extra dev
   cp .env.example .env
   ```

3. Set `SPOTIFY_CLIENT_ID` in `.env` or your process environment.
4. Authorize once; the refresh token is stored in the operating system keychain while access tokens
   remain in memory:

   ```bash
   uv run spotify-mcp-auth
   ```

5. Start the loopback server:

   ```bash
   uv run spotify-mcp-server
   ```

The MCP endpoint is `http://127.0.0.1:8000/mcp` by default. Configure an MCP v2 client to use that
Streamable HTTP URL. The server rejects non-loopback `MCP_HOST` values.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPOTIFY_CLIENT_ID` | — | Public Spotify application client ID; required for authorization |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8765/callback` | Registered loopback OAuth callback |
| `SPOTIFY_API_BASE_URL` | `https://api.spotify.com/v1` | Spotify API base; primarily useful in tests |
| `SPOTIFY_ACCOUNTS_BASE_URL` | `https://accounts.spotify.com` | Spotify OAuth base; primarily useful in tests |
| `MCP_HOST` | `127.0.0.1` | Loopback bind address only |
| `MCP_PORT` | `8000` | Local Streamable HTTP port |

Spotify OAuth credentials are never returned from MCP tools. Access tokens remain memory-only, and
refresh tokens are stored by the operating system credential backend.

## Development

```bash
uv run --frozen ruff check .
uv run --frozen ruff format --check .
uv run --frozen pytest
```

Tests use mocked Spotify HTTP responses and the MCP in-memory client; they do not require a Spotify
account or make network calls. A live smoke test additionally needs an authorized account and, for
playback controls, an active Spotify device and any account capabilities Spotify requires.

## API compatibility and policy

The HTTP client enforces an explicit allowlist audited against Spotify's post-February 2026 Web API
surface. Deprecated bulk and type-specific mutation endpoints are rejected before a request is
sent. Request limits mirror Spotify's published limits. A `429` response is retried within a small
bounded budget and, if still unsuccessful, is returned as a structured warning alongside any
successful partial results.

Before distribution, recheck Spotify's current Developer Terms for the intended MCP host and LLM
runtime. Spotify content must not be used to train or fine-tune a model, and this server provides no
long-lived content cache or embeddings.
