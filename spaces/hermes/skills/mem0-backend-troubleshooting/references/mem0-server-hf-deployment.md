# mem0 Official Docker Server + HF Space Deployment Constraints

Investigation of mem0's official `server/` Docker deployment and whether it can run on HF Space free tier with NIM + 智谱 + Supabase.

## mem0 Official Server (`server/` in GitHub repo)

The `server/` directory exists in `github.com/mem0ai/mem0` (NOT in the pip package). It contains a full FastAPI app: `Dockerfile`, `docker-compose.yaml`, `main.py`, `auth.py`, `routers/`, Alembic migrations, and a Next.js dashboard.

### Routes (matches hermes SelfHostedBackend contract)

```
POST   /memories         — create memories
GET    /memories         — list memories
POST   /search           — search memories
PUT    /memories/{id}    — update
DELETE /memories/{id}    — delete
GET    /memories/{id}    — get one
POST   /configure       — set mem0 config at runtime (admin)
GET    /configure        — get current config
```

These line up with hermes `SelfHostedBackend` (`_backend.py` L83-145): `POST /memories`, `POST /search`, `PUT /memories/{id}`, `DELETE /memories/{id}`.

### Provider validation (BLOCKS non-bundled providers)

`main.py` hardcodes:
```python
BUNDLED_LLM_PROVIDERS = ("openai", "anthropic", "gemini")
BUNDLED_EMBEDDER_PROVIDERS = ("openai", "gemini")
```

`_validate_bundled_providers()` rejects any `/configure` call where `llm.provider` or `embedder.provider` is not in these tuples. Returns 400 with: "To use another provider, install its Python package, rebuild the container, and extend BUNDLED_LLM_PROVIDERS in server/main.py."

**Workaround**: NIM and 智谱 are accessed via OpenAI-compatible endpoints, so set `provider: "openai"` + `openai_base_url` pointing to NIM/z.ai. The validator only checks the provider string, not the URL. This is the same trick used in the OSS `mem0.json` config.

### Auth mismatch (mem0 server vs hermes SelfHostedBackend)

- **mem0 server** `auth.py`: uses `Authorization: Bearer <JWT>` (30-min access token + 30-day refresh) OR `ADMIN_API_KEY` env var. API key management via `routers/api_keys.py` with bcrypt-hashed keys in Postgres.
- **hermes `SelfHostedBackend`** (`_backend.py` L98-99): sends `X-API-Key: <api_key>` header. Comment: "omitted only for AUTH_DISABLED servers".

**Resolution**: Set `AUTH_DISABLED=true` in mem0 server env to make it accept unauthenticated requests (skip the `Authorization` header). hermes's `SelfHostedBackend` omits `X-API-Key` when `api_key` is empty in `mem0.json`. This works for development/internal use — for production, add an `X-API-Key` check in a custom wrapper instead.

### docker-compose runs 3 containers

```yaml
services:
  mem0:          # FastAPI server, port 8888→8000
  postgres:      # pgvector/pgvector:pg17, port 8432→5432
  mem0-dashboard: # Next.js dashboard, port 3000
```

The server expects its own Postgres instance for both memory storage (pgvector) AND app state (users, API keys, settings, request logs — stored in a separate `mem0_app` database via Alembic migrations).

**To use an external Supabase instead of the bundled Postgres**: set `POSTGRES_HOST` to the Supabase pooler URL. But the server also needs the `mem0_app` DB for its own state (users, API keys, request logs) — that's a separate schema that Alembic creates. You'd need to run `alembic upgrade head` against your Supabase to create the app state tables, which pollutes the Supabase project with mem0 server internal tables.

## HF Space Free Tier Constraints (deployment blockers)

### Blocker 1: Single container only

HF Space runs ONE container per Space. `docker-compose` with 3 services (mem0 + postgres + dashboard) **cannot run on HF free tier**. Only the Docker SDK (single `Dockerfile`) is supported.

### Blocker 2: Only port 7860 exposed

HF Space exposes **only port 7860** to the public internet (`*.hf.space`). The mem0 server Dockerfile exposes port 8000 — HF won't forward traffic to it. A bare FastAPI/uvicorn on port 8000 is **unreachable from outside the Space**.

**Workarounds:**
- Run uvicorn on port 7860 (change `--port 8000` to `--port 7860` in the CMD)
- Or use Gradio's `gradio.mount_gradio_app` to mount FastAPI routes under the Gradio app on 7860

### Blocker 3: No persistent disk

`/opt/data` is ephemeral (wiped on Restart). The mem0 server's `history.db` (SQLite for request logs) and any local state would be lost. Memory data itself is in Supabase (external, survives).

