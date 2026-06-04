# FleetPulse — developer task runner.
# Docker runs the app + backing services; host-side uv/Python runs the fast inner
# loop (lint, type-check, tests). `.venv` ≈ node_modules, `uv sync` ≈ npm install.

.PHONY: up down lint fmt test sync

# Bootstrap .env from .env.example (only .example is committed), then bring up infra.
up:
	@[ -f .env ] || (cp .env.example .env && echo "Created .env from .env.example")
	docker compose up -d

down:
	docker compose down

# Install/refresh the project-local virtualenv from pyproject + uv.lock.
sync:
	uv sync

# Lint + type-check — backend/ and simulator/ only (frontend has its own toolchain).
lint:
	uv run ruff check backend simulator
	uv run mypy

fmt:
	uv run ruff format backend simulator
	uv run ruff check --fix backend simulator

test:
	uv run pytest
