# FleetPulse — Fleet Telemetry & Alerting Platform — Project Spec

_Last updated: 2026-06-03. This is the source-of-truth requirements doc. **This is a backend-focused portfolio project**: the technically interesting work lives in ingestion, the time-series layer, the async alerting pipeline, and real-time streaming. The frontend is a clean operations dashboard, not the showcase._

---

## 1. Vision

A telemetry monitoring platform for a fleet of vehicles. A simulated fleet continuously emits telemetry (location, speed, state-of-charge, motor temperature, error codes); the platform **ingests it at scale, stores it as time-series, evaluates alert rules in the background, opens/resolves incidents, and streams a live operations dashboard** over WebSockets.

**Why this project, for this resume:** It directly fills the biggest gap — every prior project (Roadmap, Playlist Converter, Crypto Dashboard) is Node/JS frontend-heavy, while the resume *claims* Python. FleetPulse is **Python-first** and exercises the "systems" layer those projects don't: high-throughput async ingestion, background workers, time-series modeling, caching, and real-time push. It also leans on real lived experience — two years on autonomous-vehicle **fleet monitoring, telemetry, and incident response at Cruise** — so it's defensible in interviews rather than tutorial-shaped. It replaces the **Cryptocurrency Dashboard** on the resume.

**This is a portfolio project, not a commercial product.** Decisions favor resume-relevance and backend learning (async FastAPI, Celery, TimescaleDB, WebSockets, Docker) over monetization or true scale. The architecture should be the kind a real product would use, sized to a single-developer build of a few weeks.

---

## 2. MVP scope (v1)

The MVP must nail the full telemetry loop end to end: **simulated fleet → ingest → store → evaluate rules → open incidents → live dashboard.** Everything else is deferred.

**In scope for v1:**
- **Fleet simulator** (standalone Python service) that spins up N virtual vehicles, each emitting realistic telemetry on an interval, including occasional fault scenarios (overheating, battery sag, error codes, dropped/offline) so the alerting layer has something to catch.
- **Async ingestion API** (FastAPI) accepting **batched** telemetry over HTTP, with Pydantic validation, idempotency, and back-pressure handling. (A WebSocket ingest path is a stretch goal; HTTP batch is the v1 contract.)
- **Time-series persistence** in TimescaleDB (Postgres + hypertables), with **continuous aggregates** powering fast dashboard rollups (per-minute/per-hour summaries over raw readings).
- **Async alerting pipeline** — Celery workers evaluate configurable **alert rules** (scalar threshold + count-based hysteresis windows) against incoming telemetry, decoupled from the ingest hot path via Redis, with **hysteresis** to avoid alert flapping. _(Rolling-stddev anomaly checks are an M7 stretch, not v1 — §9.)_
- **Incident lifecycle** — a tripped rule **opens** an incident (vehicle, rule, severity, opened_at); recovery **resolves** it (resolved_at). Incidents are first-class, queryable, and acknowledgeable.
- **Real-time operations dashboard** (React + TS) — live fleet overview (status per vehicle), per-vehicle detail with telemetry time-series charts, and a live incident feed, all pushed over **WebSockets** (no polling).
- **Redis-cached hot state** — latest reading + status per vehicle served from cache, not a table scan.
- **Tests** (pytest) covering the rule engine, ingestion validation, and incident transitions, plus a **load test** (Locust/k6) to produce real throughput/latency numbers for resume bullets.
- **Docker Compose** one-command spin-up of the whole topology (api, worker, beat, redis, timescaledb, simulator, web).

**Explicitly NOT in v1** (see Roadmap):
- Real auth / multi-tenant org model / RBAC (single implicit operator in v1; auth is Phase 2).
- Real hardware or real vehicle APIs — everything is simulated.
- Map tiles / geospatial routing (show coordinates / a simple map marker; no routing/geofencing engine in v1).
- Notification fan-out to email/SMS/Slack (incidents surface in-app only in v1).
- Cloud deploy (local Docker is the v1 target; AWS is a stretch goal — see §10).

---

## 3. Core flow (v1)

