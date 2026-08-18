# Multi-Agent Access to mem0 (Sharing Patterns)

mem0's backend is a cloud PostgreSQL (Supabase pgvector). Any process that can reach the internet and has the credentials can connect. But "can connect to the database" ≠ "can use the mem0 pipeline" — the embedding + LLM extraction layer requires the `mem0ai` SDK + NIM key + 智谱 key.

## The Three Integration Paths

### Path 1: Independent mem0 SDK per agent (heaviest)

Each remote agent installs `mem0ai` + configures its own `mem0.json` (Supabase connection string + NIM key + 智谱 key). Full pipeline runs locally on each agent.

- **Pros:** Clean isolation. Each agent can use a different embedder/LLM.
- **Cons:** Key duplication, SDK upgrades and config sync must be managed per agent. Embedding dimension mismatch means agents using different embedders CANNOT share the same pgvector table (pgvector rejects mixed-dim vectors in one table).
- **When:** Agents needing different embedding models or full offline autonomy.

### Path 2: mem0 FastAPI server (recommended for sharing)

mem0 ships a self-hosted FastAPI server mode (`server/` directory in mem0 repo). Run one `mem0 server` process — it holds all keys (NIM, 智谱, Supabase) and exposes HTTP routes (`/memories`, `/search`). All agents (hermes + remote) connect as HTTP clients.

**Hermes natively supports this mode** — `plugins/memory/mem0/_backend.py` has `SelfHostedBackend`:

```python
# plugins/memory/mem0/__init__.py L281-288
if self._mode == "oss":
    return OSSBackend(self._config.get("oss", {}))     # current mode
if self._host:                                           # ← self-hosted mode
    from ._backend import SelfHostedBackend
    return SelfHostedBackend(self._api_key, self._host)
```

To switch hermes from OSS to self-hosted, change `mem0.json`:
```json
{
  "mode": "self_hosted",
  "host": "http://your-mem0-server:8080",
  "api_key": "<optional X-API-Key>"
}
```

Remote agents call the server directly via HTTP — no mem0 SDK, no API keys needed on their side (or one shared X-API-Key if auth enabled).

- **Pros:** Keys in one place. All agents equal. No SDK install on remote side. Decouples from hermes source code.
- **Cons:** Need to run a separate server process. `SelfHostedBackend` uses `X-API-Key` auth + `/memories` + `/search` routes (NOT the mem0 cloud API's `Authorization: Bearer` + `/v1/ping` validation — see `_backend.py` L83-145). **mem0 2.0.10 pip package does NOT include the `server/` directory** — it only contains `client/`, `memory/`, `embeddings/`, `llms/`, `vector_stores/`, `configs/`, `proxy/`, `reranker/`, `utils/`. No `server/` module, no FastAPI app, no CLI entry point for serving.
- **When:** Multiple agents (hermes + remote) sharing the same memory pool.

### Path 2 implementation: pip package has no server

Since `pip install mem0ai` does NOT include the FastAPI server (only the SDK library), there are two sub-paths:

**2a: Clone mem0 GitHub repo and use its `server/` directory.** The official `mem0/` GitHub repo has a `server/` FastAPI app not shipped in the pip wheel. Clone the repo, install extra deps (fastapi, uvicorn), run `server/`.

**2b (recommended): Write a thin custom FastAPI wrapper (~80 lines).** Load the same `mem0.json` → `Memory.from_config(config)`, expose 4 HTTP routes matching the `SelfHostedBackend` contract, add `X-API-Key` auth. This avoids depending on mem0's repo structure (which may change) and is less code than wiring up the full upstream server.

**SelfHostedBackend API contract** (from `_backend.py` L83-145 — hermes calls these routes):
```
POST   /search          → body: {"query": "...", "filters": {...}, "top_k": N}
POST   /memories        → body: {"text": "...", "user_id": "...", ...}
PUT    /memories/{id}   → body: {"text": "..."}
DELETE /memories/{id}
```
Auth: `X-API-Key` header (omitted when server runs with AUTH_DISABLED). No `Authorization: Bearer`, no `/v1/ping` validation — those are the mem0 *cloud* client contract, not the self-hosted one.

### Path 3: Add `/memory/*` routes to hermes api_server (most coupled)

hermes `api_server` exposes `/v1/chat/completions`, `/v1/models`, `/v1/runs` but has **NO** `/memory/*` endpoints. `mem0_add`/`mem0_search` are agent-loop tools, not HTTP routes. Would require adding custom FastAPI routes to `hermes_cli/web_server.py` that internally call `MemoryManager`.

- **Pros:** Single entry point. Same `API_SERVER_KEY` auth as chat.
- **Cons:** Modifies hermes source — every upstream upgrade needs rebase. Not recommended unless you can't run a separate mem0 server.

## agent_id Isolation (same-table multi-agent)

mem0 stores `agent_id` in each row's `payload` JSONB field. Writes auto-tag with the configured agent_id; searches filter by agent_id. So multiple agents CAN share the same `hermes_mem0` table without cross-contamination — as long as they use different `agent_id` values.

To share memories across agents: search without the agent_id filter, or explicitly search `agent_id="hermes"` from a remote agent.

## Verification Pitfall (user-corrected)

**Raw SQL INSERT into hermes_mem0 does NOT validate the mem0 pipeline.** It bypasses embedding (NIM) + fact extraction (智谱 LLM) entirely — it just stuffs a row into pgvector. A successful raw INSERT only proves the Supabase connection works, not that mem0 can add/search memories.

To truly verify mem0 end-to-end:
1. Construct `OSSBackend(cfg["oss"])` (see SKILL.md Step 2)
2. Call `backend._memory.add("test text", user_id="...")` — this exercises NIM embed + 智谱 extract + pgvector INSERT
3. Call `backend._memory.search("query", filters={"user_id": "..."})` — this exercises NIM embed + pgvector similarity search

Only if both succeed is the pipeline confirmed working.

## Daemon Restart Requirement

After fixing `mem0.json`, the hermes daemon's in-memory `mem0_search`/`mem0_add` tools **still return 500** because the daemon holds a Backend instance built with the OLD config. The daemon must be restarted (or the HF Space restarted) for the new `mem0.json` to take effect at the tool-call level.

**Don't be fooled** by debugging scripts that show the fix working via `OSSBackend(cfg["oss"])` — those construct a fresh Backend from the fixed config, but the daemon is still using its stale one.
