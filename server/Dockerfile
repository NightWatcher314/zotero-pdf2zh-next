# syntax=docker/dockerfile:1.7
FROM ghcr.io/astral-sh/uv:python3.12-trixie-slim

ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy

WORKDIR /app/server

COPY server/pyproject.toml server/uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev --no-install-project

COPY server/server.py server/task_manager.py server/pdf2zh_next_service.py ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --locked --no-dev

EXPOSE 8890

CMD ["uv", "run", "--no-sync", "zotero-pdf2zh-next", "--host", "0.0.0.0", "--port", "8890"]
