"""Shared pytest configuration."""

from __future__ import annotations

import pytest


@pytest.fixture
def anyio_backend() -> str:
    """The server uses asyncio primitives, so tests run on that supported backend."""

    return "asyncio"
