---
name: mem0-backend-troubleshooting
description: "Fix mem0 OSS backend (NIM+pgvector+Supabase) on Hermes."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [mem0, memory, pgvector, supabase, nim, embedding, debugging, hermes]
---

# mem0 OSS Backend Troubleshooting (Hermes Agent)

Debug and fix the mem0 OSS self-hosted memory backend on Hermes Agent. The stack: **NIM (NVIDIA) embedding API** → **智谱/ZAI LLM** (fact extraction) → **pgvector on Supabase** (vector store).

**Templates:**
- `templates/mem0-server.Dockerfile` — ready-to-push HF Space Dockerfile (git sparse-checkout mem0 server/, port 7860)
- `templates/mem0-server.README.md` — HF Space README with metadata + env var reference
- `templates/mem0-server.start.sh` — startup script (alembic + /health injection + uvicorn 7860)

**Reference files:**
- `references/known-good-mem0-config.md` — verified working mem0.json field-level config + critical do-nots
- `references/nim-embedding-models.md` — full NIM model probe results (which work, which 500/400, dims)
- `references/spacy-docker-baking.md` — how to bake spaCy into the Docker image (optional)
- `references/multi-agent-access.md` — sharing mem0 across remote agents (3 integration paths, agent_id isolation, verification pitfall)
- `references/hf-free-tier-capabilities.md` — HF free Space hardware limits, feature availability matrix, deployment decision framework
- `references/persistence-architecture.md` — four-layer persistence model (credentials / agent memory / program data / knowledge base), tool comparison mem0 vs TencentDB-Agent-Memory, decision framework for "scattered stuff" problem
- `references/mem0-server-hf-deployment.md` — mem0 official Docker server investigation: provider validation limits, auth mismatch with hermes, HF Space single-container/port-7860 constraints, why thin custom wrapper is simpler on HF free tier, minimal implementation sketch
- `references/free-tier-vector-db-alternatives.md` — Supabase free tier quota analysis, 8 alternative free-tier vector DBs compared (Neon/Qdrant/Pinecone/Zilliz/Turso/ElephantSQL/Railway/CockroachDB), mem0's 26 supported vector store providers, decision framework
- `references/nexus-architecture.md` — Nexus architecture: Hermes + LangGraph ⊕ Mem0 Worker Space, component roles, LangGraph-on-free-HF capability matrix, persistence/logging on ephemeral disk, Neon/R2/Upstash shared infra
- `references/github-actions-hf-deploy.md` — GitHub private repo + Actions → HF Space deploy pattern: why GitHub over direct HF git, Actions workflow (manual/tag trigger only), agent maintenance via git+PAT, gh CLI install without sudo on HF Space, three-file repo layout + DEPLOY.md summary
- `references/hermes-gateway-log-analysis.md` — hermes gateway log analysis methodology: log file locations, failure pattern classification (429/stream-stale/relay-bug/compression-cascade/auxiliary-down), root-cause vicious cycle, known self-healing warnings vs real errors
- `references/hermes-boot-persistence.md` — real-start.sh boot sequence, "缺才生成" mem0.json template mechanism, why self_hosted mem0 config gets overwritten on restart (not in Bucket restore/upload lists), fix options + Env-Var-Only Fix (zero script changes)
- `references/hf-bucket-vs-dataset.md` — HF Storage Bucket vs Dataset 全维度对比 (2026-08-17官方文档查证): rw挂载/增量同步/git膨胀/配额/nexus 7维查证/state.db malformed根因/三件套现状对比

## When to Load

- `mem0_add` / `mem0_search` tools return 500 "Something went wrong"
- mem0 sync errors in `errors.log` (400 `dimensions extra_forbidden`, 500, 404)
- "Network is unreachable" in gateway/agent logs (IPv6 stack issue)
- "Memory provider 'mem0' activated" in log but calls fail
- After changing embedding model, LLM provider, or Supabase connection string
- After Space Restart (ephemeral disk wipes `/opt/data/.hermes/mem0.json`)
- After Space Restart and mem0 config reverts to `oss` mode (self_hosted → HF Space config lost — see `references/hermes-boot-persistence.md` § "Env-Var-Only Fix" for the zero-script-change solution: just set `MEM0_HOST` + `MEM0_API_KEY` as HF Secrets)

