"""Environment and filesystem configuration."""

from __future__ import annotations

import os
from dataclasses import dataclass

SCOPES = (
    "user-read-private",
    "user-read-playback-state",
    "user-read-currently-playing",
    "user-modify-playback-state",
    "playlist-read-private",
    "playlist-read-collaborative",
    "playlist-modify-private",
    "playlist-modify-public",
    "user-library-read",
    "user-library-modify",
    "user-follow-read",
    "user-follow-modify",
    "user-read-playback-position",
    "user-top-read",
    "user-read-recently-played",
)


@dataclass(frozen=True, slots=True)
class Settings:
    client_id: str
    redirect_uri: str
    api_base_url: str = "https://api.spotify.com/v1"
    accounts_base_url: str = "https://accounts.spotify.com"
    keyring_service: str = "spotify-mcp-server"
    max_retries: int = 3
    host: str = "127.0.0.1"
    port: int = 8000

    @classmethod
    def from_env(cls) -> Settings:
        return cls(
            client_id=os.environ.get("SPOTIFY_CLIENT_ID", ""),
            redirect_uri=os.environ.get("SPOTIFY_REDIRECT_URI", "http://127.0.0.1:8765/callback"),
            api_base_url=os.environ.get("SPOTIFY_API_BASE_URL", "https://api.spotify.com/v1"),
            accounts_base_url=os.environ.get(
                "SPOTIFY_ACCOUNTS_BASE_URL", "https://accounts.spotify.com"
            ),
            keyring_service=os.environ.get("SPOTIFY_KEYRING_SERVICE", "spotify-mcp-server"),
            host=os.environ.get("MCP_HOST", "127.0.0.1"),
            port=int(os.environ.get("MCP_PORT", "8000")),
        )