## Official Docker with Zero Source Changes (env-var-only adaptation)

Before dismissing the official `server/` Docker, verify whether **environment variables alone** can adapt it — no source file edits means no rebase on upstream updates.

### Key discovery: `db.py` + `alembic/env.py` both use env vars

- `db.py::_build_database_url()` reads `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `APP_DB_NAME` — all env vars
- `alembic/env.py` calls `config.set_main_option("sqlalchemy.url", _build_database_url())` — **overrides** the hardcoded URL in `alembic.ini`
- `alembic.ini`'s `sqlalchemy.url` is never used at runtime — `env.py` replaces it

**→ Can connect to Supabase by setting env vars only, no `db.py` or `alembic.ini` edit needed.**

### Supabase `mem0_app` database → use `postgres` + `APP_DB_NAME=postgres`

Supabase free tier: 1 postgres instance, 1 default database (`postgres`). `CREATE DATABASE` is blocked (Supabase restricts superuser). But mem0 server needs a database for its app state (users, API keys, settings, request logs).

**Solution**: Set `APP_DB_NAME=postgres` → mem0 server connects to the existing `postgres` database. Tables (users, api_keys, request_logs, refresh_token_jtis, settings) go in the `public` schema alongside existing tables.

### Table name conflict check (verified 2026-08-15)

mem0 server creates 5 tables: `users`, `api_keys`, `request_logs`, `refresh_token_jtis`, `settings`.

Supabase existing tables: `agent_states`, `backup_snapshots`, `hermes_mem0`, `long_memory`, `skills_index`, `space_health`, `task_logs`, `task_queue`.

**No name collisions** — the 5 mem0 app-state tables can safely coexist in the same `postgres` database's `public` schema.

### Environment variable summary (zero source changes)

```
# Supabase connection (both memory storage and app-state tables)
POSTGRES_HOST=aws-0-us-west-1.pooler.supabase.com
POSTGRES_PORT=5432
POSTGRES_DB=postgres
POSTGRES_USER=postgres.<project-ref>
POSTGRES_PASSWORD=<supabase-password>
APP_DB_NAME=postgres
POSTGRES_COLLECTION_NAME=hermes_mem0

# Auth — disable JWT/Bearer, use X-API-Key or none
AUTH_DISABLED=true

# Provider workaround (NIM + 智谱 via "openai" provider name)
OPENAI_API_KEY=<nim-key>  # default embedder key
MEM0_DEFAULT_LLM_MODEL=glm-4.7-flash
MEM0_DEFAULT_EMBEDDER_MODEL=nvidia/nemotron-3-embed-1b
# Then POST /configure once to set openai_base_url for LLM and embedder
```

### First-run: Alembic migration + `/configure` API call

After the container starts for the first time:

1. **`alembic upgrade head`** — creates the 5 app-state tables in Supabase `postgres` database. These persist — subsequent restarts skip (alembic checks `alembic_version` table).
2. **`POST /configure`** — sets NIM `openai_base_url` + 智谱 `openai_base_url` at runtime. Config overrides are stored in the `settings` table in Supabase → **persist across restarts**. Only needs to be called once.

### Remaining changes (unavoidable, in YOUR Dockerfile only — not upstream source)

1. **Port**: `EXPOSE 8000` → `7860` + `CMD --port 8000` → `7860` (HF only exposes 7860)
2. **docker-compose**: not used (HF free = single container)

These go in YOUR `Dockerfile` that wraps the official server code — not in the upstream `server/` files. So **zero rebase on upstream mem0 updates**.

## Three-File永续 Architecture (user's preferred pattern)

User's constraint: **minimize git push to HF Space repo** — frequent pushes trigger rebuilds which can get free-tier Spaces banned. The goal is three files that go in once and rarely change:

| File | Content | Changes needed |
|------|---------|-----------------|
| `Dockerfile` | Base image + git clone mem0/server + pip install + EXPOSE 7860 + CMD start.sh | Only when mem0 changes Docker base or port |
| `README.md` | HF Space metadata (title, sdk: docker, app_port: 7860) | Never (after initial creation) |
| `start.sh` | `alembic upgrade head` + `uvicorn main:app --port 7860` | Only if startup sequence changes |

**Persistence mechanism (no HF Bucket needed):**
```
HF Space Restart
  ↓
Three files in Space git repo → survive restart (HF persistent)
  ↓ Docker rebuild (only if files changed, not on every restart)
git clone --depth 1 mem0/server → latest code
  ↓
start.sh:
  1. alembic upgrade head → connects to Supabase (tables exist → skip)
  2. uvicorn → connects to Supabase pgvector (memory survives)
  ↓
/configure overrides loaded from Supabase `settings` table → survive restart
  ↓
