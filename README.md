---
title: Spotify MCP Server
emoji: 🎧
colorFrom: green
colorTo: gray
sdk: docker
app_port: 7860
license: mit
---

# Spotify MCP Server

A self-hostable, Pydantic-first MCP v2 server that bundles Spotify Web API operations into nine
agent-friendly tools. It can run locally with operating-system keyring storage or remotely with
Scalekit OAuth 2.1, CIMD client discovery, and encrypted per-user credentials in Neon Postgres.

The server targets MCP `2026-07-28`, uses the official Python `mcp` v2 SDK, and serves stateless
Streamable HTTP. Local mode is loopback-only. Hosted mode refuses to start without OAuth, database,
and encryption configuration.

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

The MCP endpoint is `http://127.0.0.1:8000/mcp` by default. Configure an MCP v2 client to use that
Streamable HTTP URL. The server rejects non-loopback `MCP_HOST` values.

## Hosted deployment

The included Dockerfile is configured for a Hugging Face Docker Space on port `7860`. Hosted mode
uses Scalekit as the MCP authorization server and creates a single `spotify_credentials` table in
Neon. The table contains a Scalekit subject, an encrypted Spotify refresh token, and an update
timestamp. Spotify access tokens and Spotify content are not persisted.

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
| `MCP_ALLOWED_SUBJECTS` | Variable, optional | Comma-separated Scalekit user IDs allowed to connect Spotify |

Spotify OAuth credentials are never returned from MCP tools. Access tokens remain memory-only, and
refresh tokens are stored either by the operating system credential backend or encrypted in Neon.
If `MCP_ALLOWED_SUBJECTS` is unset, any user whom your Scalekit and Spotify configurations admit may
connect; set it for a server-side allowlist.

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

MIT
