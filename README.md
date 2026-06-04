# FleetPulse — Fleet Telemetry & Alerting Platform

A telemetry monitoring platform for a fleet of vehicles. A simulated fleet
continuously emits telemetry (location, speed, state-of-charge, motor temperature,
error codes); the platform **ingests it at scale, stores it as time-series,
evaluates alert rules in the background, opens/resolves incidents, and streams a
live operations dashboard over WebSockets.**

This is a **backend-focused** portfolio project: the interesting work lives in
high-throughput async ingestion, the TimescaleDB time-series layer, the async
Celery alerting pipeline, and real-time streaming. See [`SPEC.md`](SPEC.md) for the
source-of-truth requirements and [`IMPLEMENTATION_PLAN.md`](IMPLEMENTATION_PLAN.md)
for the build runbook.

## Tech stack

Python 3.12 · FastAPI · Pydantic v2 · SQLAlchemy 2 (async) + asyncpg · Alembic ·
TimescaleDB (Postgres 16) · Celery + Redis · WebSockets · Docker Compose ·
Vite + React + TypeScript (dashboard).

## Getting started

Prerequisites: Docker, [`uv`](https://docs.astral.sh/uv/), and Python 3.12
(`uv python install 3.12`).

```bash
# 1. Bring up the backing services (TimescaleDB + Redis).
#    `make up` bootstraps .env from .env.example automatically on first run.
make up

# 2. Install the project-local virtualenv for the host dev loop
#    (.venv ≈ node_modules, uv sync ≈ npm install).
make sync

# 3. Lint / type-check and run the tests.
make lint
make test
```

Config is 12-factor via environment variables (`.env`, git-ignored). Only
`.env.example` is committed — `make up` copies it to `.env` if one doesn't exist.

> **Status:** under construction, built one step at a time per
> `IMPLEMENTATION_PLAN.md`. Step 1 (scaffolding + infra-only Compose) is in place;
> app services (api, worker, beat, simulator, web) land in later steps.