Server ready on port 7860
```

All dynamic config (API keys, connection strings, model names) lives in **HF Space Secrets** (persist across restart, not in git repo). Static config (NIM base_url, 智谱 base_url) stored in Supabase `settings` table after first `/configure` call.

## Thin Custom Wrapper Alternative

A **thin custom wrapper** (~80 lines) is an alternative that avoids the official server entirely:

| | Official `server/` on HF | 80-line custom wrapper |
|---|---|---|
| Provider validation | Must set `provider:"openai"` workaround | Direct `Memory.from_config(your_cfg)` ✅ |
| External Supabase | Needs Alembic migrations for `mem0_app` tables | Only connects to your existing `hermes_mem0` table ✅ |
| Auth | Must set `AUTH_DISABLED=true` | Write `X-API-Key` check to match hermes ✅ |
| HF port 7860 | Must change Dockerfile CMD port | Set uvicorn `--port 7860` directly ✅ |
| Containers | Must strip compose to single service | Single `Dockerfile` + `app.py` ✅ |
| Code size | Full server/ directory + 3 containers | ~80 lines + 1 Dockerfile |
| HF Space git persistence | — | **Dockerfile + app.py stored in Space repo = survive Restart** ✅ |

The custom wrapper uses `mem0ai` pip package + `Memory.from_config()` directly — no `server/` code, no Alembic, no bundled Postgres. The Space's own git repo (where `Dockerfile` and `app.py` live) is persistent across Restarts, making the restore chain simpler than hermes's own Bucket+template+envsubst chain.

## Hermes Switch (no source code change)

Once the mem0 server Space is live at `https://your-mem0-space.hf.space`:

```json
// /opt/data/.hermes/mem0.json — change from:
{
  "mode": "oss",
  "oss": { ... NIM, 智谱, Supabase ... }
}
// to:
{
  "mode": "self_hosted",
  "host": "https://your-mem0-space.hf.space",
  "api_key": "",
  "agent_id": "hermes"
}
```

Hermes's `_create_backend` (L281-288) dispatches `mode:"self_hosted"` + `host` set → `SelfHostedBackend(api_key, host)` → HTTP client mode. No hermes source change, no rebase on upgrade.

**Remote agents** (Claude Code, OpenClaw, etc.) call the same Space directly via HTTP `POST /memories` / `POST /search` — no mem0 SDK install, no NIM/智谱/Supabase keys on their side.

## Minimal Implementation Sketch

```python
# app.py — ~80 lines, deploy on HF Space (Docker SDK)
import os
from mem0 import Memory
from fastapi import FastAPI, Header, HTTPException

config = {
    "vector_store": {
        "provider": "pgvector",
        "config": {
            "connection_string": os.environ["SUPABASE_PG_STRING"],
            "collection_name": "hermes_mem0",
            "hnsw": False,
            "embedding_model_dims": 2048
        }
    },
    "llm": {
        "provider": "openai",
        "config": {
            "model": "glm-4.7-flash",
            "openai_base_url": "https://api.z.ai/api/paas/v4",
            "api_key": os.environ["ZHIPU_API_KEY"],
            "temperature": 0.1,
            "max_tokens": 2000
        }
    },
    "embedder": {
        "provider": "openai",
        "config": {
            "model": "nvidia/nemotron-3-embed-1b",
            "openai_base_url": "https://integrate.api.nvidia.com/v1",
            "api_key": os.environ["NIM_API_KEY"]
        }
    }
}

m = Memory.from_config(config)
api_key = os.environ.get("MEM0_API_KEY", "")
app = FastAPI(title="mem0-server")

def _auth(x_api_key: str = Header(None)):
    if api_key and x_api_key != api_key:
        raise HTTPException(401, "Invalid API key")

@app.post("/memories")
def add_memories(messages: list, user_id: str = "default",
                 agent_id: str = "default", _: str = Header(None)):
    _auth(_)
    return m.add(messages, user_id=user_id, agent_id=agent_id)

@app.post("/search")
def search(query: str, user_id: str = "default",
          agent_id: str = "default", _: str = Header(None)):
    _auth(_)
    return m.search(query, user_id=user_id, agent_id=agent_id)
# + PUT/DELETE /memories/{id}
```

## Sleep behavior (HF free tier)

HF free Spaces auto-sleep after ~48h of no requests. The mem0 server Space will sleep when idle. On the next request (from hermes or remote agent), HF auto-wakes the Space (cold start 3-10 seconds). This is acceptable for memory writes/reads (not latency-critical). To keep it always-on, add a lightweight keepalive cron (e.g. curl the `/docs` endpoint every 30 min from another Space or UptimeRobot).
