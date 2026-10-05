# syntax=docker/dockerfile:1.7
# ---------------------------------------------------------------------------
# Stage 1: resolve and install dependencies with uv (fast, reproducible from uv.lock)
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS builder

COPY --from=ghcr.io/astral-sh/uv:0.11.7 /uv /bin/uv
ENV UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    UV_PYTHON_DOWNLOADS=never

WORKDIR /app

# Dependencies first: this layer is cached until pyproject/uv.lock change.
COPY pyproject.toml uv.lock ./
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-install-project

COPY README.md ./
COPY src ./src
RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --frozen --no-dev --no-editable

# ---------------------------------------------------------------------------
# Stage 2: minimal runtime - no compiler, no uv, no source tree, non-root
# ---------------------------------------------------------------------------
FROM python:3.12-slim AS runtime

RUN groupadd --system --gid 10001 app \
 && useradd --system --uid 10001 --gid app --home-dir /app --shell /usr/sbin/nologin app

WORKDIR /app
COPY --from=builder --chown=app:app /app/.venv /app/.venv
# The knowledge base ships with the image only for the in-memory dev retriever.
COPY --chown=app:app data ./data

# LANGGRAPH_STRICT_MSGPACK: only safe built-in types are deserialised from
# checkpoints, so a tampered checkpoint row cannot import/execute arbitrary code.
ENV PATH="/app/.venv/bin:$PATH" \
    PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    LANGGRAPH_STRICT_MSGPACK=true \
    PORT=8000

USER app
EXPOSE 8000

# One worker per container: scale out with Container Apps replicas, not processes.
# --proxy-headers: we sit behind the Container Apps (Envoy) ingress.
CMD ["sh", "-c", "exec uvicorn --factory support_agent.main:create_app --host 0.0.0.0 --port ${PORT} --proxy-headers --forwarded-allow-ips='*' --no-access-log --timeout-graceful-shutdown 25"]
