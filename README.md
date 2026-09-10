---
title: Spotify MCP Server
emoji: 🎧
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
license: mit
short_description: Self-hostable Spotify MCP. Bring your own Spotify app; this Space is a reference deploy.
---

# Spotify MCP Server

This is software you host yourself. Create your own Spotify developer application, then run
the server locally or deploy a copy. It is **not** a public Spotify MCP that other people can
connect to, and it is not affiliated with Spotify.

A Hugging Face Docker Space is included as a **reference** for authenticated remote hosting. That
example uses the maintainer's development-mode Spotify app, which Spotify caps at five accounts.
Do not add the Space URL as a connector in ChatGPT, Cursor, Claude, or any other client.

A self-hostable, Pydantic-first MCP v2 server that bundles Spotify Web API operations into nine
agent-friendly tools. It can run locally with operating-system keyring storage or remotely with
Scalekit OAuth 2.1, CIMD client discovery, and encrypted per-user credentials in Neon Postgres.

The server targets MCP `2026-07-28`, uses the official Python `mcp` v2 SDK, and serves stateless
Streamable HTTP. Local mode is loopback-only. Hosted mode refuses to start without OAuth, database,
and encryption configuration.

## Compared with Spotify's Claude connector

Spotify's [official Claude connector](https://claude.com/connectors/spotify) is a Claude product
integration operated by Spotify. It works in Claude products. It is not a general MCP server, so
it cannot be pointed at ChatGPT, Cursor, or other MCP clients.

This server speaks Streamable HTTP. After you run or host it with **your** Spotify client ID, any
MCP v2 client that can reach the endpoint can use it, including ChatGPT custom connectors, Cursor, and
Claude Desktop.

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

## Prompts and resources

The server exposes four curated workflows that compose the nine tools without adding another API
surface:

| Prompt | Purpose |
| --- | --- |
| `catch_up_on_podcasts` | Prioritize unfinished or unplayed episodes from saved shows |
| `weekly_music_recap` | Summarize patterns in recent plays and top music |
| `build_playlist_for_mood` | Search and create a private playlist for a mood or activity |
| `now_playing_briefing` | Produce a compact playback, device, progress, and queue summary |

The intentionally small resource catalog contains `spotify://me`. It returns the current user's
Spotify display name and stable `account_id`; dynamic playback, library, and playlist state remains
behind tools.

Podcast transcripts and inferred podcast listening history are intentionally out of scope because
Spotify does not expose them through the supported Web API. The server does not embed, train on,
download, or persist Spotify content.

## Spotify access limits

Read this before deploying. These are Spotify platform rules, not limits of this server, and nothing
here works around them.

- A Spotify application in **development mode** may authorize **at most 5 Spotify accounts**. You
  add each one by hand in the developer dashboard under Settings -> User Management, using the
  person's name and the email on their Spotify account. Accounts missing from that list fail
  authorization.
- **Extended quota mode**, which lifts the limit, has been restricted to organizations since
  15 May 2025 and requires a registered business with at least 250,000 monthly active users.
  Individual developers cannot qualify, and there is no intermediate tier.
- **Playback control requires Spotify Premium** on each user's own account. Free accounts can still
  search, read their library, and read listening history, but `player_control` requests fail.

One deployment therefore serves you plus at most four people you invite by hand. Scaling past that
requires each user to supply their own Spotify application and client ID, which this server does not
implement today; it sends every user through one shared `SPOTIFY_CLIENT_ID`.

Spotify's [quota modes](https://developer.spotify.com/documentation/web-api/concepts/quota-modes)
documentation has the current rules.

## Local setup

Prerequisites:

