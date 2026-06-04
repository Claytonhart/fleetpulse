# FleetPulse backend image — runs BOTH the one-shot `migrate` service and the `api`.
# Build context is the repo root (pyproject.toml + uv.lock live there); see
# docker-compose.yml `build.context: .` / `dockerfile: backend/Dockerfile`.
FROM python:3.12-slim AS runtime

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    UV_COMPILE_BYTECODE=1 \
    UV_LINK_MODE=copy \
    PYTHONPATH=/app \
    PATH="/app/.venv/bin:$PATH"

# uv for dependency install — pinned to match the host toolchain.
COPY --from=ghcr.io/astral-sh/uv:0.11.18 /uv /usr/local/bin/uv

WORKDIR /app

# Dependency layer (cached unless pyproject/lock change). README.md is referenced
# by pyproject's `readme=`, so it must be present when uv reads project metadata.
# --no-install-project: the `app` package runs from source via PYTHONPATH, not as
# an installed distribution.
COPY pyproject.toml uv.lock README.md ./
RUN uv sync --frozen --no-dev --no-install-project

# App source: backend/ becomes /app (so /app/app, /app/alembic, /app/alembic.ini).
COPY backend/ ./

EXPOSE 8000

# api overrides this in Compose; migrate runs `alembic upgrade head`.
CMD ["uvicorn", "app.main:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