1. **Simulator boots** N vehicles (pre-registered, §5) and begins emitting telemetry batches to the ingest API on a fixed cadence (e.g. every 1–5s per vehicle, batched).
2. **Ingest API** validates the **whole batch** (Pydantic; any malformed reading → `422`, nothing written — see §10.8), then writes raw readings to the TimescaleDB hypertable with `INSERT ... ON CONFLICT (vehicle_id, ts) DO NOTHING RETURNING ...`. It updates each vehicle's hot state in Redis, **publishes telemetry deltas** to the `telemetry` pub/sub channel, and **enqueues only the rows that actually inserted** for rule evaluation (one task per vehicle, §10.10) — then returns fast, without blocking on rule work. Enqueuing only inserted rows is what makes idempotency mean "no double-alert," not just "no double-write."
3. **Celery workers** pull each per-vehicle task (readings sorted by `ts`), run them through the **rule engine**, and apply hysteresis via atomic, `ts`-aware Redis counters: a rule whose condition holds for `>= N` consecutive readings **opens an incident** (guarded by a partial unique index so concurrent workers can't double-open); sustained recovery past the dead band **resolves** it.
4. **Incident events** are published to the `incidents` pub/sub channel — `open`/`resolve` by the **worker** (which detects them), and `ack` by the **API** (where acknowledgement happens, §6). The web process's subscriber relays both channels to connected dashboards over WebSockets. (Telemetry deltas come from the API in step 2.)
5. **Operator** watches the live fleet board, drills into a vehicle to see its telemetry charts (served from continuous aggregates), and acknowledges/reviews incidents in the live feed. The board reads from cache with a fallback to the `vehicle` table so offline vehicles (expired from cache) still appear.

---

## 4. The technically interesting parts (the portfolio centerpiece)

These are the pieces to build well and talk about in interviews.

### 4.1 Async ingestion under back-pressure
- FastAPI `async` endpoint, **batched** payloads (one request carries many readings from many vehicles) to amortize per-request overhead.
- **Pydantic v2** models for strict **whole-batch** validation — any malformed reading rejects the entire batch with `422` and writes nothing (deterministic, simple to test; §10.8). Producer resends the corrected batch.
- **Idempotency**: dedupe on the `(vehicle_id, ts)` natural key via a unique constraint + `INSERT ... ON CONFLICT DO NOTHING RETURNING vehicle_id, ts`. Only the **returned (actually-inserted)** rows are cached + enqueued, so a retried batch causes neither a double-write nor a double-alert. (No client-supplied `reading_id` in v1 — see §10.3.)
- **Back-pressure (concrete policy, §10.9)**: cap request body (~1 MB) and batch size (≤ 500 readings) → `413`/`422` over the cap; bounded async DB pool with a short acquire timeout → `503` on pool exhaustion; reject with `503` + `Retry-After` when the Celery broker's pending-task depth exceeds a threshold (shed load rather than buffer unboundedly). "Back-pressure" means *bounded queues that push back*, not "Redis buffers forever."
- **Decouple ingest from alerting**: the request path does the minimum (validate → persist → cache → publish telemetry delta → enqueue inserted rows) and returns; all rule work happens off the hot path in Celery. This separation is the whole point and the headline bullet.
- Connection pooling (asyncpg / SQLAlchemy async) sized deliberately; document the pool config.

### 4.2 Time-series modeling
- **TimescaleDB hypertable** for `telemetry_reading`, partitioned by time (and optionally by `vehicle_id`).
- **Continuous aggregates** for per-minute and per-hour rollups (avg/max motor_temp, min battery, etc.) so the dashboard never scans raw rows for charts.
- **Retention/compression policy** on raw data (e.g. compress > 7 days) — a realistic ops touch and a good talking point.

### 4.3 The rule engine
- Rules are **data, not code**: `metric`, `operator` (`> >= < <= ==`), `threshold`, `recovery_threshold`, `severity`, `window_count` (N consecutive readings). Stored in Postgres, editable without redeploy. **v1 metrics are scalar only** (`motor_temp_c`, `soc_pct`, `speed_kph`, …); `error_codes` is an array and doesn't fit the scalar comparator, so error-code-triggered rules are a Phase 2 add (§10.12). Time-based windows are also Phase 2 (§10.4).
- Engine is **vehicle-agnostic and metric-agnostic** — it only sees a normalized reading shape, so adding a scalar metric or rule is config, not a code change.
- **Hysteresis / debounce** prevents flapping (the classic "alert storm" failure mode — directly informed by Cruise incident-response experience): an incident opens only after the condition holds for `window_count` consecutive readings, and resolves only after the `recovery_threshold` dead band is cleared for `window_count` consecutive readings. Counters are per-`(vehicle, rule)`, atomic and `ts`-aware in Redis (§10.10).
- Simple **anomaly check** beyond static thresholds as a stretch: rolling mean ± k·stddev over a short window per vehicle.

### 4.4 Real-time streaming
- **WebSocket** endpoint (`/ws/fleet`) pushes (a) live telemetry deltas and (b) incident open/ack/resolve events. **Publishers, by design:** the **ingest API** publishes telemetry deltas (it has the reading at write time) **and** the `ack` incident event (acknowledgement happens via the API); **Celery workers** publish `open`/`resolve` incident events (they detect them). All go to Redis pub/sub channels; the web process subscribes and relays. **All client-facing payloads key vehicles by `external_id`** (the internal `vehicle_id` PK never crosses the wire), so an `incident_event` carries `vehicle_external_id` and the frontend can map it to a board card.
- **Redis pub/sub** fans out to the FastAPI process(es) holding the WebSocket connections — so producers and the web layer stay decoupled and it scales horizontally (run M web processes, all subscribe).
- **Volume control:** the board stream is throttled/sampled (it only needs current status, not every reading); the per-vehicle detail view subscribes to just that one vehicle's full-rate stream. Avoids broadcasting every reading to every client.
- **WebSocket is best-effort, not the source of truth.** Redis pub/sub is at-most-once; a client can miss events across a reconnect. Mitigation: on connect the client pulls a **REST snapshot** (fleet state from cache + open incidents from Postgres), then applies deltas. The durable truth is always Postgres (incidents) + the REST read APIs; the socket is just the live layer.

---

## 5. Data model (v1)

**Postgres / TimescaleDB:**

- **`vehicle`** (durable, low cardinality) — `id` (internal PK), `external_id` (the fleet identifier producers use, e.g. `VH-0007`), `name`, `model`, `status` (`active|idle|offline|maintenance`), `last_seen_at`, `created_at`. **Vehicles are pre-registered** (seeded / sim registers on boot); an unknown `external_id` in a batch is a validation failure (§10.8, §10.11). **`status` ownership:** the durable `status`/`last_seen_at` is written by the **offline-detection beat job** (marks a vehicle `offline` when `last_seen_at` ages out) and refreshed on ingest; the Redis cache holds the live derived view. One writer of durable status (the beat job + ingest touch) avoids split-brain.
- **`telemetry_reading`** (**hypertable**, high volume) — `vehicle_id` (FK → `vehicle.id`), `ts`, `lat`, `lon`, `speed_kph`, `soc_pct` (state of charge), `motor_temp_c`, `odometer_km`, `error_codes` (text[]/jsonb). **Unique `(vehicle_id, ts)`** for idempotency (§10.3). Primary time-series table.
- **`telemetry_1m` / `telemetry_1h`** — **continuous aggregates** (real-time mode, §10.2) over `telemetry_reading` for dashboard charts.
- **`alert_rule`** — `id`, `name`, `metric` (scalar metric name only, §4.3), `operator`, `threshold`, `recovery_threshold`, `severity` (`info|warning|critical`), `window_count` (N consecutive readings), `enabled`. _(No `window_seconds` in v1 — time-based windows are Phase 2.)_
- **`incident`** — `id`, `vehicle_id`, `rule_id`, `severity`, `status` (`open|acknowledged|resolved`), `opened_at`, `acknowledged_at`, `resolved_at`, `opened_value` (the reading that tripped it), `note`. **Partial unique index `UNIQUE (vehicle_id, rule_id) WHERE status != 'resolved'`** so concurrent workers can't open a second active incident for the same vehicle+rule.
- **Indexes:** `telemetry_reading (vehicle_id, ts DESC)` (latest-per-vehicle + range reads), partial index on `alert_rule WHERE enabled`, `incident (status, severity, vehicle_id)` for the feed filters, plus the partial-unique above.

**Redis (transient) — namespaced keys, explicit TTLs:**
- `fleet:vehicle:{external_id}:state` — latest reading + derived live status (hot cache for the board + the REST snapshot the client fetches on WS connect). Short TTL, refreshed on every ingest; board read falls back to Postgres on miss (offline vehicles).
- `fleet:eval:{vehicle_id}:{rule_id}` — atomic, `ts`-aware hysteresis counter + `last_eval_ts` + open-incident flag (§10.10).
- **Celery broker + result backend**, with `task_ignore_result=True` for fire-and-forget rule tasks (no pointless result writes).
- **pub/sub channels** — `fleet:incidents` (worker-published), `fleet:telemetry` (API-published) — fan-out to WebSocket-holding web processes.

---

## 6. API contract (v1, sketch)

REST (FastAPI, `/api/v1`):
- `POST /telemetry` — ingest a batch of readings. Body: `{ readings: [{ vehicle_external_id, ts, lat, lon, speed_kph, soc_pct, motor_temp_c, odometer_km, error_codes[] }] }`. Producers reference vehicles by **`vehicle_external_id`** (the fleet ID), which the API resolves to the internal `vehicle.id`. **Whole-batch validation:** any malformed reading or unknown vehicle → `422` (nothing written). On success → `202 Accepted` with `{ inserted_count, duplicate_count }` (duplicates = rows skipped by `ON CONFLICT`). (No partial acceptance / per-row errors in v1 — §10.8.)
- `GET /vehicles` — fleet board: each vehicle + current status + latest reading (from cache).
- `GET /vehicles/{id}` — vehicle detail.
- `GET /vehicles/{id}/telemetry?from&to&resolution=1m|1h|raw` — time-series for charts (served from continuous aggregates unless `raw`).
- `GET /incidents?status=&severity=&vehicle_id=` — incident list/feed.
- `POST /incidents/{id}/acknowledge` — ack an open incident.
- `GET /rules` / `POST /rules` / `PATCH /rules/{id}` — manage alert rules.

WebSocket:
- `WS /ws/fleet` — **deltas only.** Client→server messages: `subscribe_vehicle` / `unsubscribe_vehicle`. Server→client messages: `telemetry_delta`, `incident_event`. The **initial snapshot is fetched over REST** (the read endpoints above), not pushed over the socket; on reconnect the client refetches the REST snapshot and resumes deltas. This matches §4.4 (WS is best-effort; REST + Postgres are the source of truth).

---

## 7. Tech stack

### Backend (the focus)
- **Python 3.12 + FastAPI** — async-first; the right tool for high-throughput ingest and WebSocket streaming, and the most in-demand Python backend skill for new roles.
- **Pydantic v2** for request/response models and validation.
- **SQLAlchemy 2.x (async) + asyncpg**, **Alembic** for migrations.
- **TimescaleDB** (Postgres 16 extension) — hypertables, continuous aggregates, retention/compression.
- **Celery + Redis** — Redis as broker/result backend (`task_ignore_result=True` for rule tasks) **and** hot-state cache **and** hysteresis-counter store **and** pub/sub bus, all under namespaced `fleet:*` keys with explicit TTLs. **Celery beat** for periodic jobs: **offline-detection** (age out `last_seen_at` → mark `offline`). _(Retention/compression are **Timescale-managed** policy jobs created in a migration, not Celery beat; no aggregate-refresh job — continuous aggregates run in real-time mode, §10.2.)_
- **WebSockets** via FastAPI/Starlette, fanned out from the API (telemetry + `ack`) and workers (incident `open`/`resolve`) via Redis pub/sub.
- **pytest** (+ `pytest-asyncio`, `httpx` test client, factory fixtures) for unit/integration tests.

### Fleet simulator
- Standalone **Python** service (its own container). Spins up N vehicles as async tasks, each with a small state machine (driving / idle / charging / fault), emitting batched telemetry to the ingest API. Fault injection (overheat, battery sag, error codes, go-offline) is configurable so demos reliably trigger incidents.

### Frontend (clean, not the showcase)
- **Vite + React + TypeScript.**
- **TanStack Query** for REST reads; a thin **WebSocket** client for the live stream.
- Charts via a lightweight lib (e.g. Recharts/uPlot). A simple map marker view for coordinates (no heavy GIS).
- Goal: a credible, readable **operations dashboard** — fleet board, vehicle detail with charts, live incident feed. Polished but intentionally restrained; the backend is the story.
- _(Optional: the editorial design system from the music_platform project (`tokens.css`) could be reused for a distinctive look, but matching that bar is explicitly out of scope here.)_

### Infrastructure
- **Docker Compose** from day one: `api`, `worker`, `beat`, `redis`, `timescaledb`, `simulator`, `web`. One command brings up the whole loop.
- **GitHub Actions** CI: lint (ruff) + type-check (mypy) + pytest on push.
- **Load testing** with **Locust** (or k6) against the ingest endpoint to produce real throughput/latency numbers.
- **AWS deploy is a stretch goal** (ECS Fargate for api/worker, ElastiCache Redis, RDS/Timescale Cloud). Keep local Docker == prod topology so nothing hard-depends on AWS.

---

## 8. Resume bullets this targets

Draft bullets to back with **real measured numbers** from the load test and implementation. These replace the Cryptocurrency Dashboard entry.

**FleetPulse — Fleet Telemetry & Alerting Platform** | _Python, FastAPI, Celery, Redis, TimescaleDB, WebSockets, Docker_
- Built an **async FastAPI ingestion service** processing **~X telemetry events/sec** from a simulated fleet of **N vehicles**, using batched Pydantic-validated payloads and idempotent writes, with rule evaluation decoupled from the ingest hot path via a Redis-backed Celery pipeline.
- Designed a **configurable, data-driven rule engine** with hysteresis (separate trip/recovery thresholds) that eliminates alert flapping — verified by a replay test where a threshold-hovering signal produced **N** open/resolve cycles under naive thresholding vs. **1** incident with hysteresis (informed by autonomous-fleet incident-response experience).
- Modeled high-volume telemetry in **TimescaleDB hypertables with continuous aggregates and compression**, serving **sub-Xms** dashboard rollups over **millions of rows**.
- Streamed live fleet state and incidents to a **React dashboard over WebSockets** via Redis pub/sub fan-out, eliminating client polling and supporting **N concurrent operators**.
- Containerized the full topology (api, workers, beat, Redis, TimescaleDB, simulator, web) with **Docker Compose** and **GitHub Actions CI** (ruff, mypy, pytest).

---

## 9. Milestones (build order)

Sequenced so something runs end-to-end early, then depth is layered on.

1. **M1 — Skeleton + ingest path.** FastAPI app, Pydantic models, async SQLAlchemy + TimescaleDB hypertable, Alembic, `POST /telemetry` persisting readings. Docker Compose with api + timescaledb. _Done when: a curl batch writes rows._
2. **M2 — Simulator.** Python simulator emitting realistic batched telemetry for N vehicles with fault injection. _Done when: data flows continuously without me curling._
3. **M3 — Cache + read API.** Redis hot-state on ingest; `GET /vehicles`, `GET /vehicles/{id}`, telemetry read endpoint. Continuous aggregates for rollups. _Done when: REST returns live fleet state + chartable series._
4. **M4 — Alerting pipeline.** Celery + Redis broker; data-driven rule engine with hysteresis (trip/recovery dead band, `ts`-aware atomic counters); `alert_rule` + `incident` tables with the active-incident partial unique index; enqueue-only-inserted wiring; one task per vehicle. Includes the **flap-prevention replay test** (naive vs. hysteresis) that backs the §8 bullet. _Done when: a simulated overheat opens then resolves exactly one incident, and the flap test passes._
5. **M5 — Real-time dashboard.** WebSocket endpoint + Redis pub/sub fan-out (API publishes telemetry, workers publish incidents); REST snapshot-on-connect; throttled board stream + per-vehicle detail subscription; React dashboard (fleet board with cache→table fallback, vehicle detail + charts, live incident feed). _Done when: an incident appears live without refresh, and survives a socket reconnect via snapshot._
6. **M6 — Hardening + numbers.** Set **load-test acceptance targets first** (target vehicles, batch size, events/sec, p95 ingest latency, max duplicate rate), then Locust the ingest path to those targets and record real numbers; if the ORM insert is the bottleneck, do the `COPY` swap (§10.1) and record the delta. pytest suite covering **idempotency** (dup batch → no extra rows, no counter increment), **incident race** (concurrent eval → one active incident), **hysteresis** (open/recover/flap/ack→resolve), and **out-of-order readings** (documented behavior). ruff/mypy, GitHub Actions CI, README with architecture diagram. _Done when: the resume bullets have real numbers behind them._ **Protect this milestone — it's where the resume numbers come from; cut M7 before cutting M6.**
7. **M7 (stretch, genuinely optional).** WebSocket ingest path; anomaly rule (rolling mean ± k·σ); error-code-triggered rules; API-key auth on ingest (`secrets.compare_digest`); AWS Fargate deploy; notification fan-out.

---

## 10. Decisions (resolved)

_These were the open questions; all resolved in discussion 2026-06-03. Rationale kept so it survives across sessions._

1. **ORM vs. raw for the hot path → batched ORM `insert()` first; `COPY` only if load-testing proves it's the bottleneck.** M1 uses a single multi-row SQLAlchemy `insert() ... ON CONFLICT DO NOTHING RETURNING` per batch (clean, typed, idempotent, returns inserted rows for enqueue). M6 deliberately load-tests the ingest path with Locust, cranking the event rate to find where it bends; **if** the insert path is the bottleneck, swap to raw asyncpg and **record the before/after delta** as the throughput bullet. **Caveat:** Postgres `COPY` (`copy_records_to_table`) does **not** support `ON CONFLICT` or `RETURNING`, so the `COPY` path can't dedupe inline — it would be `COPY → unlogged staging table → INSERT…SELECT…ON CONFLICT DO NOTHING RETURNING` to preserve idempotency + the inserted-row set (the second insert may itself become the new bottleneck). At portfolio scale we likely never need `COPY`; this is noted so "swap to COPY" doesn't silently break idempotency. The optimization is a goal-with-a-measured-number, not a "maybe."
2. **Continuous aggregate refresh → real-time aggregates.** Charts are trend/history over per-minute & per-hour buckets; the *live current value* is already served instantly from Redis hot-state over WebSocket, so only the newest bucket's freshness is in question. Real-time aggregates keep the chart's leading edge current so the key demo moment — watching motor-temp climb into the red as the incident fires — isn't undercut by a lagging line. Per-query cost is trivial at this scale; periodic-refresh noted in README as the "at real scale" alternative.
3. **Idempotency key → `(vehicle_id, ts)` natural key**, enforced via unique constraint + `INSERT ... ON CONFLICT DO NOTHING`. Plays cleanly with the hypertable rule that a unique index must include the time partition column. **No `reading_id` in v1** — easy to add later if we want a producer-supplied event-id / tracing story.
4. **Hysteresis → count-based trigger + recovery dead band.** A rule opens when its condition holds for **N consecutive readings** (deterministic, trivial to unit-test) and resolves only after the **recovery condition** holds for N consecutive readings — `threshold` and a separate `recovery_threshold` create a dead band that kills flapping (the alert-storm failure mode from Cruise incident-response experience). Time-based windows deferred to Phase 2. **Per-(vehicle, rule) evaluation counters live in Redis** (fast, shared across workers); durable **incident records live in Postgres**.
5. **Simulator fidelity → Tier 2: state machine + smooth/continuous signals + deterministic fault injection.** Each vehicle has a `driving/idle/charging/offline` state; metrics walk plausibly (temp rises under load, SoC drains driving / rises charging) rather than teleporting, so charts read as real telemetry. **Scheduled fault scenarios first** (overheat, battery sag, go-offline — zero interaction needed to demo the full loop); a live fault-trigger knob is a nice-to-have. No physics sim. Knobs: vehicle count, emit interval, batch size.
6. **WebSocket delivery → Redis pub/sub fan-out, built properly in M5.** Workers detect incidents but sockets live in the web process; in-memory-only would force polling or moving rule eval into the web process (both break the decoupling). The **API** publishes telemetry deltas + the `ack` event; **workers** publish incident `open`/`resolve`; the web process runs a background subscriber that relays to its own local set of connected sockets. Redis is already in the stack, so this is near-free and gives the horizontal-scale story.
7. **Auth → no user auth in v1; API key on ingest only at deploy time.** Roadmap already proves session auth + password hashing, so rebuilding login here adds no new story and burns M-time better spent on telemetry/alerting. Dashboard + read API stay open locally; full user auth is an honest Phase 2 item. The `POST /telemetry` endpoint gets a simple **API-key** check (FastAPI dependency comparing an `X-API-Key`/`Authorization` header against an env var using **`secrets.compare_digest`**, not `==`) — a *device-auth* story distinct from Roadmap's session auth — wired in as an M7 item tied to the AWS deploy, since locally in Docker Compose it's pure friction.

### Resolved from spec review (2026-06-03)

8. **Ingest validation → whole-batch reject.** Any malformed reading or unknown `vehicle_external_id` rejects the entire batch with `422`; nothing is written. Chosen over partial-acceptance for v1: deterministic, simplest to test, and a clean contract. Success returns `202` + `{ inserted_count, duplicate_count }`. Partial-acceptance (accept valid rows, report per-row errors) is noted as a more "realistic telemetry" Phase 2 option.
9. **Back-pressure → bounded queues that push back.** Concrete v1 limits (all tunable): request body ~1 MB and batch ≤ 500 readings (over → `413`/`422`); bounded async DB pool with short acquire timeout (exhaustion → `503`); reject with `503` + `Retry-After` when the Celery broker's pending-task depth exceeds a threshold. The point is shed-load, not unbounded Redis buffering — so "back-pressure handling" is a real mechanism, not a claim.
10. **Concurrency / ordering → pragmatic, with a documented limit.** One Celery task **per vehicle per batch** (never per-reading — per-reading dispatch overhead would dominate and sabotage the throughput number), readings sorted by `ts`. Hysteresis counters are updated via an **atomic, `ts`-aware** Redis op that tracks `last_eval_ts` and ignores readings older than it. This is correct *within* a batch and robust to lost updates; **cross-batch reordering** (two same-vehicle batches in flight on different workers) is handled best-effort and **documented as a known limitation**. The fully-correct alternative — partitioning vehicles across dedicated single-concurrency queues to serialize per vehicle — is noted as the scale answer but deliberately out of scope for v1.
11. **Vehicle identity → producers use `vehicle_external_id`; vehicles pre-registered.** Ingest payloads carry the fleet identifier (`VH-0007`), resolved server-side to the internal `vehicle.id` PK (which `telemetry_reading` FKs to). Vehicles are seeded / registered by the simulator on boot; an unknown `external_id` is a `422` (consistent with whole-batch reject), not auto-created — keeps identity explicit and the internal PK clean for a future "claim into an account" path.
12. **Rule metrics → scalar only in v1.** The `metric/operator/threshold` comparator handles scalar fields (`motor_temp_c`, `soc_pct`, `speed_kph`, …). `error_codes` is an array and doesn't fit; error-code-triggered rules (e.g. "any code in set present") are a Phase 2 rule type. Telemetry still *stores* `error_codes` in v1; it's just not a rule input yet.
13. **`vehicle.status` ownership → single writer.** Durable `status`/`last_seen_at` is owned by ingest (touch `last_seen_at`) + the offline-detection beat job (ages it out → `offline`); the Redis cache holds the live derived view only. The board reads cache with a **fallback to the `vehicle` table** so an offline vehicle (cache expired) still renders — avoiding the "vanishes exactly when you want to see it red" bug.

---

## 11. Definition of done (v1)

`docker compose up` brings the whole system online: a simulated fleet streams telemetry into an async FastAPI ingestion service, readings land in TimescaleDB hypertables with continuous aggregates, a Celery/Redis pipeline evaluates rules and opens/resolves incidents with hysteresis, and a React dashboard shows the live fleet board, per-vehicle telemetry charts, and a real-time incident feed over WebSockets — with a pytest suite green, a Locust run producing real throughput/latency numbers, CI passing, and a README explaining the architecture. The Cryptocurrency Dashboard comes off the resume; FleetPulse goes on.