- Python 3.11+
- [`uv`](https://docs.astral.sh/uv/)
- A Spotify developer application (see [Spotify access limits](#spotify-access-limits))

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

The MCP endpoint is `http://127.0.0.1:8000/mcp` by default. The server rejects non-loopback
`MCP_HOST` values.

## Connect an MCP client

Point clients at **your** loopback or hosted `/mcp` URL. Do not use the maintainer Hugging Face
Space.

### Cursor

Add to `~/.cursor/mcp.json` or the project `.cursor/mcp.json`:

```json
{
  "mcpServers": {
    "spotify": {
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

### Claude Desktop and Claude Code

Claude Desktop (`claude_desktop_config.json`) and Claude Code both need an explicit HTTP transport:

```json
{
  "mcpServers": {
    "spotify": {
      "type": "http",
      "url": "http://127.0.0.1:8000/mcp"
    }
  }
}
```

Claude Code equivalent: `claude mcp add --transport http spotify http://127.0.0.1:8000/mcp`.

### ChatGPT

ChatGPT custom connectors require a public HTTPS endpoint. After you deploy **your** hosted copy,
add that `/mcp` URL in Settings -> Connectors (Developer Mode). ChatGPT cannot reach `127.0.0.1`.
Do not enter the maintainer Space URL.

Hosted clients complete Scalekit OAuth first, then connect Spotify when a tool returns the
`/spotify/connect` link.

## Hosted deployment (reference)

The included Dockerfile is configured for a Hugging Face Docker Space on port `7860`. Use it to
deploy **your own** Space, Scalekit environment, Neon database, and Spotify application. The public
Space at [LeoWalker/spotify-mcp-server](https://huggingface.co/spaces/LeoWalker/spotify-mcp-server)
shows that this pattern works; it is not an open MCP service.

Hosted mode uses Scalekit as the MCP authorization server (not WorkOS AuthKit) and creates a
single `spotify_credentials` table in Neon. The table contains a Scalekit subject, an encrypted
Spotify refresh token, and an update timestamp. Spotify access tokens and Spotify content are not
persisted.

Set `MCP_ALLOWED_SUBJECTS` on every hosted instance so only your Scalekit user IDs can start Spotify
OAuth against your shared client ID.

1. Create a Spotify developer application and register this redirect URI. Add every permitted
   Spotify account under Settings -> User Management; development mode allows at most 5, including
   your own, and unlisted accounts cannot authorize:

   ```text
   https://<space-owner>-<space-name>.hf.space/spotify/callback
   ```

2. In Scalekit, register `https://<space-owner>-<space-name>.hf.space/mcp` as the MCP Server URL and
   enable CIMD. Configure DCR separately only if compatibility with a non-CIMD client is required.
3. Create a Neon database and use its pooled connection string for `DATABASE_URL`.
4. Generate a stable encryption key once:

   ```bash
   uv run python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

5. Configure the Space variables and secrets listed below, then deploy this repository to the
   Docker Space. The remote MCP endpoint is the same URL registered in Scalekit.

After a user authorizes the MCP client through Scalekit, their first Spotify tool request returns a
short-lived `/spotify/connect` link. They authorize the shared Spotify developer application with
PKCE, return to the MCP client, and retry the request. Every user authorizes the same shared
application, so the 5-account development-mode limit above applies to the deployment as a whole.

## Configuration

| Variable | Default | Purpose |
| --- | --- | --- |
| `SPOTIFY_CLIENT_ID` | — | Public Spotify application client ID; required for authorization |
| `SPOTIFY_REDIRECT_URI` | `http://127.0.0.1:8765/callback` | Registered loopback OAuth callback |
| `SPOTIFY_API_BASE_URL` | `https://api.spotify.com/v1` | Spotify API base; primarily useful in tests |
| `SPOTIFY_ACCOUNTS_BASE_URL` | `https://accounts.spotify.com` | Spotify OAuth base; primarily useful in tests |
| `MCP_HOST` | `127.0.0.1` | Loopback bind address only |
| `MCP_PORT` | `8000` | Local Streamable HTTP port |

Hosted mode sets `MCP_DEPLOYMENT_MODE=hosted` in the Docker image and additionally requires:

| Variable | Storage | Purpose |
| --- | --- | --- |
| `MCP_SERVER_URL` | Variable | Exact public endpoint ending in `/mcp`; also the validated token audience |
| `SCALEKIT_ENVIRONMENT_URL` | Secret or variable | Scalekit environment issuer URL |
| `SCALEKIT_RESOURCE_ID` | Variable | Scalekit MCP resource ID beginning with `res_` |
| `DATABASE_URL` | Secret | Neon pooled PostgreSQL connection string |
| `TOKEN_ENCRYPTION_KEY` | Secret | Stable base64 key used to derive separate refresh-token and OAuth-state keys |
| `SPOTIFY_CLIENT_ID` | Secret or variable | Public client ID of the hosted Spotify developer application |
| `MCP_ALLOWED_SUBJECTS` | Variable | Comma-separated Scalekit user IDs allowed to connect Spotify; set this on every hosted instance |

Spotify OAuth credentials are never returned from MCP tools. Access tokens remain memory-only, and
refresh tokens are stored either by the operating system credential backend or encrypted in Neon.
If `MCP_ALLOWED_SUBJECTS` is unset, any user whom your Scalekit and Spotify configurations admit may
connect.

The server validates Scalekit access tokens locally against the public signing keys at
`<SCALEKIT_ENVIRONMENT_URL>/keys`; Scalekit client credentials are not required by the runtime.

## Development

```bash
uv run ruff check .
uv run ruff format --check .
uv run pytest
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

## License

MIT. Spotify is a trademark of Spotify AB. This project is unofficial and is not endorsed by
Spotify.