## Architecture Quick Map

```
mem0.json ($HERMES_HOME/mem0.json)
├── oss.embedder  → NIM integrate.api.nvidia.com/v1 (NVIDIA API key, direct — NOT via omniroute)
├── oss.llm       → 智谱 api.z.ai/api/paas/v4 (glm-4.7-flash, ZAI API key)
└── oss.vector_store → pgvector on Supabase (session pooler IPv4)
```

**Hermes plugin three-mode dispatch** (`plugins/memory/mem0/__init__.py` L267-288 `_create_backend`):

```python
if self._mode == "oss":                                # mode:"oss" in mem0.json
    return OSSBackend(self._config.get("oss", {}))    # → reads oss block, NIM+智谱+pgvector
if self._host:                                         # mode:"self_hosted", host set
    return SelfHostedBackend(self._api_key, self._host) # → HTTP client to mem0 server
return PlatformBackend(self._api_key)                  # mode:"platform" (default), api_key set
                                                       # → mem0 SaaS cloud client
```

Config loading (`_load_config` L78-110): defaults from env vars (`MEM0_MODE`, `MEM0_API_KEY`, `MEM0_HOST`, `MEM0_AGENT_ID`), then `mem0.json` overrides via `config.update({k: v for k, v in file_cfg.items() if v is not None and v != ""})`. **Dict/list values are preserved** (only None/"" filtered) — so the `oss: {...}` block survives the merge and reaches `OSSBackend`.

**OSSBackend indirection:** `OSSBackend.__init__` takes `cfg["oss"]` (NOT the top-level `cfg`), restructures via `_provider_block()`, then calls `Memory.from_config(config)`. Testing via `Memory.from_config(cfg)` directly gives WRONG results — model falls back to `text-embedding-3-small` because top-level keys (`mode`, `agent_id`, `oss`) don't match `MemoryConfig` fields.

**Always test via `OSSBackend(cfg["oss"])` to reproduce the real loading path.**

## Verifying External Agent Reports (methodology)

When a subagent (delegate_task / Claude Code / Codex / cg52) reports a structural claim about hermes internals (e.g. "hermes plugin is Platform SaaS client, doesn't read oss block"), **verify against the actual source on THIS machine before acting on it**:

