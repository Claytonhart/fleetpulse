# Docker & Step 0 — Personal Reference

_Created 2026-06-03. A plain-language record of the Step 0 environment setup and the
Docker smoke tests, written so future-me can remember **what** was run, **why**, and
**how** to do it again. Not part of the build plan — this is a learning/reference note._

---

## 1. Core Docker vocabulary (the mental model)

Three concepts that are easy to blur together:

| Term | What it is | Analogy |
|---|---|---|
| **Image** | A read-only *blueprint* that pins the software + versions (e.g. "Alpine Linux + Postgres 16"). | A class, or an npm package tarball. |
| **Container** | A running (or stopped) *instance* of an image — the actual live process. Disposable. | An object created from a class. |
| **Volume** | A *separate, durable storage area* on the host disk, attached to a container. Survives the container. | An external USB drive you plug into a laptop. |
| **Compose project** | A group of containers defined together in one `docker-compose.yml`, started/stopped as a unit. | `package.json` scripts that spin up several services at once. |

**Key rules I learned:**

- **Image vs container:** one image can back many containers (e.g. one `postgres:16-alpine`
  image ran two separate Postgres containers on ports 5432 and 5433).
- **Stop ≠ remove:** stopping a container (Docker Desktop's blue square / `docker stop`)
  keeps everything. Only `docker rm` deletes the container, and even then **named volumes
  survive** — your DB data is safe until you explicitly delete the *volume*
  (`docker compose down -v`).
- **Container vs Virtual Machine:** a VM runs its own full OS *including a kernel* (heavy,
  GBs). A container **shares the host's kernel** and only bundles the userland/software
  (light, MBs). On a Mac, Docker Desktop quietly runs a small Linux VM in the background,
  and containers share *that* VM's kernel (because macOS isn't Linux).
- **Image tags:** an image name is `repository:tag`, e.g. `redis:7.4`. Different tags =
  different images, even for the same software. `redis:7.4` (Debian-based, 136MB) and
  `redis:7-alpine` (Alpine-based, 42MB) coexist independently.
- **`-alpine`** in a tag = built on tiny Alpine Linux → smaller image.

---

## 2. What the Step 0 smoke tests were FOR

**Goal:** before building anything, prove my machine can actually pull and *run* the two
backing-service images FleetPulse depends on — Redis and TimescaleDB — and tear them down
cleanly. Catch environment problems now, not mid-build.

These were **throwaway** containers: started, checked, and deleted immediately. Nothing
permanent was created.

---

## 3. The Redis smoke test — line by line

```bash
docker run --rm -d --name fp-smoke-redis redis:7.4
```
Start a Redis container.
- `docker run` — create + start a container
- `redis:7.4` — from this image
- `--name fp-smoke-redis` — friendly name to refer to it
- `-d` — *detached*: run in the background (don't take over the terminal)
- `--rm` — auto-delete the container when it stops (keeps things tidy)

Output = a long **container ID** = confirmation it launched.

```bash
docker exec fp-smoke-redis redis-cli ping
```
Talk to the running container to prove it works.
- `docker exec fp-smoke-redis` — run a command *inside* the running container
- `redis-cli ping` — Redis's built-in client asks the server "are you alive?"
- **Expected reply: `PONG`** ← the test passing.

```bash
docker rm -f fp-smoke-redis
```
Stop + remove the container (`-f` = force, since it's running). Cleanup.

**Result I got:** `PONG`. ✅

---

## 4. The TimescaleDB smoke test — line by line

```bash
docker run --rm -d --name fp-smoke-ts -e POSTGRES_PASSWORD=smoke timescale/timescaledb:latest-pg16
```
Start a TimescaleDB container.
- Same flags as Redis, **plus** `-e POSTGRES_PASSWORD=smoke`
- `-e` sets an **environment variable** inside the container. This image *requires* a
  password on first boot or it refuses to start, so I gave it a throwaway one (`smoke`).
  (Env vars are how FleetPulse passes real config via its `.env` file — same mechanism.)

```bash
sleep 6
docker exec fp-smoke-ts psql -U postgres -c "SELECT extname, extversion FROM pg_extension WHERE extname='timescaledb';"
```
Wait, then query the database.
- `sleep 6` — a database needs a few seconds to initialize before it can answer; querying
  too soon gives "connection refused" (Redis is instant; a Postgres-based DB is not).
- `psql -U postgres` — Postgres's command-line client, logged in as the default `postgres` user
- `-c "SELECT ..."` — run one SQL query and exit
- The query asks: "is the `timescaledb` extension installed?"
- **Expected: one row** showing `timescaledb` + a version ← proves it's not plain Postgres
  but actually has the time-series extension FleetPulse needs.

```bash
docker rm -f fp-smoke-ts
```
Cleanup.

**Result I got:** one row, `timescaledb | 2.27.2`. ✅
(This also told us the current stable Timescale version: **2.27.2** → Step 1 pins
`timescale/timescaledb:2.27.2-pg16`.)

---

## 5. Why the images stayed after the containers were deleted

After both smoke tests, `docker images` still showed `redis` and `timescale/timescaledb`.
That's the image-vs-container split in action: deleting the **container** (the running
instance) does **not** delete the **image** (the blueprint). The images stay on disk,
ready to be reused — by FleetPulse, or by re-running these tests.

---

## 6. How to re-run the whole thing later

```bash
# Redis
docker run --rm -d --name fp-smoke-redis redis:7.4
docker exec fp-smoke-redis redis-cli ping            # expect: PONG
docker rm -f fp-smoke-redis

# TimescaleDB
docker run --rm -d --name fp-smoke-ts -e POSTGRES_PASSWORD=smoke timescale/timescaledb:latest-pg16
sleep 6
docker exec fp-smoke-ts psql -U postgres -c "SELECT extname FROM pg_extension WHERE extname='timescaledb';"  # expect: 1 row
docker rm -f fp-smoke-ts
```

---

## 7. Handy everyday Docker commands (cheat sheet)

```bash
docker ps                 # running containers
docker ps -a              # ALL containers (running + stopped)
docker images             # downloaded images (blueprints)
docker volume ls          # volumes (persistent data)
docker compose ls         # running Compose projects
docker system df          # disk usage breakdown

docker stop <name>        # stop a container (data kept)
docker start <name>       # start a stopped container back up
docker rm <name>          # remove a stopped container (volumes survive)
docker rm -f <name>       # force-remove a running container
docker rmi <image>        # remove an image

# Compose (run from the folder with docker-compose.yml):
docker compose up -d      # start the whole project in the background
docker compose stop       # stop all its containers (data kept)
docker compose down       # remove its containers (named volumes KEPT)
docker compose down -v    # remove containers AND volumes (DELETES DATA)
```

**The one to be careful with:** `docker compose down -v` — the `-v` deletes volumes,
which is the only routine command that wipes your database data.
