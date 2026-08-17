FROM ghcr.io/astral-sh/uv:0.10.2 AS uv

FROM python:3.11-slim-bookworm

COPY --from=uv /uv /usr/local/bin/uv

WORKDIR /app

ENV MCP_DEPLOYMENT_MODE=hosted \
    MCP_PORT=7860 \
    PYTHONUNBUFFERED=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

COPY pyproject.toml uv.lock README.md ./
COPY src ./src

RUN uv sync --locked --no-dev --no-editable \
    && useradd --create-home --uid 1000 appuser \
    && chown -R appuser:appuser /app

USER appuser

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=15s --retries=3 \
    CMD ["python", "-c", "import urllib.request; urllib.request.urlopen('http://127.0.0.1:7860/healthz', timeout=3)"]

CMD ["/app/.venv/bin/spotify-mcp-server"]
