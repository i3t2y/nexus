

## Final Verification (2026-08-16)

All fixes applied. End-to-end test results:

```
POST /memories  → {"results":[{"id":"41b5821f-...","memory":"User likes pizza","event":"ADD"}]} ✅
POST /search    → {"results":[{"memory":"User likes pizza","score":0.7634}]} ✅
GET /memories   → {"results":[{"memory":"User likes pizza","user_id":"test-001"}]} ✅
```

**Complete fix chain (in order):**
1. `POSTGRES_DB=neondb` HF Secret — pgvector reads `POSTGRES_DB` (not `APP_DB_NAME`), default `postgres` connects to wrong database → `InsufficientPrivilege` on CREATE TABLE
2. patch 10: `embedding_model_dims: 2048` in vector_store.config — NIM nemotron-3-embed-1b outputs 2048 dims, pgvector defaults to 1536
3. patch 10: `hnsw: False` — pgvector HNSW index hard limit 2000 dims, 2048 exceeds → `ProgramLimitExceeded`
4. patch 20: `check=ConnectionPool.check_connection` — psycopg3 pool serves stale connections after Neon scale-to-zero → `OperationalError: connection is lost`
5. patch 30: `DROP TABLE memories CASCADE` — old table created with 1536 dims, `CREATE TABLE IF NOT EXISTS` won't rebuild it
6. patch 30: `DELETE FROM settings WHERE key='config_overrides'` — clear stale DB overrides that strip `openai_base_url` (Issue #4910)

**Keepalive**: cron-job.org → GET `https://nmem-memgraph.hf.space/health` every 4 min → mem0 server queries Neon → all three services stay alive.

**Auth**: Admin API key (`ADMIN_API_KEY` HF Secret) + `AUTH_DISABLED` removed entirely. hermes `SelfHostedBackend` sends `X-API-Key` header → `verify_auth` branch 2 → admin. `/health` bypasses auth (cron unaffected). Anonymous → 401. See `references/mem0-server-auth.md` § Verification Results for the full test matrix.

## hermes SelfHostedBackend Config (mem0.json + .env)

hermes PR #60494 (merged 2026-07-07) added self-hosted mode. The setup wizard writes:
- `mem0.json`: `{"mode": "self_hosted", "self_hosted": {"host": "https://<space>.hf.space"}}`
- `.env`: `MEM0_API_KEY=<ADMIN_API_KEY value>`

**Config loading** (`__init__.py` L87-100): hermes reads `MEM0_API_KEY` from `.env` via `get_secret()` and `MEM0_HOST` from env, then `mem0.json` file overrides. Router priority: `oss > host > platform`. `SelfHostedBackend(api_key, host)` is constructed when `host` is set and mode != `oss`.

**hermes search filters**: `_read_filters()` returns `{"user_id": self._user_id}` — **no agent_id in filters** (by design, so recall surfaces memories across all agents). `_DEFAULT_USER_ID = "hermes-user"`. hermes sends `filters` dict (not top-level `user_id`) to `/search` — top-level `user_id` is deprecated in the mem0 API.

**Finding MEM0_API_KEY**: The value the user enters in hermes backend `→ .env` as `MEM0_API_KEY`. This must match the `ADMIN_API_KEY` HF Space Secret. To find it: `grep '^MEM0_API_KEY=' /opt/data/.hermes/.env`. Do NOT confuse with `HF_TOKEN` (separate secret).

**hermes restart required**: After changing `mem0.json` or adding `MEM0_API_KEY` to `.env`, the hermes process must restart for the new config to take effect. Until restart, `mem0_search`/`mem0_add` may use the old backend (or stale cache) and return data that doesn't exist on the new self_hosted server.
