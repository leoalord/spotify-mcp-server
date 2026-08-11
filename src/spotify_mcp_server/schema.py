"""Export deterministic MCP tool discovery schemas for review and compatibility tests."""

from __future__ import annotations

import argparse
import asyncio
import json
from pathlib import Path

from spotify_mcp_server.server import mcp


def _write(path: Path, payload: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


async def export(path: Path) -> None:
    tools = await mcp.list_tools()
    payload = [tool.model_dump(by_alias=True, exclude_none=True, mode="json") for tool in tools]
    await asyncio.to_thread(_write, path, payload)


def main() -> None:
    parser = argparse.ArgumentParser(description="Export the server's MCP tool schemas.")
    parser.add_argument("path", nargs="?", default="schemas/tools.json")
    args = parser.parse_args()
    asyncio.run(export(Path(args.path)))