1. The subagent may have read a different codebase (e.g. `/home/user/.hermes/hermes-agent/` on their machine vs `/opt/hermes-agent/` on HF Space — different versions, different patches).
2. Read the specific line numbers they cite and check if the code matches. Claims of `self._client = MemoryClient(api_key)` that don't exist in the actual source = wrong codebase.
3. A subagent spending 30+ minutes debugging with a BLOCKED classifier (can't run Bash/grep) is a red flag — its conclusions are based on partial reads, not exhaustive search. Treat as a hypothesis to verify, not a fact.

**Concrete case:** cg52 reported "hermes plugin = Mem0 Platform SaaS client, `MemoryClient(api_key)`, doesn't read oss block" — verifiably wrong on this machine. The actual `_create_backend` (L281-288) has three-mode dispatch; `mode:"oss"` → `OSSBackend` reads the oss block. `MemoryClient` appears nowhere in `__init__.py`. cg52 was reading a different install path.

## Transient vs Config Errors (429 diagnosis)

Not all mem0 failures are config errors. The `LLM extraction failed: 429 - code:1305` error from 智谱 (`glm-4.7-flash`) is a **transient overload** on 智谱's free tier (RPM/RPS limit), NOT a configuration bug. Distinguish:

| Signal | Config error | Transient overload |
|--------|-------------|-------------------|
| Error code | 400/404/500 with specific field name | 429 / 1305 / "service may be temporarily overloaded" |
| Consistency | Same error every retry | Succeeds on retry after cooldown |
| Scope | Only one model/provider affected | Model works fine minutes later |
| Action | Fix mem0.json field | Wait + retry; or set `infer=False` to skip LLM extraction |

**`infer=False` workaround:** If 智谱 429s are frequent, `mem0.Memory.add(..., infer=False)` skips the entire LLM extraction phase (no fact提炼, no dedup, no entity extraction — raw text goes straight to embedding + pgvector). Cost: storage bloat (full messages instead of extracted facts), no dedup. Benefit: zero LLM dependency, embedding-only path via NIM (no 智谱 call). Note: hermes's own `mem0_conclude` tool already hard-codes `infer=False` at plugin L353; the `infer=True` path is only in `sync_turn` (auto-background write).

## Key Field Distinctions (critical — getting these wrong = silent failures)

| Field | Where in mem0.json | What it controls | Wrong value → |
|-------|--------------------|--------------------|--------------|
| `embedding_dims` | `oss.embedder.config` | Whether mem0 passes `dimensions` param to embedding API | Non-matryoshka models (NIM) reject the param → 400/404/500 |
| `embedding_model_dims` | `oss.vector_store.config` | pgvector column width (vector dim) | Mismatch with actual embedding output → INSERT fails |
| `hnsw` | `oss.vector_store.config` | HNSW index on/off | >2000 dims + HNSW on = `ProgramLimitExceeded` |
| `collection_name` | `oss.vector_store.config` | pgvector table name | Stale table from old model/dims → schema mismatch |

## Debug Protocol

### Step 1: Classify the error

| Error | Root cause class | Jump to |
|-------|------------------|---------|
| `400 dimensions extra_forbidden` | `embedding_dims` set on embedder, NIM rejects it | [Fix A](#fix-a-remove-embedding_dims-from-embedder) |
| `500 Something went wrong` | NIM model unavailable for this account/key | [Fix B](#fix-b-find-a-working-nim-embedding-model) |
| `404 page not found` | Model fallback to `text-embedding-3-small` (test path wrong) OR NIM model not found | [Fix C](#fix-c-verify-loading-path--model-name) |
| `ProgramLimitExceeded ... >2000 dimensions` | HNSW index limit exceeded | [Fix D](#fix-d-disable-hnsw-for-2048-dim-models) |
| `Network is unreachable` | Supabase direct PG DNS gives IPv6, HF has no v6 egress | [Fix E](#fix-e-use-ipv4-session-pooler) |
| `Could not find the table` (PGRST205) | Supabase tables not created (empty project) | [Fix F](#fix-f-create-supabase-tables) |
| `429 code:1305 service may be temporarily overloaded` | 智谱 free-tier RPM limit (transient, NOT config) | [Fix G](#fix-g-429-transient-overload-workaround) |

### Step 2: Test via the real loading path

```python
import os, json, sys
sys.path.insert(0, '/opt/hermes-agent')
from plugins.memory.mem0._backend import OSSBackend

with open('/opt/data/.hermes/mem0.json') as f:
    cfg = json.load(f)

# Set env vars that mem0's internal OpenAI clients read
os.environ["OPENAI_API_KEY"] = cfg["oss"]["embedder"]["config"]["api_key"]
os.environ["OPENAI_BASE_URL"] = cfg["oss"]["embedder"]["config"]["openai_base_url"]

backend = OSSBackend(cfg["oss"])  # NOT Memory.from_config(cfg)!

emb = backend._memory.embedding_model
print(f"model: {emb.config.model}")           # should be your NIM model
print(f"pass_dimensions: {emb._pass_dimensions_to_api}")  # should be False for NIM
print(f"dims: {emb.config.embedding_dims}")
print(f"vs_dims: {backend._memory.config.vector_store.config.embedding_model_dims}")

# Test end-to-end
r = backend._memory.add("test memory", user_id="hermes")
print(r)
```

### Step 3: Probe NIM embedding models directly

```python
import httpx, json
with open('/opt/data/.hermes/mem0.json') as f:
    nv = json.load(f)["oss"]["embedder"]["config"]["api_key"]

# List all available models
r = httpx.get("https://integrate.api.nvidia.com/v1/models",
              headers={"Authorization": f"Bearer {nv}"}, timeout=15)
models = [m["id"] for m in r.json()["data"] if "embed" in m["id"].lower()]

# Test each: no input_type (symmetric), with input_type (asymmetric)
for model in models:
    body = {"input": ["test"], "model": model, "encoding_format": "float"}
    r = httpx.post("https://integrate.api.nvidia.com/v1/embeddings",
                   headers={"Authorization": f"Bearer {nv}"}, json=body, timeout=30)
    status = r.status_code
    dim = len(r.json()["data"][0]["embedding"]) if status == 200 else "?"
    print(f"{model}: {status} dims={dim}")
```

## Fixes

### Fix A: Remove `embedding_dims` from embedder
Do NOT set `oss.embedder.config.embedding_dims`. mem0's `OpenAIEmbedder.__init__` does:
```python
self._pass_dimensions_to_api = self.config.embedding_dims is not None
```
If `embedding_dims` is set, mem0 passes `dimensions=NNN` to the API. NIM models are **not** matryoshka — they reject this param. Leave it unset (None) so `_pass_dimensions_to_api=False`.

Set `embedding_model_dims` (vector store, NOT embedder) to match the model's native output dim.

### Fix B: Find a working NIM embedding model
NIM models return **500 "Something went wrong"** when the model is unavailable for your account (not a clean 404). Probe all embedding models (see Step 3). Known working symmetric model: `nvidia/nemotron-3-embed-1b` (2048 dims, no `input_type` required).

Asymmetric models (e.g. `nvidia/nv-embedqa-e5-v5`) require `input_type: "query"|"passage"` — mem0 doesn't send this param, so they return 400.

### Fix C: Verify loading path & model name
If `emb.config.model` is `text-embedding-3-small` (OpenAI default), you tested via the wrong path. Use `OSSBackend(cfg["oss"])`, not `Memory.from_config(cfg)`. See Step 2.

### Fix D: Disable HNSW for >2000-dim models
pgvector HNSW index has a **2000-dimension hard limit**. For `nvidia/nemotron-3-embed-1b` (2048 dims), set `oss.vector_store.config.hnsw: false`. Without HNSW, pgvector uses IVF flat index (no dim limit).

### Fix E: Use IPv4 session pooler
Supabase direct PG (`db.<project>.supabase.co:6543`) resolves to IPv6. HF containers have no IPv6 egress → "Network is unreachable". Use the session pooler:
```
aws-0-us-west-1.pooler.supabase.com:5432
```
Username format: `postgres.<project-ref>` (not just `postgres`).

### Fix F: Create Supabase tables
Supabase projects start empty. pgvector tables (`hermes_mem0`) are auto-created by mem0 on first successful `add()`. The `persist_to_r2.py` tables (`agent_states`, `task_logs`, `long_memory`, `skills_index`, `backup_snapshots`, `space_health`, `task_queue`) require a SQL migration.

### Fix G: 429 transient overload workaround
`429 - code:1305 - service may be temporarily overloaded` is 智谱 free-tier RPM/RPS throttling. NOT a config error — retry after cooldown usually succeeds. If frequent:
1. **Option A: Retry with backoff.** 智谱 free tier recovers in seconds-to-minutes. mem0 has no built-in retry for LLM calls — wrap `add()` in a retry loop.
2. **Option B: `infer=False`.** Skip LLM extraction entirely — raw text goes straight to NIM embedding → pgvector. No fact提炼, no dedup, no entity extraction, but zero 智谱 dependency. See "Transient vs Config Errors" above for tradeoffs.
3. **Option C: Upgrade 智谱 tier.** Paid 智谱 API has higher RPM limits.

## Multi-Agent Shared Memory (Deploying a mem0 Server)

When the user wants "any agent to use one unified cloud memory" — hermes's internal mem0 plugin is hermes-only (tool calls, not HTTP). To share memory across Claude Code, OpenClaw, Codex, etc., deploy a separate mem0 HTTP server. See `references/multi-agent-access.md` for the 3 paths + `references/mem0-server-hf-deployment.md` for official Docker investigation.

**Decision matrix (no VPS available):**

| Approach | hermes source change | HF compatibility | Effort |
|----------|---------------------|------------------|--------|
| Official `server/` docker-compose | None | ❌ 3 containers, HF free = single container only | High (strip compose + port 7860 + auth + provider workaround) |
| Thin custom wrapper on new HF Space | None | ✅ Single container on port 7860 | Low (~80 lines + Dockerfile) |
| Routes in hermes `web_server.py` | Yes (rebase on every upgrade) | ✅ (uses existing 7860) | Medium but maintenance tax |

**Recommended**: Thin custom wrapper on a new HF Space. Hermes switches via `mem0.json` `mode:"self_hosted"` + `host:"https://space.hf.space"` — no source change. Remote agents call the same Space via HTTP. Space's git repo persists Dockerfile+app.py across Restarts (simpler than hermes's own Bucket restore chain). See `references/mem0-server-hf-deployment.md` for implementation sketch + official Docker env-var-only adaptation.

**Source of truth (decided 2026-08-15)**: Maintain deployment files in a **GitHub private repo**, not directly in HF Space git. GitHub Actions deploys to HF Space only on manual trigger or version tag — normal code pushes to GitHub do NOT trigger HF rebuilds. This solves the "frequent push = rebuild = ban" risk while preserving full version history and enabling agent maintenance via `git` + PAT. See `references/github-actions-hf-deploy.md` for the Actions workflow, repo layout, and `gh CLI` install-without-sudo technique.

**Provider key isolation**: mem0 server's `DEFAULT_CONFIG` reads a single `OPENAI_API_KEY` env var for both LLM and embedder. To use different providers (NIM for embedder, 智谱 for LLM) with separate keys, there are two approaches:

**(A) /configure endpoint**: Set `JWT_SECRET` + `ADMIN_API_KEY` in HF Space Secrets, then `POST /configure` with per-provider `api_key` fields. Config stored in Neon `settings` table, survives restarts. **Problem**: Setting `JWT_SECRET` enables JWT auth on ALL endpoints (not just `/configure`), blocking unauthenticated `/search` and `/memories` calls from hermes `SelfHostedBackend`.

**(B) DEFAULT_CONFIG patch (recommended)**: Patch `main.py`'s `DEFAULT_CONFIG` at startup in `start.sh` — insert separate `NIM_API_KEY`/`ZAI_API_KEY` env var reads and replace the `llm.config` and `embedder.config` lines. No JWT needed, no `/configure` call, `AUTH_DISABLED=true` stays as the only auth setting. HF Space Secrets: `NIM_API_KEY`, `ZAI_API_KEY` (plus optional `NIM_BASE_URL`, `ZAI_BASE_URL`). Do NOT set `JWT_SECRET` or `ADMIN_API_KEY`. See `hf-space-deploy-via-github` skill § "Patching DEFAULT_CONFIG" for the full patch code.

**Agent context continuity via docs/**: Put `docs/STATUS.md` (deployment checklist with ✅/❌ per step) and `docs/SECRETS.md` (key names + descriptions, no values) in the GitHub repo. Any agent in any session can `git clone` and read these to resume deployment without relying on mem0 (which compresses/loses detail) or ephemeral files (which wipe on Restart). Update STATUS.md on every deployment state change. This is more reliable than mem0 for multi-session project continuity.

**Key gotcha**: mem0 official `server/` uses `Authorization: Bearer` (JWT), but hermes `SelfHostedBackend` sends `X-API-Key` → set `AUTH_DISABLED=true` on official server, or write a custom `X-API-Key` check in the thin wrapper to match hermes's contract exactly.

## Restart Persistence (Hermes HF Space)

`/opt/data` is ephemeral — `mem0.json` is wiped on Space Restart. The template at `/data/scripts/mem0.json.template` is the source of truth (envsubst generates `mem0.json` on boot if absent). **Any fix to `mem0.json` must also be applied to the template**, or the fix is lost on restart.

**Critical gap**: The template is hardcoded to `mode: oss` + Supabase. If you switch mem0 to `self_hosted` (HF Space HTTP API), the config is lost on every restart because `mem0.json` is NOT in the Bucket home-backups restore list or the home-files daemon upload list (unlike `.env`, `config.yaml`, and `MEMORY.md`). Both `restore_home_files.py` and `home_files_uploader.py` have confirmed `_FILES` lists that exclude `mem0.json` — see `references/hermes-boot-persistence.md` for the exact lists, community approaches (HermesFace/HuggingMes whole-directory sync vs nexus per-file list), and four fix options with trade-off analysis.

**✅ Recommended fix — env-var only (zero script/template changes)**: Set `MEM0_HOST=https://nmem-memlg.hf.space` and `MEM0_API_KEY=<key>` as HF Secrets. Do NOT set `MEM0_MODE` (leave it unset → default "platform"). hermes `_load_config()` reads env vars as base layer; without `MEM0_MODE=oss`, `real-start.sh` skips template generation entirely → no `mem0.json` → hermes falls back to env vars → `SelfHostedBackend(api_key, host)`. This survives upgrades because it uses hermes's own env-var fallback design, not modified scripts. See `references/hermes-boot-persistence.md` § "Env-Var-Only Fix" for full source evidence and verification steps.

**Daemon restart pitfall:** After fixing `mem0.json`, debugging scripts that construct `OSSBackend(cfg["oss"])` will show the fix working — but the hermes daemon's `mem0_add`/`mem0_search` tools will **still return 500** because the daemon holds a stale Backend built from the OLD config. The daemon (or HF Space) must be restarted for the new config to take effect at the tool-call level. See `references/multi-agent-access.md` § "Daemon Restart Requirement".

## NIM vs omniroute (user-corrected fact)

**NIM (NVIDIA embedder) is called directly with the NVIDIA API key via `integrate.api.nvidia.com`. It has NOTHING to do with omniroute.** omniroute (`nonoke-omn.hf.space`) is a separate OpenAI-compatible chat completions proxy for the main hermes LLM inference path — it does NOT touch embeddings. Never conflate the two when debugging the embedding layer.

## spaCy (optional NLP lemma)

mem0 logs `WARNING: Failed to load spaCy lemma model: spaCy is not installed`. This is **optional** — mem0 falls back to `to_tsvector('simple', ...)` (no lemmatization). Vector semantic search (the primary recall path) is unaffected. spaCy only improves full-text GIN index recall for same-stem-different-word queries.

To bake spaCy into the Docker image (persists across restarts): add a `spacy-lemma` extra to `pyproject.toml` + install `en_core_web_sm` model + add `--extra spacy-lemma` to Dockerfile's `uv sync` command. See `references/spacy-docker-baking.md` for details.

## HF Free Tier vs Local Deployment (capability assessment)

When the user asks whether deploying Hermes on HF free Space is "worth it" vs local, use this quantified breakdown:

**HF free Space hard limits:** 2 vCPU, 16 GB RAM, no GPU, ephemeral disk (Restart wipes `/opt/data`), no IPv6 egress.

**100% functional (core):** terminal, file ops, code execution, skills (via Bucket restore), mem0 (via external Supabase), delegate (subagents), cron, session_search, web_search — the reasoning/operational core is fully intact.

**0% functional (perception/generation):** browser/CDP (no Chrome), image_gen/BFL (no GPU/API key), TTS (no audio deps), computer_use (no desktop), video_gen (no GPU), native vision (no GPU). These are check_fn-gated and silently disabled.

**~80% persistent:** ephemeral disk requires HF Bucket + R2 + Supabase triple-backup. After Restart, skills/config/.env restore from Bucket, mem0 SDK re-lazy-installs, mem0.json regenerates from template. Each restore has failure points — the restart path is never zero-risk.

**Recommended architecture:** local machine as primary deployment (persistent, full feature set, no restart roulette); HF free Space as lightweight remote entry point for Telegram/chat + memory + orchestration only. Don't stack production-grade features on HF free tier — the ephemeral disk + restart fragility + 2 vCPU ceiling makes heavy stacks a maintenance tax.

## User Deployment Constraints (preferences)

**No VPS**: User does not currently have (or want to purchase) a VPS for hosting. All cloud services must run on free tiers (HF Space, Supabase free, Cloudflare Workers). When recommending deployment targets, only consider: HF free Space + Supabase + CF Workers.

**Switched to Neon (decided 2026-08-15)**: User chose to replace Supabase with Neon as mem0's pgvector backend, primarily to eliminate the 7-day inactivity pause risk. Neon's scale-to-zero auto-wakes in ~500ms (no manual Restore needed vs Supabase's manual restore). Multi-project strategy (100 projects × 0.5GB = 50GB max,额度叠加). Keepalive via dedicated cron-job.org account (separate from other uses) pinging /health every 4 min. Neon region: AWS us-east-1 (same as HF Space, <1ms latency). No Neon Auth or Backend Services add-ons — mem0 server has its own auth. See `references/neon-keepalive-architecture.md` for the full architecture and `references/free-tier-vector-db-alternatives.md` for the comparison analysis. Supabase data (11 MB, 54 rows) can stay for non-mem0 tables or migrate to a second Neon project.

**Neon setup don'ts**: When creating a Neon project, do NOT select "Backend Services" / "Neon Auth" — mem0 server has its own auth (`AUTH_DISABLED=true` + `X-API-Key`). Neon Auth adds an unnecessary OAuth/magic-link user system (`neon_auth` table). Only run `CREATE EXTENSION IF NOT EXISTS vector;` after project creation. Select AWS us-east-1 (matches HF Space region).

**Neon free tier setup do's**:
- `CREATE EXTENSION IF NOT EXISTS vector;` in SQL Editor
- Region: AWS us-east-1 (same as HF Space)
- No Backend Services / Neon Auth add-on
- Multi-project strategy: 100 projects × 0.5GB each (quotas stack)

**HF Space anti-risk-control (decided 2026-08-15)**: User uses a separate HF account (`nmem`) for the mem0 server Space, not the main account. Space named `0` (single character, gives URL `https://nmem-0.hf.space`). README.md is minimal — YAML frontmatter only, no descriptive text (moved to `docs/README_original.md` in GitHub repo). This avoids HF content scanning on the deployment Space. The HF token for Actions deploy must be generated from the **nmem account** (the Space owner), not from `sonoke` (the hermes account) — a token from account A cannot push to account B's Space.

**HF Space Secrets for mem0 server (13 keys)**: `POSTGRES_HOST`, `POSTGRES_PORT`, `POSTGRES_USER`, `POSTGRES_PASSWORD`, `APP_DB_NAME`, `POSTGRES_COLLECTION_NAME`, ~~`AUTH_DISABLED`~~ (cannot be HF Variable — inject via `exec env AUTH_DISABLED=true uvicorn ...` in start.sh; see `hf-space-deploy-via-github` pitfalls), `MEM0_DEFAULT_LLM_MODEL` (=glm-4.7-flash), `MEM0_DEFAULT_EMBEDDER_MODEL` (=nvidia/nemotron-3-embed-1b), `OPENAI_API_KEY` (placeholder for DEFAULT_CONFIG startup — can be the NIM key, unused after /configure or DEFAULT_CONFIG patch), `NIM_API_KEY` (for DEFAULT_CONFIG patch embedder), `ZAI_API_KEY` (for DEFAULT_CONFIG patch LLM), `MEM0_TELEMETRY` (=false), `HF_TOKEN` (for `snapshot_download` of private Dataset worker code — can be added via `huggingface_hub` API but do NOT set as a Secret if it collides; use `WORKER_TOKEN` alias if needed). Do NOT set `JWT_SECRET` or `ADMIN_API_KEY` unless using the `/configure` approach — they enable JWT auth on ALL endpoints, breaking unauthenticated `/search` and `/memories`.

**Three-file永续 pattern**: When deploying anything on HF Space, the user wants exactly three files in the Space git repo (Dockerfile, README.md, start.sh). All dynamic config goes in HF Secrets (not in git). The goal is to push to the Space repo as rarely as possible — frequent pushes trigger Docker rebuilds which the user fears may get the free-tier Space banned. See `references/mem0-server-hf-deployment.md` § "Three-File永续 Architecture" for the pattern applied to mem0 server.

**No source code patching**: User strongly rejects approaches that require patching upstream source and rebasing on every upgrade (e.g. "modify hermes web_server.py" = rejected for this reason). Prefer env-var-only adaptation or thin wrapper approaches that don't touch upstream files.

**Anti-patchwork stance**: User dislikes "缝缝补补" (patch-on-patch) persistence hacks. When the hermes persistence chain has gaps (e.g. mem0.json not in Bucket restore list, template hardcoded to oss), the user prefers understanding the full boot mechanism first ("先分析清楚再说") and researching how other projects solve the same problem before applying fixes. Present the complete persistence flow and the root gap, not just a quick patch. When researching, **search for open-source projects that deploy hermes on HF** (HermesFace, HuggingMes, etc.) to compare approaches — the user asked "搜下github上hf部署hermes的项目" to see community solutions. Offer fix options that work WITH the existing mechanism (e.g. update the template, add to the daemon's file list) rather than adding yet another ad-hoc workaround layer. Prefer the community-recommended pattern (configs go in Secrets + template generation) over modifying hermes infrastructure scripts.

**Architecture accuracy**: User corrected me twice in one session when I mischaracterized the deployment topology — first conflating the hermes Space with the mem0/LangGraph Space, then forgetting the hermes Space is a separate Space. The "三件套" (triad) is: **Hermes (sonoke Space, separate) = 云上大脑/入口** + **Mem0+LangGraph (nmem/memlg Space) = 记忆+编排器** + **Neon Postgres**. Before analyzing persistence or architecture, correctly identify which Space runs what — the user considers this foundational, not optional.

**Three-part triad = unified cloud brain (2026-08-17)**: The triad's final purpose is **统一和调度其他agent (云端或本地)** — hermes is the cloud entry point/router, LangGraph is the thin orchestrator, Mem0 is the shared memory layer. Other agents (local Claude/Codex, cloud第三方) are dispatched BY the triad, not part of it. nexus concept preserved (hermes=入口/路由/调度+统一记忆层+逻辑层与镜像分离+永续铁律+HF rebuild付费墙规避); infra simplified (R2砍, Supabase→Neon, GHCR砍, 4Space→2Space). Bucket vs Dataset选择按Space需求分: hermes Space用Bucket(rw挂载+state.db快照), memlg Space用Dataset(boot拉取逻辑层)。详见 `references/hf-bucket-vs-dataset.md`。

## Search Capability (AnySearch vs hermes built-in web_search)

hermes's built-in `web_search` tool has 7 backends (tavily, exa, parallel, firecrawl, searxng, brave-free, ddgs) — **all require API keys or extra setup**. On a fresh HF Space with no search backend configured, `web_search` defaults to `firecrawl` but fails silently (no key). This means **the built-in web_search is non-functional without explicit backend setup**.

AnySearch (installed as a hermes skill at `skills/research/anysearch/`) is the **only working search capability** when `ANYSEARCH_API_KEY` is configured. It provides general web search, 16 vertical domain searches (finance/academic/legal/health/security/code/etc.), batch parallel search (2-5 queries), and URL→Markdown extraction — all via a CLI tool calling JSON-RPC API. Free tier: 2000 calls/day, which is 10x typical daily usage (~100-200 calls).

**Do not write a hermes web provider plugin to integrate AnySearch as a `web_search` backend** — this requires source code changes (maintenance tax). AnySearch as a skill already works correctly; the agent reads the SKILL.md and calls the CLI directly. Downgrading AnySearch's multi-domain/batch/extract capabilities to the single-query `web_search` interface would be a feature regression.
