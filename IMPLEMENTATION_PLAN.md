# FleetPulse — Implementation Plan

_Last updated: 2026-06-03. Companion to `SPEC.md` (the source-of-truth requirements doc). This file is the **build runbook**: an ordered set of self-contained steps, each sized for a single agent session._

---

## ⚠️ How to use this plan (READ THIS FIRST — every agent, every session)

This project is built **one step per agent session**. The agent doing a step has **no memory** of prior sessions. So:

1. **Ground yourself before doing anything.** Read, in order:
   - This section + the **Engineering conventions** section below.
   - The **Progress Log** at the bottom (what's been done, any deviations, notes left for you).
   - The **Status checklist** to find the **first step whose status is `TODO`** — that is your step. Do **only that step**.
   - `SPEC.md` — at minimum the sections your step's **"Read first"** list names. `SPEC.md` is the source of truth; if this plan and `SPEC.md` ever disagree, **`SPEC.md` wins** — note the conflict in the Progress Log.
2. **Do the step.** Follow its tasks. Stay inside its scope — do not pull work forward from later steps (it breaks the handoff and the verification contract).
3. **Verify.** Run the step's **"Verify"** commands. The step is not done until they pass. If you cannot make them pass, leave the step `IN PROGRESS`, write what's blocking in the Progress Log, and stop.
4. **Record.** When verification passes:
   - Flip the step's status in the **Status checklist** to `DONE`.
   - Append a **Progress Log** entry: what you built, any decisions/deviations from the plan, files added, and anything the next agent needs to know (especially **seam locations** later steps depend on).
5. **Git is the user's job.** Per the user's global rules, **do not run git commands** (no init, branch, commit, push) unless the user explicitly asks in-session. At the end of your step, summarize your changes and tell the user the step is ready for them to review/commit. The Progress Log — not git — is the authoritative record of what's done.

**Definition of done for the whole plan:** `SPEC.md` §11 (Definition of done v1). Steps 1–13 deliver it; Step 14+ are the optional M7 stretch.

---

## Engineering conventions (apply to every step)

- **Language/runtime:** Python **3.12**, fully **async** (FastAPI, async SQLAlchemy, asyncpg). Type hints everywhere; code must pass **mypy** and **ruff**.
- **Dependency mgmt:** `uv` with a `pyproject.toml` (pip + `requirements.txt` is an acceptable fallback if `uv` is unavailable — note which you used). Pin versions.
- **Config:** 12-factor. All config via env vars through **`pydantic-settings`**; `.env` for local (git-ignored), `.env.example` committed. **No secrets in the repo.**
- **Everything runs in Docker Compose.** A step's "Verify" should work against `docker compose up`. Local host runs are fine for unit tests.
- **Migrations are owned by one service.** A dedicated one-shot **`migrate`** Compose service runs `alembic upgrade head`; `api`, `worker`, and `beat` all `depends_on` it with `condition: service_completed_successfully`. **No app service runs migrations on its own startup** — this avoids races/lock contention (established in Step 3; all later services inherit it).
- **`telemetry_reading` has NO surrogate `id`.** Its primary key is the **composite `(vehicle_id, ts)`** — TimescaleDB requires the partitioning column (`ts`) to be part of any PK/unique constraint, so a surrogate `id` PK would make `create_hypertable` fail. (Re-stated in Step 2; do not forget it.)
- **TimescaleDB DDL that can't run in a transaction:** `CREATE MATERIALIZED VIEW … WITH (timescaledb.continuous)`, `add_continuous_aggregate_policy`, `add_retention_policy`, `add_compression_policy` **cannot execute inside a transaction block**, but Alembic wraps migrations in one by default. Run them inside `with op.get_context().autocommit_block():`. (Re-stated in Step 7.)
- **Tests:** `pytest` + `pytest-asyncio`; name files `test_*.py` under `backend/tests/`. Each step that adds logic adds tests for it.
- **Test-database strategy (established in Step 1, reused by every step — do NOT re-improvise):** integration tests run against an **ephemeral TimescaleDB + Redis started via `testcontainers`** (same pinned images as Compose), behind a shared `conftest.py` session fixture that applies `alembic upgrade head`. Pure-logic tests (e.g. the Step 8 rule engine) need no DB. This runs identically locally and in CI — both only need Docker. If your step needs a DB in tests, **use the existing fixture; don't stand up your own.**
- **Tests self-seed; they never depend on the simulator.** Integration tests insert their own telemetry via fixtures/helpers (direct inserts or the ingest endpoint) so they're deterministic and CI-runnable. The simulator (Step 5) is for demos + load tests only — never a test-data source.
- **Lint scope:** `ruff` + `mypy` target `backend/` and `simulator/` only — **not** `frontend/` (that's TypeScript, linted by its own toolchain).
- **Source of truth:** `SPEC.md`. Decisions live in `SPEC.md` §10 — honor them (idempotency, hysteresis, whole-batch validation, pub/sub split, etc.).
- **Repo layout** (established in Step 1; all later steps assume it):
  ```
  fleet_telemetry/
    SPEC.md  IMPLEMENTATION_PLAN.md  README.md
    docker-compose.yml  .env.example  .gitignore  Makefile  pyproject.toml
    backend/
      app/
        main.py            # FastAPI app factory
        config.py          # pydantic-settings
        db/                # engine, session, Base
        models/            # SQLAlchemy models
        schemas/           # Pydantic request/response models
        api/               # routers (telemetry, vehicles, incidents, rules, ws)
        services/          # ingest, cache, rule engine, incidents
        workers/           # celery app, tasks, beat schedule
        ws/                # websocket manager + redis pubsub relay
      alembic/             # migrations
      tests/
      Dockerfile
    simulator/             # standalone fleet simulator (own Dockerfile)
    frontend/              # Vite + React + TS (own Dockerfile)
    load/                  # locustfile.py + acceptance targets
  ```

### Shared note — post-DB side-effect failure window (referenced by Steps 4, 6, 9)

Ingest does `INSERT … ON CONFLICT (vehicle_id, ts) DO NOTHING RETURNING …`, then performs side effects on the **returned** rows (cache write, telemetry publish, enqueue for rule eval). If the DB insert succeeds but a side effect then fails (Redis/Celery hiccup) and the producer retries the **same** batch, the re-insert returns **no rows** (already present) — so the side effect never runs for those readings. **v1 accepts this as a known tradeoff** (at-least-once delivery is not guaranteed end-to-end). The production-grade fix is a transactional **outbox** (persist intent in the same DB txn, relay asynchronously); note it in the README as the scale answer, but **do not build it in v1.**

---

## Status checklist

> Flip `TODO → IN PROGRESS → DONE` as you work. The first `TODO` is the next step.

- [x] **Step 0** — Environment prerequisites (Docker, uv, Python 3.12, smoke tests) — `DONE` (human-owned; completed 2026-06-03. Smoke tests passed: Redis `PONG`, TimescaleDB extension `2.27.2` loaded. `websocat` skipped — optional, only for Step 10.)
- [x] **Step 1** — Repo scaffolding, tooling, infra-only Compose — `DONE`
- [x] **Step 2** — Database foundation (models, Alembic, hypertable, indexes) — `DONE`
- [x] **Step 3** — FastAPI app skeleton + health + DB session + `migrate`/`api` containers — `DONE`
- [ ] **Step 4** — Ingest endpoint `POST /telemetry` (validation, idempotency, back-pressure) — `TODO`
- [ ] **Step 5** — Fleet simulator (M2) — `TODO`
- [ ] **Step 6** — Redis hot-state cache + read APIs (`/vehicles`) — `TODO`
- [ ] **Step 7** — Continuous aggregates + retention/compression + telemetry read endpoint — `TODO`
- [ ] **Step 8** — Celery/beat setup + pure rule engine + deterministic rule seeding — `TODO`
- [ ] **Step 9a** — Alerting pipeline: enqueue + hysteresis counters + incidents — `TODO`
- [ ] **Step 9b** — Incident & rules CRUD endpoints — `TODO`
- [ ] **Step 10** — WebSocket + Redis pub/sub fan-out (deltas-only protocol) — `TODO`
- [ ] **Step 11a** — React dashboard: board + vehicle detail + charts (REST) — `TODO`
- [ ] **Step 11b** — React live layer: WS deltas + live incident feed — `TODO`
- [ ] **Step 12** — Test suite hardening + load test + numbers — `TODO`
- [ ] **Step 13** — CI + README + finalize resume bullets — `TODO`
- [ ] **Step 14+** — M7 stretch (optional) — `TODO`

---

## Step 0 — Environment prerequisites (human-owned — do before Step 1)

**Who does this:** **You (the human)**, once, on your dev machine. **AGENTS: skip this step** — treat a healthy toolchain as a given and start at the first `TODO` (Step 1). If a later step's `Verify` fails because a host tool is missing, **stop and flag it**; do not try to install host tooling yourself.

**Goal:** A working **hybrid** dev environment — Docker runs the app + backing services; host-side `uv`/Python and Node run the fast inner loop (lint, type-check, tests, IDE). Verified end-to-end before any code exists.

**Why hybrid (and why it's the realistic workplace setup):** real Python shops run the *application and its backing services* in Docker/Compose (reproducibility + prod parity) while keeping the *language runtime + a per-project virtualenv on the host* for the editor, debugger, and test loop. Mental-model map for a JS/React background: **`.venv/` ≈ `node_modules/`**, **`pyproject.toml` ≈ `package.json`**, **`uv.lock` ≈ `package-lock.json`**, **`uv sync` ≈ `npm install`**, **`uv run` ≈ `npm run`**, **`uv python install` ≈ `nvm`**. Unlike npm, Python isolation is *manual* — plain `pip install` pollutes global Python, so **all project deps live in a project-local `.venv` (built by Step 1's `uv sync`), never global**.

**Already present on this machine (checked 2026-06-03):** Docker 28.5 + Compose v2.40, Homebrew, Node 22.9 / npm 10.8, make, git, jq, system Python 3.14. **Gaps to close below:** `uv` (missing), Docker daemon (not running at check time), `websocat` (optional).

**Tasks:**

1. **Start the Docker daemon.** Launch Docker Desktop (daemon was down at last check). In Docker Desktop → Settings → Resources, give it headroom — the full stack is ~7 containers (`timescaledb`, `redis`, `api`, `worker`, `beat`, `simulator`, `web`): **≥ 6 GB memory, ≥ 4 CPUs** recommended.
   - _Verify:_ `docker info` exits 0; `docker run --rm hello-world` prints the success message.
2. **Install uv** (the one genuine addition): `brew install uv`.
   - _Verify:_ `uv --version` prints a version.
3. **Install the project's Python (3.12) via uv.** System Python is **3.14** (too new for the pinned deps); uv keeps 3.12 isolated without touching global 3.14: `uv python install 3.12`.
   - _Verify:_ `uv python list` shows a `3.12.x`. (Do **not** `pip install` project deps into global Python — Step 1's `uv sync` builds the project-local `.venv`.)
4. **Sanity-check the already-present tools:** `node --version` (≥ 20), `make --version`, `git --version`.
5. **Pre-pull the infra images** so Step 1 doesn't stall on a cold download and to prove Docker can run them:
   - `docker pull redis:7.4`
   - `docker pull timescale/timescaledb:latest-pg16` — **for this smoke test only.** Step 1 pins an exact patch tag (e.g. `2.x.y-pg16`); `latest-pg16` here just confirms Docker can pull + run a Timescale image.
6. **Smoke-test that both backing services actually run** (throwaway containers, removed after):
   - Redis: `docker run --rm -d --name fp-smoke-redis redis:7.4` → `docker exec fp-smoke-redis redis-cli ping` returns `PONG` → `docker rm -f fp-smoke-redis`.
   - Timescale: `docker run --rm -d --name fp-smoke-ts -e POSTGRES_PASSWORD=smoke timescale/timescaledb:latest-pg16` → wait ~5s → `docker exec fp-smoke-ts psql -U postgres -c "SELECT extname FROM pg_extension WHERE extname='timescaledb';"` returns a row (extension preloaded) → `docker rm -f fp-smoke-ts`.
7. **(Optional) `brew install websocat`** — used only for the manual WebSocket check in Step 10; skippable (a `docker run` one-liner can substitute).

**Verify (all must pass):**
- `docker info` succeeds and `docker run --rm hello-world` works.
- `uv --version` works and `uv python list` shows `3.12.x`.
- `node --version` ≥ 20, plus `make --version` and `git --version` print.
- Both smoke-test containers responded (`PONG` from Redis; the Timescale query returned a row) and were removed.

**Done when:** Docker runs containers, `uv` + Python 3.12 are ready on the host, Node/make/git are confirmed, and both backing-service images run locally. You're clear to hand **Step 1** to an agent.

---

## Step 1 — Repo scaffolding, tooling, infra-only Compose

**Goal:** A clean project skeleton that lints, with TimescaleDB + Redis running in Compose. No app code yet.

**Read first:** This file's conventions + `SPEC.md` §7 (Tech stack). Prereqs: none.

**Tasks:**
- Create the repo layout above (empty package dirs with `__init__.py`; placeholder dirs for `simulator/`, `frontend/`, `load/`).
- `pyproject.toml` with deps grouped: runtime (`fastapi`, `uvicorn[standard]`, `pydantic`, `pydantic-settings`, `sqlalchemy[asyncio]`, `asyncpg`, `alembic`, `celery[redis]`, `redis`) and dev (`pytest`, `pytest-asyncio`, `httpx`, `testcontainers`, `ruff`, `mypy`, `locust`). Pin versions.
- **Test-database harness (establish once — every later step reuses it):** a shared `backend/tests/conftest.py` with a **session-scoped fixture that starts ephemeral TimescaleDB + Redis via `testcontainers`** (same pinned images as Compose), runs `alembic upgrade head` against the DB, and yields connection settings; plus a function-scoped fixture giving each test isolation (transaction-rollback or truncate-between-tests). Add a tiny data-seeding helper (insert N readings for a vehicle) that integration tests reuse. See the Testing conventions above — this is the canonical mechanism; later agents must not stand up their own.
- `ruff` + `mypy` config (in `pyproject.toml`), **scoped to `backend/` and `simulator/`** (not `frontend/`). `.gitignore` (Python, `.env`, `node_modules`, `__pycache__`, etc.).
- `.env.example` with every var the project will need (DB URL, Redis URL, pool size + acquire timeout, batch/body caps, broker queue-depth threshold, **CORS allowed origins**, **frontend API base URL + WS URL**, API key placeholder, sim knobs) — seed it now with DB + Redis URLs; later steps add the rest.
- `docker-compose.yml` with **only infra** services for now: `timescaledb` (**pin an exact patch tag, e.g. `timescale/timescaledb:2.x.y-pg16` — do NOT use `latest`/`latest-pg16`**; Timescale's continuous-aggregate/retention/compression APIs change across versions, and an unpinned image will silently break Step 7 migrations on a future pull. Check the current stable tag and pin it.) with named volume + healthcheck; and `redis` (pin e.g. `redis:7.4`, healthcheck). App services (`migrate`, `api`, `worker`, `beat`, `simulator`, `web`) get added in their own steps.
- `Makefile` with `up`, `down`, `lint` (ruff+mypy on backend/+simulator/), `test`, `fmt` targets. The **`up` target must bootstrap `.env`**: `[ -f .env ] || cp .env.example .env` before `docker compose up` — only `.env.example` is committed, so a clean checkout needs this (alternatively, give every Compose var a safe `${VAR:-default}`; pick one and document it).
- `README.md` skeleton (project name + one-paragraph description from `SPEC.md` §1; "Getting started" stub that tells a fresh user to `cp .env.example .env` / run `make up`).

**Verify:**
- `docker compose up -d timescaledb redis` → both become healthy (`docker compose ps`).
- `docker compose exec timescaledb psql -U <user> -d <db> -c "SELECT name FROM pg_available_extensions WHERE name='timescaledb';"` returns a row (extension **available**; it gets `CREATE`d in Step 2's migration).
- A trivial integration test (e.g. `SELECT 1` against the testcontainers-provided DB via the new `conftest.py` fixture) passes — proving the test-DB harness works before any real tests depend on it.
- `make up` from a checkout with no `.env` succeeds (bootstrap copies `.env.example`).
- `make lint` runs clean on the (empty) package.

**Done when:** infra is up + healthy and lint passes. Record chosen image tags, user/db names, and `uv`-vs-pip in the Progress Log.

---

## Step 2 — Database foundation (models, Alembic, hypertable, indexes)

**Goal:** All v1 tables exist via Alembic migrations, `telemetry_reading` is a TimescaleDB hypertable, and all indexes/constraints from the spec are in place.

**Read first:** `SPEC.md` §5 (Data model — full), §10.3 (idempotency key), §10.13 (status ownership). Prereqs: Step 1.

**Tasks:**
- `backend/app/db/`: async engine + `async_sessionmaker` + declarative `Base`, all reading config from `config.py` (`pydantic-settings`).
- SQLAlchemy models in `backend/app/models/` exactly per `SPEC.md` §5:
  - `vehicle` (`id` PK, `external_id` unique, `name`, `model`, `status` enum, `last_seen_at`, `created_at`).
  - `telemetry_reading` — **NO surrogate `id`. Composite primary key `(vehicle_id, ts)`** (must include `ts`; see conventions). FK `vehicle_id` → `vehicle.id`, plus `ts`, scalar metrics (`lat`, `lon`, `speed_kph`, `soc_pct`, `motor_temp_c`, `odometer_km`), `error_codes` jsonb.
  - `alert_rule` (`metric`, `operator`, `threshold`, `recovery_threshold`, `severity`, `window_count`, `enabled` — **no `window_seconds`**).
  - `incident` (fields per §5).
- Alembic init + first migration that, in order:
  1. `CREATE EXTENSION IF NOT EXISTS timescaledb;`
  2. creates tables (with the composite PK on `telemetry_reading`),
  3. `SELECT create_hypertable('telemetry_reading','ts');` **(works because the PK includes `ts`),**
  4. adds indexes from §5: `telemetry_reading (vehicle_id, ts DESC)`, partial index on `alert_rule WHERE enabled`, `incident (status, severity, vehicle_id)`, and the **partial unique** `UNIQUE (vehicle_id, rule_id) WHERE status != 'resolved'` on `incident`.
- Use raw `op.execute(...)` for the Timescale-specific and partial-index DDL. _(Note: the hypertable + plain index DDL here is transaction-safe; the **autocommit-block** requirement only applies to continuous aggregates/policies in Step 7.)_

**Verify:**
- `alembic upgrade head` succeeds against the Compose DB.
- `psql` checks: `telemetry_reading` appears in `timescaledb_information.hypertables`; the partial unique index exists on `incident`; the composite PK `(vehicle_id, ts)` exists.
- `alembic downgrade base` then `upgrade head` round-trips cleanly.

**Done when:** migrations apply + round-trip, hypertable + all indexes/constraints confirmed in psql. Note any Timescale DDL-ordering gotchas in the Progress Log.

---

## Step 3 — FastAPI app skeleton + health + DB session + `migrate`/`api` containers

**Goal:** A running FastAPI service in Compose with a health check wired to the DB, a request-scoped async DB session dependency, and the one-shot migration service that all app services depend on.

**Read first:** `SPEC.md` §7 (Backend); this file's conventions (migration ownership). Prereqs: Steps 1–2.

**Tasks:**
- `backend/app/main.py`: FastAPI app factory; mount an `/api/v1` router; `GET /api/v1/health` returning `{status, db: ok}` (does a `SELECT 1`).
- **CORS middleware**, allowed origins from settings (`CORS_ORIGINS`), so the separate-origin frontend `web` service (Step 11) and the WS connection can reach the API. Default it to the Compose web origin; this is wired now so Step 11a doesn't stall on it.
- DB session dependency (async, per-request) in `app/db/`.
- `backend/Dockerfile` (python:3.12-slim, install via `uv`/pip, run uvicorn).
- **`migrate` service** in `docker-compose.yml`: uses the backend image, command `alembic upgrade head`, `depends_on` db healthy, restart `no` (one-shot). This is the **only** service that runs migrations.
- **`api` service**: `depends_on` `migrate` (`condition: service_completed_successfully`) and redis healthy; env from `.env`; expose port. Does **not** run migrations itself.
- Basic structured logging + a settings object surfacing pool size + acquire timeout (used in Step 4's back-pressure).

**Verify:**
- `docker compose up -d` (now includes `migrate` + `api`) → `migrate` runs to completion, then `curl localhost:<port>/api/v1/health` returns `200` with `db: ok`.
- `make lint` clean; a trivial `test_health.py` passes via `httpx`.

**Done when:** health endpoint is green against the live DB in Compose, with migrations applied by the one-shot `migrate` service.

---

## Step 4 — Ingest endpoint `POST /telemetry`

**Goal:** The core write path: validated, idempotent, back-pressured batch ingest. (Cache/publish/enqueue are added in Steps 6/9 — leave clearly marked seams.)

**Read first:** `SPEC.md` §4.1, §6 (`POST /telemetry`), §10.1, §10.3, §10.8, §10.9, §10.11; this file's "post-DB side-effect failure window" note. Prereqs: Steps 1–3.

**Tasks:**
- Pydantic v2 schemas in `app/schemas/`: batch body `{ readings: [{ vehicle_external_id, ts, lat, lon, speed_kph, soc_pct, motor_temp_c, odometer_km, error_codes[] }] }`; response `{ inserted_count, duplicate_count }`.
- `app/services/ingest.py` + `app/api/telemetry.py`:
  - **Whole-batch validation** (§10.8): malformed reading or unknown `vehicle_external_id` → `422`, nothing written.
  - **Resolve `vehicle_external_id` → internal `vehicle.id` in ONE bulk query per batch** (a single `WHERE external_id IN (...)`), not one query per reading. Unknown external_id in the set → 422.
  - Insert via `INSERT ... ON CONFLICT (vehicle_id, ts) DO NOTHING RETURNING *` — **return the full inserted rows** (all telemetry fields), not just `(vehicle_id, ts)`, so Steps 6 & 9 have what they need for cache/publish/eval without re-querying. Use the batched ORM/Core `insert()` — **not** `COPY` (that's only the M6 optimization).
  - **Enrich the returned rows with `vehicle_external_id`** from the bulk-lookup map built during resolution. The DB row only carries the internal `vehicle_id`, but cache keys (`fleet:vehicle:{external_id}:state`) and all WS payloads are keyed by `external_id` — so the seam must hand Steps 6/9 **enriched readings = DB fields + `vehicle_external_id`**, not raw DB rows.
  - **Durable status on ingest** (§10.13): set `vehicle.last_seen_at = greatest(last_seen_at, max(submitted ts))` for the batch's vehicles, and **clear `offline` (→ `active`) when telemetry arrives for a vehicle currently marked offline** — otherwise a returned-online vehicle stays `offline` until the next beat cycle and durable status reads incoherently.
  - Return `{inserted_count, duplicate_count}` (duplicates = submitted − inserted).
  - **Back-pressure caps** (§10.9): batch > 500 readings → `422`; bounded DB pool acquire timeout → `503`. **Body-size cap** (~1 MB → `413`) requires **ASGI middleware** (Starlette does not enforce a max body by default) — implement it, don't assume a config flag. _(The broker queue-depth back-pressure check is added in Step 9a, once the queue exists.)_
  - **Leave a marked seam** (comment + a function boundary that receives the list of inserted rows) where Step 6 will cache + publish a telemetry delta, and Step 9 will enqueue per-vehicle.
- Tests: happy path writes rows; **duplicate batch** → `duplicate_count` > 0, no extra rows; malformed reading → `422`; unknown vehicle → `422`; oversized batch/body → rejected.

**Verify:**
- Seed a vehicle (small fixture/script), `curl` a batch → rows in `telemetry_reading`; re-`curl` same batch → `duplicate_count` matches, row count unchanged.
- `pytest backend/tests/test_ingest*.py` green; `make lint` clean.

**Done when:** idempotent validated ingest works against Compose and tests pass. **Record the exact seam function/location in the Progress Log** for Steps 6 & 9.

---

## Step 5 — Fleet simulator (M2)

**Goal:** A standalone Python service that registers a fleet and streams realistic batched telemetry into the ingest API, with scheduled fault scenarios.

**Read first:** `SPEC.md` §2 (simulator bullet), §4.3 (what triggers alerts), §6 (API surface), §10.5 (Tier 2 + scheduled faults), §10.11 (vehicle registration). Prereqs: Steps 1–4.

**Tasks:**
- `simulator/` package + Dockerfile + Compose service `simulator` (depends_on `api`).
- **Add an idempotent registration endpoint** `POST /api/v1/vehicles/register` (body: external_id, name, model; upsert on `external_id` so simulator restarts are safe). On boot the simulator registers its N vehicles through it. **This endpoint is new API surface not yet in `SPEC.md` §6 — append it to `SPEC.md` §6** as part of this step (and note it in the Progress Log) so it isn't undocumented drift.
- Per-vehicle async loop with a small **state machine** (`driving/idle/charging/offline`) and **smooth/continuous** signals (temp rises under load & cools at idle; SoC drains driving / rises charging; speed follows state) — values walk with small deltas + noise, no teleporting (Tier 2, §10.5). Emit **batched** readings on a configurable cadence.
- **Scheduled fault scenarios** (run automatically, zero interaction): overheat ramp (past a temp threshold, held), battery sag, go-offline (stop reporting). At least one scenario must reliably trip alerting once Step 9a lands.
- Config knobs via env: vehicle count, emit interval, batch size, fault schedule.

**Verify:**
- `docker compose up` → telemetry rows accumulate continuously without manual curl; `SELECT count(*)` over `telemetry_reading` climbs; distinct `vehicle_id`s = N.
- A scheduled overheat is visible as climbing `motor_temp_c` for the targeted vehicle in the raw data.

**Done when:** continuous realistic data flows and faults are observable in the table. (Incidents come in Step 9a.)

---

## Step 6 — Redis hot-state cache + read APIs

**Goal:** Live fleet-state cache populated on ingest, plus the read endpoints that back the dashboard board — with the cache→table fallback for offline vehicles.

**Read first:** `SPEC.md` §4.1, §5 (Redis keys + `vehicle.status` ownership), §6 (`GET /vehicles`, `/vehicles/{id}`), §10.13; this file's "post-DB side-effect failure window" note. Prereqs: Steps 1–5.

**Tasks:**
- `app/services/cache.py`: write `fleet:vehicle:{external_id}:state` (latest reading + derived live status) on each ingest; explicit TTL. Wire into the **Step 4 seam** (consumes the full inserted rows).
- **Leave a marked telemetry-publish seam for Step 10** right alongside the cache write (a clear function boundary / comment where Step 10 will `PUBLISH` a `telemetry_delta` to `fleet:telemetry`). Record its location in the Progress Log, mirroring Step 4's seam discipline.
- `GET /api/v1/vehicles` (board): merge cache with the `vehicle` table so vehicles **missing from cache still render** (offline; §10.13). `GET /api/v1/vehicles/{id}` detail.
- Tests: ingest populates cache; board reflects latest values; a vehicle with expired/missing cache still appears via table fallback.

**Verify:**
- With simulator running, `GET /vehicles` returns all N with current-ish values; stop the simulator for one vehicle (or expire its key) → it still appears (status reflects staleness), proving the fallback.

**Done when:** read APIs return live fleet state with working offline fallback; tests pass. **Record the publish-seam location** for Step 10.

---

## Step 7 — Continuous aggregates + retention/compression + telemetry read endpoint

**Goal:** Time-series rollups for charts, all Timescale data-lifecycle policies, and the endpoint that serves chart data.

**Read first:** `SPEC.md` §4.2, §10.2 (real-time aggregates), §6 (`GET /vehicles/{id}/telemetry`); this file's conventions (autocommit-block requirement). Prereqs: Steps 1–6.

**Tasks:**
- Alembic migration — **wrap all of this in `with op.get_context().autocommit_block():`** (continuous-aggregate and policy DDL cannot run in a transaction):
  - **Continuous aggregates** `telemetry_1m` and `telemetry_1h` over `telemetry_reading` (avg/max `motor_temp_c`, min `soc_pct`, avg `speed_kph`, etc.), in **real-time mode** (`materialized_only=false`) — §10.2. Add a `add_continuous_aggregate_policy` for the materialized portion; document the refresh window.
  - **All Timescale data-lifecycle policies live here.** **Enable compression on the hypertable FIRST**, then add the policy: `ALTER TABLE telemetry_reading SET (timescaledb.compress, timescaledb.compress_segmentby='vehicle_id', timescaledb.compress_orderby='ts DESC');` → `add_compression_policy('telemetry_reading', INTERVAL '7 days')`. Then `add_retention_policy` for raw data. _(These were mentioned around Step 8 in earlier drafts — they belong here with the other autocommit-block DDL. Step 8 does not touch Timescale policies.)_
- **Write a proper `downgrade()`** (also inside an `autocommit_block`): drop the retention/compression policies, then `DROP MATERIALIZED VIEW telemetry_1h, telemetry_1m;` (CAGGs), in an order that reverses `upgrade`. Mirror Step 2's clean round-trip discipline.
- `GET /api/v1/vehicles/{id}/telemetry?from&to&resolution=1m|1h|raw` → reads from the matching aggregate (or raw for `resolution=raw`).
- Tests (**self-seeded via the Step 1 fixture/helper — not the simulator**): aggregates return non-empty buckets after the test inserts readings directly; `raw` vs `1m` differ as expected; `alembic downgrade -1` then `upgrade head` round-trips this migration cleanly. _(Note: continuous aggregates may need an explicit `refresh_continuous_aggregate` call in-test to materialize seeded data deterministically rather than waiting on the policy — do this in the fixture.)_

**Verify:**
- With simulator running a few minutes, `GET /vehicles/{id}/telemetry?resolution=1m` returns bucketed series whose latest bucket is current (real-time mode working).
- `psql`: continuous aggregates + retention + compression policies are present (`timescaledb_information.jobs` / `.continuous_aggregates`).

**Done when:** chartable rollups are served and current to the latest minute, and all Timescale policies are installed.

---

## Step 8 — Celery/beat setup + pure rule engine + deterministic rule seeding

**Goal:** Celery wired to Redis, a beat schedule with offline-detection, a **pure, unit-tested** rule-evaluation module, and default rules that exist deterministically after migration.

**Read first:** `SPEC.md` §4.3, §5 (`alert_rule`, Redis eval keys), §10.4, §10.10, §10.12, §10.13. Prereqs: Steps 1–7.

**Tasks:**
- `app/workers/celery_app.py`: Celery app on the Redis broker; `task_ignore_result=True` (§5). Add `worker` and `beat` services to Compose (both `depends_on` `migrate` completed + redis healthy).
- **Beat job: offline-detection** (§10.13) — ages out `vehicle.last_seen_at` → sets durable `status='offline'`.
- `app/services/rules.py` — **pure rule engine, no I/O.** This holds **ALL decision logic** and is the single source of truth tested here. Contract (pin it, because Step 9a wraps it with Redis):
  - Signature shape: `evaluate(rule, reading, prior_state) -> (next_state, decision)` where `prior_state` = `{count, last_eval_ts, incident_open}` and `decision ∈ {None, OPEN, RESOLVE}`.
  - Implements **count-based hysteresis** with separate `threshold`/`recovery_threshold` dead band (§10.4); scalar metrics only (§10.12); ignores readings with `ts <= last_eval_ts` (out-of-order guard).
  - **Redis (added in Step 9a) is dumb storage only** — atomic get/set of `prior_state`. No threshold/dead-band/count logic in Lua or the worker, so the tests here exercise the exact logic that runs in production.
- **Deterministic rule seeding:** insert a few default `alert_rule` rows via an **Alembic data migration** (so they exist right after `alembic upgrade head` / the `migrate` service) — e.g. `motor_temp_c > 110` recover `< 105` window 3; `soc_pct < 15` recover `> 25`. The alerting demo depends on these existing.
- **Unit tests (the testable core):** open after N consecutive; no open at N−1; recovery only past the dead band; **flap-prevention** (threshold-hovering signal → 1 open under hysteresis vs. many under naive — this test backs the §8 résumé bullet); out-of-order reading ignored. _(Incident `ack→resolve` lifecycle is NOT pure-engine logic — it's tested in Step 9a.)_

**Verify:**
- `pytest backend/tests/test_rules*.py` green, including flap-prevention + out-of-order.
- `worker` + `beat` start in Compose; a **stale / non-reporting** vehicle (no telemetry past the threshold — not merely a `driving`-state idle that still reports) gets marked `offline` by the beat job.
- After `migrate`, `SELECT * FROM alert_rule` shows the seeded defaults.

**Done when:** pure rule engine is fully unit-tested, default rules are seeded by migration, and Celery/beat run in Compose.

---

## Step 9a — Alerting pipeline: enqueue + hysteresis counters + incidents

**Goal:** Connect ingest → per-vehicle Celery tasks → the pure rule engine → incidents, with atomic Redis state and the active-incident guard. This is where alerts become real.

**Read first:** `SPEC.md` §3 (flow), §4.1 (enqueue-only-inserted), §4.3, §5 (`incident`, eval keys, partial unique), §10.9 (queue-depth back-pressure), §10.10 (ordering/granularity); Step 8's engine contract. Prereqs: Steps 1–8.

**Tasks:**
- Wire the **Step 4 enqueue seam**: group **inserted** rows by vehicle, sort by `ts`, enqueue **one Celery task per vehicle** (never per-reading, §10.10).
- Per-vehicle task: load enabled rules, walk readings in `ts` order, and for each (vehicle, rule) load/store `prior_state` from `fleet:eval:{vehicle_id}:{rule_id}` using an **atomic optimistic-concurrency wrapper** (Redis `WATCH`/MULTI or a Lua get/set) — but **all decisions come from the pure `evaluate()`** of Step 8; Redis only stores the state atomically and rejects lost updates. On `OPEN` → insert an `incident` relying on the **partial unique index** so concurrent workers can't double-open (handle the conflict gracefully). On `RESOLVE` → close it.
- **Queue-depth back-pressure (§10.9), now that the queue exists:** in the ingest path, reject with `503` + `Retry-After` when the Celery broker's pending-task depth exceeds the configured threshold. (Completes the back-pressure story started in Step 4.)
- **Publisher split for Step 10 (leave a seam):** the **worker** publishes `incident_event` for `OPEN`/`RESOLVE` to `fleet:incidents`. _(The `ack` event is published by the API in Step 9b/10, not the worker, since ack happens via the API.)_
- Document the **cross-batch ordering limitation** (§10.10) in code comments + a README note.
- Tests (**self-seeded — not the simulator**): a crafted rising→falling `motor_temp_c` sequence fed directly through the pipeline opens exactly one incident then resolves; **incident-race test** (concurrent eval of same vehicle+rule → one active incident); duplicate-batch re-enqueue does **not** double-increment counters (ties to Step 4 idempotency); **`ack→resolve` lifecycle** transition (incident can be acknowledged then resolved). _(The live simulator overheat is the manual Verify below; the automated tests must not depend on it.)_

**Verify:**
- `docker compose up` with the simulator's overheat scenario → exactly one `open` incident appears for that vehicle, then flips to `resolved` on recovery.

**Done when:** the full ingest→alert→incident loop runs against the live simulator and the race/idempotency/lifecycle tests pass. Record the incident-publish seam for Step 10.

---

## Step 9b — Incident & rules CRUD endpoints

**Goal:** The REST surface for operating on incidents and rules.

**Read first:** `SPEC.md` §6 (incidents + rules endpoints). Prereqs: Step 9a.

**Tasks:**
- `GET /api/v1/incidents?status=&severity=&vehicle_id=` (the feed; uses the §5 incident index).
- `POST /api/v1/incidents/{id}/acknowledge` — transitions `open → acknowledged`. **The API publishes the `incident_event` (ack) to `fleet:incidents`** (workers own open/resolve; the API owns ack, since ack happens here) — leave the publish call wired to the same channel Step 10 relays.
- Rules CRUD: `GET /api/v1/rules`, `POST /api/v1/rules`, `PATCH /api/v1/rules/{id}` (enable/disable, tune thresholds).
- Tests: ack transitions state + publishes; rules CRUD round-trips; disabling a rule stops it opening new incidents.

**Verify:**
- `GET /incidents` shows the incident from Step 9a; `POST /incidents/{id}/acknowledge` flips it to `acknowledged`; a `PATCH /rules/{id}` disabling a rule prevents new incidents for it.

**Done when:** incident + rules endpoints work and ack publishes its event. Backend core (SPEC §4 centerpiece) is complete.

---

## Step 10 — WebSocket + Redis pub/sub fan-out (deltas-only protocol)

**Goal:** Real-time push to the dashboard, fanned out via Redis pub/sub, with a defined event protocol. **Snapshot is REST, not WS** (§4.4, §6).

**Read first:** `SPEC.md` §4.4, §6 (WS contract), §3 step 4, §5 (pub/sub channels). Prereqs: Steps 1–9b.

**Tasks:**
- **Publishers** (wire into the seams left earlier): the **API** publishes `telemetry_delta` to `fleet:telemetry` (Step 6 seam) and `incident_event(ack)` (Step 9b); **workers** publish `incident_event(open|resolve)` to `fleet:incidents` (Step 9a seam).
- `app/ws/`: a background **Redis subscriber** in the web process that relays both channels to a local in-memory set of connected sockets; endpoint **`WS /ws/fleet`** (top-level path, matching `SPEC.md` §6 — **not** under `/api/v1`).
- **Define the WS event protocol explicitly** (document it in code + README so the frontend agent doesn't invent one) — this is **deltas only; no snapshot over WS**:
  - **Client → server:** `{ "type": "subscribe_vehicle", "vehicle_external_id": "VH-0007" }`, `{ "type": "unsubscribe_vehicle", ... }`.
  - **Server → client:**
    - `{ "type": "vehicle_status_delta", "vehicle_external_id", "status", ...summary }` — **board updates**, sent to **all** connected clients (coalesced — see volume control). This is what keeps the board live without polling.
    - `{ "type": "telemetry_delta", "vehicle_external_id", "ts", ...metrics }` — **full-rate**, sent **only** to clients that `subscribe_vehicle`'d that vehicle (drives the detail chart).
    - `{ "type": "incident_event", "action": "open|ack|resolve", "incident": { ..., "vehicle_external_id" } }` — the incident payload **MUST include `vehicle_external_id`** (the DB `incident` row only has the internal `vehicle_id`; resolve it before publishing). All client-facing WS+REST payloads key vehicles by `external_id`.
- **Volume control — the relay owns the throttling** (not the publishers, not the clients): the web-process relay **coalesces per-vehicle status to ≤ 1/sec** and emits `vehicle_status_delta` to all board clients, while `telemetry_delta`s are forwarded full-rate only to subscribers of that vehicle. Don't broadcast every reading to every client.
- **Snapshot ownership:** the **client fetches the initial snapshot over REST** (Step 11a's TanStack Query against `/vehicles` + `/incidents`); the socket only streams deltas. On reconnect the client refetches REST and resumes. State this in the README; WS is best-effort, REST+DB is truth (§4.4).
- Tests: a connected test client receives an `incident_event` when one opens; a `subscribe_vehicle` client receives that vehicle's `telemetry_delta`s.

**Verify:**
- Connect a WS client (e.g. `websocat`) while the simulator runs; `subscribe_vehicle` the overheating vehicle → `telemetry_delta`s stream; trigger overheat → `incident_event(open)` arrives live, then `resolve`.

**Done when:** live events stream over the documented deltas-only protocol; publishers are correctly split (API: telemetry + ack; workers: open/resolve).

---

## Step 11a — React dashboard: board + vehicle detail + charts (REST)

**Goal:** The static-data dashboard: fleet board, vehicle detail, and charts, all over REST. (Live WS layer is Step 11b.)

**Read first:** `SPEC.md` §7 (Frontend), §6 (read endpoints). Prereqs: Steps 1–10.

**Tasks:**
- `frontend/` Vite + React + TS; Dockerfile + Compose `web` service.
- **API/WS base URLs via Vite env** (`VITE_API_BASE_URL`, `VITE_WS_URL`), set on the `web` Compose service to point at the `api` service. Relies on the **CORS middleware added in Step 3** — if cross-origin requests fail, check `CORS_ORIGINS` includes the web origin before touching anything else.
- **TanStack Query owns the REST snapshot** (`/vehicles`, `/vehicles/{id}`, `/vehicles/{id}/telemetry`, `/incidents`).
- **Fleet board:** all vehicles + status; offline vehicles visibly flagged (cache→table fallback already handled server-side).
- **Vehicle detail:** telemetry charts (Recharts/uPlot) from `/vehicles/{id}/telemetry`.
- **Incident feed (static read):** render `/incidents` with an ack button hitting `POST /incidents/{id}/acknowledge`.
- _(Optional per `SPEC.md` §7: reuse `music_platform/tokens.css` editorial styling — nice-to-have, not required.)_

**Verify:**
- `docker compose up` → board shows the fleet via REST (one-time / interval fetch — **not yet live**; the live push lands in 11b), vehicle detail shows charts, incident feed lists incidents and ack works.

**Done when:** the dashboard renders real data over REST and ack works. No live push yet.

---

## Step 11b — React live layer: WS deltas + live incident feed

**Goal:** Layer the real-time stream on top of 11a so the demo moment works.

**Read first:** `SPEC.md` §4.4, §6 (WS protocol); Step 10's documented event protocol. Prereqs: Step 11a.

**Tasks:**
- Thin WS client for **`/ws/fleet`** implementing the Step 10 protocol: on mount, rely on TanStack's REST snapshot (11a), then apply `vehicle_status_delta` / `telemetry_delta` / `incident_event` updates; handle reconnect by refetching the REST snapshot and resuming deltas.
- **Fleet board goes live:** consume `vehicle_status_delta` to update each vehicle card's status without refetch — **this is what satisfies `SPEC.md` §2's "no polling"** (after this step nothing on the dashboard should be polling for updates).
- Vehicle detail `subscribe_vehicle`s its vehicle so the temp chart's leading edge climbs **live** (the demo moment — `SPEC.md` §10.2); unsubscribes on unmount.
- Incident feed updates live from `incident_event`s (open/ack/resolve) without refetch.

**Verify:**
- `docker compose up` → open the overheating vehicle: its temp chart climbs into the red **and** the incident appears in the feed in the same moment, no refresh.
- **Board updates live:** a vehicle changing state / going offline reflects on its board card without a refetch.
- Drop & reconnect the socket → state restored via REST snapshot, deltas resume.

**Done when:** the end-to-end live demo (SPEC §11) is visible in the browser.

---

## Step 12 — Test suite hardening + load test + numbers

**Goal:** Make the resume numbers real and the system trustworthy.

**Read first:** `SPEC.md` §8 (bullets needing numbers), §9 M6, §10.1 (COPY option). Prereqs: Steps 1–11b.

**Tasks:**
- **Set load-test acceptance targets first** (write them into `load/README` or the top of `locustfile.py`): target vehicles, batch size, events/sec, p95 ingest latency, max duplicate rate.
- `load/locustfile.py` hammering `POST /telemetry`; run it, **record real throughput + p95 latency**.
- If the ORM insert is the bottleneck at target load, do the **`COPY`-via-staging swap** (§10.1 — `COPY` → unlogged staging table → `INSERT…SELECT…ON CONFLICT DO NOTHING RETURNING`, preserving idempotency + the inserted-row set) and **record before/after** numbers.
- Fill any remaining gaps in the pytest suite: idempotency (dup → no extra rows/counters), incident-race (one active incident), hysteresis (open/recover/flap/ack→resolve), out-of-order readings (documented behavior), and **WS reconnect → client refetches the REST snapshot and resumes deltas** (there is no WS-pushed snapshot; snapshot is REST-owned per Step 10).
- Capture a sub-100ms aggregate-read measurement for the §8 bullet.

**Verify:**
- Locust run hits the acceptance targets; numbers are written down (Progress Log + README).
- Full `pytest` suite green; `make lint` (ruff+mypy) clean.

**Done when:** real, recorded numbers back every `X`/`N` placeholder in `SPEC.md` §8.

---

## Step 13 — CI + README + finalize resume bullets

**Goal:** Project is presentable and reproducible.

**Read first:** `SPEC.md` §8, §11. Prereqs: Steps 1–12.

**Tasks:**
- **GitHub Actions** workflow, two jobs on push/PR:
  - **backend** — ruff + mypy + pytest. pytest uses the **testcontainers** harness from Step 1, so the runner only needs Docker — **no separate DB/Redis service block, no manual `alembic upgrade`**. Data-dependent tests pass because they **self-seed** (Step 1 convention), not because the simulator runs.
  - **frontend** — `npm ci` + `tsc --noEmit` + eslint. A deliberate, lightweight check: the frontend isn't the showcase and is excluded from the Python `make lint`, but a silently-broken UI looks worse to anyone browsing the repo than a small green check. (This is the conscious decision, not a silent omission.)
- **README:** architecture diagram (the `SPEC.md` §1/§4 topology), one-command quickstart (`docker compose up`), what each service does, the WS event protocol, how to run tests + load test, and the recorded numbers.
- **Finalize `SPEC.md` §8 bullets** with the real measured numbers from Step 12 — the resume entry that replaces the Cryptocurrency Dashboard.

**Verify:**
- CI passes on a clean checkout. A new reader can `docker compose up` and reach the dashboard following only the README.

**Done when:** CI green, README complete, resume bullets finalized with real numbers. **v1 done (SPEC §11).**

---

## Step 14+ — M7 stretch (optional, only after Step 13)

Each is independent; do any/none. See `SPEC.md` §9 M7, §10.7. **Do not start these at the expense of Steps 12–13.**
- WebSocket **ingest** path (alternative to HTTP batch).
- **Anomaly rule** (rolling mean ± k·σ per vehicle).
- **Error-code-triggered rules** (new rule type beyond scalar comparators, §10.12).
- **API-key auth** on `POST /telemetry` (`secrets.compare_digest`, §10.7).
- **AWS Fargate** deploy (ECS, ElastiCache, RDS/Timescale Cloud) — keep local Compose == prod topology.
- **Notification fan-out** (email/Slack on incident open).

---

## Progress Log

> Append one entry per completed (or attempted) step. Newest at the bottom. This is the authoritative handoff record between agents. **Always record seam locations** that later steps depend on.

### Step 1 — Repo scaffolding, tooling, infra-only Compose — DONE (2026-06-03)

**What I built:** the project skeleton, dependency/tooling config, the canonical
test-DB harness, and an infra-only Docker Compose. No app code (correct for Step 1).

**Dependency mgmt — `uv` (not pip).** `pyproject.toml` defines runtime deps and a
`dev` dependency-group; `uv sync` built `.venv` and wrote **`uv.lock`** (the exact
pin / `package-lock.json` analogue). Lower bounds are in `pyproject.toml`; exact
versions live in `uv.lock`. Resolved key versions: fastapi 0.115.x, sqlalchemy
2.0.50, asyncpg 0.30.x, alembic 1.14.x, celery 5.4.x, redis-py 5.3.1, pydantic
2.x, testcontainers 4.13.3, ruff 0.9.10, mypy (strict).

**Image tags pinned (match in Compose + tests):** `timescale/timescaledb:2.27.2-pg16`
(current stable, verified in Step 0) and `redis:7.4`. The testcontainers harness
hardcodes the **same** two tags (`conftest.py` constants) so tests == Compose.

**DB/user/db names:** Postgres user/password/db all `fleetpulse`; async URL
`postgresql+asyncpg://fleetpulse:fleetpulse@timescaledb:5432/fleetpulse`. Ports use
the free defaults (5432, 6379).

**Config bootstrap decision:** the Makefile `up` target copies `.env.example → .env`
if absent **and** every Compose var also has a `${VAR:-default}` fallback (belt +
suspenders — a raw `docker compose up` works with no `.env` too). `.env` is
git-ignored; only `.env.example` is committed. `.env.example` is seeded with **all**
vars later steps need (DB/Redis URLs, pool size + acquire timeout, batch/body caps,
broker queue-depth threshold, CORS origins, Vite API/WS base URLs, ingest API-key
placeholder, sim knobs) — Step 1 only actually uses the Postgres/Redis pieces.

**Files added:**
- `pyproject.toml` (deps, ruff + mypy + pytest config; lint/type scope =
  `backend/` + `simulator/`, NOT `frontend/`), `uv.lock`.
- `.gitignore`, `.env.example`, `docker-compose.yml` (infra only), `Makefile`
  (`up`/`down`/`lint`/`fmt`/`test`/`sync`), `README.md` skeleton.
- `backend/app/` package tree with `__init__.py` in every package
  (`db/ models/ schemas/ api/ services/ workers/ ws/`), `backend/alembic/.gitkeep`.
- `simulator/__init__.py` (made it a real package so mypy/ruff have a file to
  check — empty `simulator/` made strict mypy error "no .py files").
- `frontend/.gitkeep`, `load/.gitkeep` (placeholders; TS / load-test land later).

**SEAM later steps depend on — the test-DB harness (`backend/tests/conftest.py`):**
this is the canonical mechanism; **later agents must NOT stand up their own DB.**
Fixtures provided:
- `db_container` / `redis_container` (session): ephemeral TimescaleDB + Redis via
  testcontainers (pinned images).
- `database_url` / `redis_url` (session): connection URLs.
- `db_settings` (session): **the place Step 2 must add `alembic upgrade head`** —
  there's an explicit `TODO(Step 2)` marker on the exact line. Until migrations
  exist it just yields the URL so the harness can be smoke-tested.
- `db_engine` / `db_conn` (function): per-test async engine + transaction that
  **rolls back** for isolation.
- `seed_readings` (function): the reuse point for "insert N readings for a vehicle"
  — currently raises `NotImplementedError` (no `telemetry_reading` table yet);
  Step 2+ fills in the INSERT. **Tests self-seed via this; never via the simulator.**
`backend/tests/test_harness.py` is a throwaway Step-1 smoke test proving the harness
(SELECT 1, timescaledb extension available, Redis PING) — later steps can replace it.

**Deviations from the plan:** none material. Two judgment calls worth noting:
(1) made `simulator/` a package (`__init__.py`) instead of a bare placeholder dir,
because strict mypy errors on an empty directory in its `files` list; (2) added
`${VAR:-default}` fallbacks in Compose *in addition to* the `.env` bootstrap rather
than picking just one — strictly more robust, still documented.

**Verify — all passed:**
- `docker compose up -d timescaledb redis` → both report `healthy` in `docker compose ps`.
- `psql ... pg_available_extensions WHERE name='timescaledb'` → 1 row (available;
  it gets `CREATE`d in Step 2).
- `uv run pytest` → 3/3 green via the testcontainers harness (SELECT 1, extension,
  Redis ping) — proves the test-DB plumbing before any real test depends on it.
- `make up` from a clean checkout with no `.env` → bootstraps `.env`, stack starts.
- `make lint` (ruff + mypy strict) → clean.

**Notes for the next agent (Step 2):** add `alembic upgrade head` at the
`TODO(Step 2)` marker in `conftest.py::db_settings`, and implement `seed_readings`
once `telemetry_reading` exists. Remember the conventions: `telemetry_reading` has
**no surrogate `id`** (composite PK `(vehicle_id, ts)`), and the hypertable/index
DDL in Step 2 is transaction-safe (the autocommit-block requirement is Step 7 only).

---

### Step 2 — Database foundation (models, Alembic, hypertable, indexes) — DONE (2026-06-03)

**What I built:** the async DB layer, all four v1 SQLAlchemy models, and an Alembic
setup whose single migration creates the schema, turns `telemetry_reading` into a
hypertable, and adds every index/constraint from SPEC §5. Wired the test harness to
apply migrations. No app/API code (correct for Step 2).

**DB layer (`backend/app/`):**
- `config.py` — `pydantic-settings` `Settings` (database_url + pool knobs). **Key
  decision: it does NOT load `.env`.** It reads OS env vars only, with a *localhost*
  default for `database_url`. Compose injects `DATABASE_URL=...@timescaledb...` into
  containers; host runs (alembic, pytest, local uvicorn) fall through to the
  localhost default. This sidesteps the Compose-hostname-vs-localhost split cleanly
  (the issue I flagged at end of Step 1). `.env` is for Compose to source, not the app.
- `db/base.py` (`Base`), `db/session.py` (async `engine` + `async_session_maker`;
  `pool_timeout` = the acquire bound Step 4 turns into a 503), `db/__init__.py` re-exports.

**Models (`backend/app/models/`)** — exactly per SPEC §5:
- `enums.py` — Python enums (`VehicleStatus`, `RuleOperator`, `Severity`,
  `IncidentStatus`) + their native-PG-enum column types. **Decision: native Postgres
  enum types** (not VARCHAR+CHECK). They use `create_type=False` + `values_callable`
  so (a) the migration owns `CREATE TYPE` (avoids SQLAlchemy double-creating the
  shared `severity` type used by both `alert_rule` and `incident`), and (b) the DB
  stores each member's **value** — important for `RuleOperator` where value `>` ≠ name `gt`.
- `vehicle.py`, `telemetry.py` (**no surrogate id**; composite PK `(vehicle_id, ts)`;
  metrics nullable — real telemetry has gaps; `error_codes` JSONB default `'[]'`),
  `alert_rule.py`, `incident.py`. SQLAlchemy 2.0 `Mapped[...]`/`mapped_column` typing
  throughout (passes strict mypy).

**Alembic (`backend/alembic*`):**
- `alembic.ini` uses `%(here)s` so `alembic -c backend/alembic.ini …` works from any
  CWD. `env.py` is **async** (asyncpg via `connection.run_sync`) and resolves the DB
  URL by precedence: **`-x db_url=` > `ALEMBIC_DB_URL` env > `settings.database_url`**.
- `versions/0001_initial_schema.py` (revision `0001`, down_revision `None`): in order —
  `CREATE EXTENSION timescaledb` → `CREATE TYPE` ×4 → `create_table` ×4 (composite PK on
  telemetry_reading) → `create_hypertable('telemetry_reading','ts', if_not_exists)` →
  indexes: `telemetry_reading (vehicle_id, ts DESC)`, partial `alert_rule WHERE enabled`,
  `incident (status, severity, vehicle_id)`, and partial-unique
  `uq_incident_active (vehicle_id, rule_id) WHERE status != 'resolved'`. `downgrade()`
  drops tables (FK-reverse order) then the enum types — clean round-trip. All DDL is
  transaction-safe (no autocommit-block needed; that's Step 7).

**Test harness wiring:**
- `conftest.py::db_settings` now runs `alembic upgrade head` against the testcontainers
  DB (via `ALEMBIC_DB_URL`) — the seam Step 1 left. **`seed_readings` is now
  implemented** (upserts a vehicle by external_id, inserts N readings; returns internal
  `vehicle.id`) — Steps 6/7 reuse it; tests self-seed, never via the simulator.
- `test_schema.py` (5 tests): hypertable registered, composite PK is exactly
  `(vehicle_id, ts)`, `uq_incident_active` is UNIQUE + PARTIAL, all 4 enum types exist,
  and a vehicle+reading insert + the seed helper both work.

**Tooling change:** excluded `backend/alembic/` from ruff + mypy (migration/env
boilerplate — generated shape, late imports), and added `known-third-party=["alembic"]`
to ruff isort (the local `backend/alembic/` dir made isort misclassify the `alembic`
*package* as first-party). Noted in `pyproject.toml`.

**Deviations from the plan:** none on the schema itself. One judgment call to flag:
**I implemented `seed_readings` now** (Step 1 had left it a stub) since the table
exists and it's part of the harness contract — it's generic (constant metric values),
so if a later step needs richer sequences it can extend, not rewrite.

**Verify — all passed:**
- `alembic upgrade head` against the **Compose DB** (localhost:5432) → applied, `current` = `0001 (head)`.
- psql: `telemetry_reading` in `timescaledb_information.hypertables` ✓; PK cols = `{vehicle_id, ts}` ✓; `uq_incident_active` is `UNIQUE … WHERE status <> 'resolved'` ✓; all 5 tables present.
- `alembic downgrade base` → only `alembic_version` left, **zero** leaked enum types; `upgrade head` → hypertable back. Clean round-trip.
- `uv run pytest` → **9/9 green** (3 harness + 5 schema + 1 seed) via testcontainers.
- `make lint` (ruff + mypy strict) → clean.

**Seams / facts the next agent (Step 3) needs:**
- **Migration ownership:** Step 3 adds the one-shot `migrate` Compose service that runs
  `alembic upgrade head`; in-container it should run `alembic -c <path>/alembic.ini upgrade head`
  with `DATABASE_URL` pointing at the `timescaledb` service (env.py picks it up via
  `settings.database_url`, since no `-x`/`ALEMBIC_DB_URL` is set in-container). No app
  service runs migrations itself.
- `app/config.py::settings` and `app/db/session.py` (`engine`, `async_session_maker`)
  are ready for Step 3's request-scoped session dependency + `/health` `SELECT 1`.
- Enum **values** stored in DB are the symbols/words (`>`, `active`, …), not enum names —
  matters when Step 8 seeds `alert_rule` rows and Step 9 reads them back.
- To run alembic from the **host** (tests already do this automatically), pass
  `-x db_url=postgresql+asyncpg://fleetpulse:fleetpulse@localhost:5432/fleetpulse`.

---

### Step 3 — FastAPI skeleton + health + DB session + migrate/api containers — DONE (2026-06-03)

**What I built:** a running FastAPI service in Compose with a DB-backed health check,
a request-scoped async session dependency, CORS, structured logging, the backend
Docker image, and the one-shot `migrate` service that owns migrations + the `api`
service that depends on it. No business endpoints yet (correct for Step 3).

**App (`backend/app/`):**
- `main.py` — `create_app()` factory (uvicorn runs it with `--factory`). Adds CORS
  middleware (origins from `settings.cors_origins_list`), mounts `api_router` at
  `/api/v1`, calls `configure_logging()`. **It does NOT run migrations.**
- `api/__init__.py` — `api_router` aggregator (later steps add their routers here).
  `api/health.py` — `GET /api/v1/health` → `{status, db}`, does `SELECT 1` via the
  session dep. `api/deps.py` — `SessionDep = Annotated[AsyncSession, Depends(get_session)]`
  (Annotated form, so ruff B008 never fires).
- `db/session.py` — added `get_session()` per-request async dependency.
- `schemas/health.py` — `HealthResponse`. `log_config.py` — `configure_logging()`
  (dependency-free key=value format; Step 13 can swap to JSON).
- `config.py` — added `cors_origins` (+ `cors_origins_list` property) and `redis_url`.

**Docker (`backend/Dockerfile`, `.dockerignore`):**
- `python:3.12-slim`; uv (pinned `ghcr.io/astral-sh/uv:0.11.18`) installs deps from
  the lockfile (`uv sync --frozen --no-dev --no-install-project`). **Build context is
  the repo root** (pyproject/uv.lock/README live there); `dockerfile: backend/Dockerfile`.
  `backend/` is copied to `/app` and run from source via **`PYTHONPATH=/app`** (the
  `app` package isn't pip-installed — needed because uvicorn/alembic console scripts
  don't auto-add CWD to sys.path). `.dockerignore` keeps tests/.venv/caches out of the image.

**Compose (`docker-compose.yml`):**
- A YAML anchor `x-backend` shares build/image/env across the two backend services
  (same `fleetpulse-backend:local` image). **`environment:` uses `${VAR:-default}`
  with in-container hostnames** (`@timescaledb`, `@redis`) as defaults — so the stack
  works with or without `.env` (matches Step 1's infra pattern; no `env_file` needed).
- **`migrate`**: `command: alembic upgrade head`, `depends_on` timescaledb healthy,
  `restart: "no"` (one-shot). The ONLY migration runner. In-container, env.py resolves
  the URL from the injected `DATABASE_URL` (no `-x`/`ALEMBIC_DB_URL`).
- **`api`**: `depends_on` migrate `service_completed_successfully` + redis healthy;
  uvicorn factory; port `${API_PORT:-8000}:8000`; healthcheck via a `python -c urllib`
  one-liner (slim image has no curl).

**Test harness:** added a reusable **`client`** fixture to `conftest.py` — spins up the
app and **overrides `get_session`** to bind to the testcontainers DB (the canonical
API-test entry point for Steps 4/6/7/9b). `test_health.py` asserts `200 {status:ok, db:ok}`.
⚠️ Note recorded in the fixture: `client` requests **commit** through real sessions (no
rollback isolation like `db_conn`) — write-path tests in Step 4 should use distinct ids
or truncate between tests.

**Tooling:** added `[tool.ruff.lint.flake8-bugbear] extend-immutable-calls` for FastAPI
DI markers (Depends/Query/Path/Header/Body) so future routers with arg-default DI don't trip B008.

**Deviations from the plan:** none. Two judgment calls: (1) used the `Annotated` DI form
instead of `Depends()` defaults (cleaner, lint-clean); (2) added the reusable `client`
fixture to the shared harness now rather than inline in `test_health.py`, since Step 4+
need it — consistent with the "establish the harness once" convention.

**Verify — all passed:**
- `docker compose build` → `fleetpulse-backend:local` built. `docker compose config` valid (anchors OK).
- **Fresh-volume run** (`down -v` → `up -d`): migrate log shows `Running upgrade -> 0001`,
  migrate exit code **0**, api gated on its completion → reached **healthy**. Proves the
  migrate service genuinely applies the schema (not relying on a pre-migrated DB).
- `curl localhost:8000/api/v1/health` → `200 {"status":"ok","db":"ok"}`.
- `uv run pytest` → **10/10 green** (added `test_health` via the `client` fixture). `make lint` clean.

**Seams / facts the next agent (Step 4 — ingest) needs:**
- Add the `telemetry` router under `app/api/` and `include_router` it in `app/api/__init__.py`
  (alongside `health`). Use `SessionDep` from `app/api/deps.py` for the DB session.
- The `client` fixture is ready for ingest endpoint tests — but it **commits**; plan a
  truncate-between-tests step (or unique ids) for write-path tests. `db_conn` (rollback)
  remains for pure-DB tests; `seed_readings` is available for seeding vehicles/readings.
- Back-pressure inputs already surfaced in `settings`: `db_pool_acquire_timeout_seconds`
  (pool exhaustion → 503) and the body/batch caps live in `.env.example`
  (`MAX_BATCH_READINGS`, `MAX_BODY_BYTES`) — add the matching fields to `Settings` in Step 4.
- Body-size cap needs **ASGI middleware** (Starlette has no built-in max-body); the
  app factory in `main.py` is where to add it.
