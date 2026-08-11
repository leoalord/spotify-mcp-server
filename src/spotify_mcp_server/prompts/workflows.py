"""Prompt text for the four curated Spotify workflows."""

from __future__ import annotations

from typing import Annotated

from mcp.server.mcpserver import MCPServer
from pydantic import Field


def catch_up_on_podcasts(
    show_filter: Annotated[
        str | None,
        Field(description="Optional show name or topic to limit the podcast catch-up."),
    ] = None,
) -> str:
    """Prioritize unfinished or unplayed episodes from the user's Spotify library."""

    scope = f" Limit the review to shows matching {show_filter!r}." if show_filter else ""
    return (
        "Help me catch up on podcasts using Spotify data only."
        f"{scope} Use library_read to retrieve saved shows and episodes, then use get_item with "
        "show_episodes expansion for relevant shows when more episode context is needed. Treat "
        "missing completion or resume-position data as unknown, not unplayed. Rank a concise "
        "shortlist using available release dates, durations, descriptions, and progress. Explain "
        "that Spotify provides neither transcripts nor complete chronological podcast history, "
        "and do not imply knowledge beyond the returned metadata."
    )


def weekly_music_recap() -> str:
    """Turn Spotify's available recent and top music data into a weekly-style recap."""

    return (
        "Create a concise music recap from Spotify. Call listening_activity for recent tracks and "
        "short-, medium-, and long-term top tracks and artists. Identify repetitions, contrasts, "
        "and changes that are directly supported by the returned ordering and timestamps. Do not "
        "describe the data as a complete week of listening, invent play counts, or include podcast "
        "history. Clearly distinguish recent plays from Spotify's longer-term affinity lists."
    )


def build_playlist_for_mood(
    mood: Annotated[
        str,
        Field(description="Mood, setting, or activity the new playlist should match."),
    ],
) -> str:
    """Build a Spotify playlist for a described mood, setting, or activity."""

    return (
        f"Build a Spotify playlist for this mood or activity: {mood!r}. Use search_catalog with "
        "several focused keyword queries and bounded pagination to assemble candidates. You may "
        "use listening_activity to align choices with my established taste. Do not claim to use "
        "Spotify recommendations, audio features, or similarity vectors because those surfaces are "
        "not available. Choose a coherent ordered track list, then use one playlist_modify plan to "
        "create a private playlist and add the tracks. Report the created playlist and final track "
        "list, including any partial-result warnings."
    )


def now_playing_briefing() -> str:
    """Provide a compact snapshot of the current Spotify playback state."""

    return (
        "Give me a compact now-playing briefing. Call player_status with its default complete "
        "snapshot, then summarize the active item, playback state, progress, device, and next "
        "queue items when present. If nothing is playing or a section is unavailable, say so "
        "directly and preserve any partial-result warning."
    )


def register_prompts(server: MCPServer) -> None:
    """Register the approved curated workflows on an MCP server."""

    server.prompt(
        name="catch_up_on_podcasts",
        title="Catch up on podcasts",
        description="Prioritize unfinished or unplayed episodes from saved Spotify podcasts.",
    )(catch_up_on_podcasts)
    server.prompt(
        name="weekly_music_recap",
        title="Weekly music recap",
        description="Summarize patterns in Spotify's recent plays and top music affinity.",
    )(weekly_music_recap)
    server.prompt(
        name="build_playlist_for_mood",
        title="Build a playlist for a mood",
        description="Search Spotify and create a private playlist for a mood or activity.",
    )(build_playlist_for_mood)
    server.prompt(
        name="now_playing_briefing",
        title="Now-playing briefing",
        description="Summarize current playback, device, progress, and queue in a compact form.",
    )(now_playing_briefing)
