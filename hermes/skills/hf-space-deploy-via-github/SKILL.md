---
name: hf-space-deploy-via-github
description: "Deploy Docker services to HF Space via GitHub Actions CI/CD."
version: 1.0.0
author: Hermes Agent
license: MIT
platforms: [linux]
metadata:
  hermes:
    tags: [HuggingFace, GitHub, Actions, Deployment, Ephemeral, CI/CD]
    related_skills: []
---

# HF Space Deployment via GitHub Private Repo

Deploy Docker-based services to HuggingFace Spaces using a GitHub private repo as source of truth, with GitHub Actions controlling when HF Space rebuilds. Solves: (1) HF Space ephemeral disk loses everything on Restart, (2) pushing directly to HF Space git triggers Docker rebuild every time (risks free-tier ban), (3) no version history or diff on HF Space git.

## When to Use

- Deploying a self-hosted service (mem0 server, FastAPI, etc.) to HF Space free tier
- HF Space ephemeral disk means local files don't survive Restart
- You want version history + diff without triggering HF rebuild on every push
- Agent needs to maintain deployment files across sessions/restarts

## Architecture

```
GitHub Private Repo (source of truth, full history)
    ↓ GitHub Actions (push to main OR manual trigger)
HF Space (Docker SDK, port 7860, rebuilt only on deploy)
    ↓ HF Secrets (environment variables, survive restart)
Application (reads config from env vars at runtime)
```

Key principle: **push to GitHub does not rebuild HF Space**. Actions workflow controls when HF gets deployed.

## Prerequisites

1. GitHub account with a fine-grained PAT (see `references/fine-grained-pat.md`)
2. HF account with a Space (Docker SDK, port 7860)
3. HF token with write access to the target Space
4. gh CLI (optional but recommended, see below for sudoless install)

## Step-by-Step

### 1. Install gh CLI (Without sudo)

On restricted systems (HF Space, containers without root):

```bash
GH_VERSION=$(curl -sL https://api.github.com/repos/cli/cli/releases/latest | python3 -c "import sys,json; print(json.load(sys.stdin)['tag_name'][1:])")
curl -fsSL "https://github.com/cli/cli/releases/download/v${GH_VERSION}/gh_${GH_VERSION}_linux_amd64.tar.gz" -o /tmp/gh.tar.gz
mkdir -p ~/bin
python3 -c "
import tarfile
with tarfile.open('/tmp/gh.tar.gz', 'r:gz') as t:
    t.extractall('/tmp/gh-extracted')
" && cp /tmp/gh-extracted/gh_${GH_VERSION}_linux_amd64/bin/gh ~/bin/gh
chmod +x ~/bin/gh
export PATH="$HOME/bin:$PATH"
```

Ephemeral caveat: `~/bin/gh` is lost on HF Space Restart. For ongoing maintenance, use `git` + PAT (stored in HF Secrets `.env` file, survives restart). See `references/fine-grained-pat.md` for PAT setup.

### 2. Authenticate

```bash
export GITHUB_TOKEN=$(grep '^GITHUB_TOKEN=' "${HERMES_HOME:-$HOME/.hermes}/.env" | cut -d= -f2-)
echo "$GITHUB_TOKEN" | ~/bin/gh auth login --with-token
gh auth status
```

### 3. Create Private Repo + Push Files

```bash
gh repo create <repo-name> --private
cd /path/to/deployment-files
git init
git config user.email "agent@local"
git config user.name "Agent"
git remote add origin https://<github-user>:${GITHUB_TOKEN}@github.com/<github-user>/<repo-name>.git
git add -A
git commit -m "initial: deployment files"
git branch -M main
git push -u origin main
```

### 4. Add Docs Directory for Agent Context Continuity

Put a `docs/` directory in the repo with status files the agent reads on session resume:

```
repo/
├── docs/
│   ├── STATUS.md     # deployment progress checklist (done vs pending)
│   └── SECRETS.md    # secrets key names + descriptions (NO actual values)
```

Principle: GitHub repo is non-ephemeral. `docs/STATUS.md` survives across sessions/restarts. Agent clones repo, reads STATUS.md, resumes from exact checkpoint. More reliable than mem0 (which can compress/lose entries) or ephemeral files.

### 5. Write GitHub Actions Workflow

Create `.github/workflows/deploy-hf.yml`. See `templates/deploy-hf.yml` for a ready-to-use template.

Key workflow structure:
- Trigger: `workflow_dispatch` (manual) and/or `push` to main
- Checkout repo, copy deployment files to HF Space clone, commit, push to HF
- Uses `HF_TOKEN` and `HF_SPACE_PATH` from GitHub Secrets
- Skips push if no changes (idempotent)

### 6. Configure GitHub Secrets

```bash
echo "<hf-token>" | gh secret set HF_TOKEN --repo <github-user>/<repo-name>
echo "username/space-name" | gh secret set HF_SPACE_PATH --repo <github-user>/<repo-name>
gh secret list --repo <github-user>/<repo-name>
```

### 7. Pre-Deploy Token Safety Audit

Before pushing to GitHub (especially public repos), audit files for accidentally committed tokens:

```bash
grep -rn 'hf_[A-Za-z0-9]\{10,\}' . 2>/dev/null | grep -v '.git/'
grep -rn 'nvapi-\|github_pat_\|sk-' . --include='*.md' --include='*.yml' --include='*.sh' 2>/dev/null | grep -v '.git/'
```

GitHub Secrets (HF_TOKEN, etc.) are encrypted on GitHub's side — token values never appear in repo files. If the audit returns output, redact before pushing.

### 8. Trigger Deploy

```bash
gh workflow run deploy-hf.yml --repo <github-user>/<repo-name>
gh run list --repo <github-user>/<repo-name> --limit 5
gh run view <run-id> --repo <github-user>/<repo-name>
```

## Maintenance Across Sessions

When resuming in a new session (post-restart):

```bash
export GITHUB_TOKEN=$(grep '^GITHUB_TOKEN=' "${HERMES_HOME:-$HOME/.hermes}/.env" | cut -d= -f2-)
git clone https://<github-user>:${GITHUB_TOKEN}@github.com/<github-user>/<repo-name>.git
cat docs/STATUS.md  # resume from checkpoint
```

## Key Design Decisions

1. GitHub repo = source of truth, not HF Space git (weak history, rebuilds on every push)
2. Actions controls rebuild timing. Push to GitHub is free; deploy to HF is deliberate.
3. Docs in repo, not in memory. GitHub is non-ephemeral; mem0 compresses; ephemeral files delete.
4. PAT in HF Secrets `.env` survives Space Restart. gh binary doesn't, but git+PAT does.
5. Three-file minimum: Dockerfile + README.md + start.sh. All config via HF Secrets env vars.

## Pitfalls

| Problem | Cause | Fix |
|---|---|---|
| Actions fails: HF Space not found | Space not yet created on HF | Create Space first at huggingface.co/new-space (Docker SDK) |
| gh command not found after restart | gh binary on ephemeral disk lost | Use `git` + `$GITHUB_TOKEN` from `.env` instead |
| Fine-grained PAT 403 on Actions API | Missing Actions: Read and write permission | Regenerate PAT with correct scopes (see `references/fine-grained-pat.md`) |
| gh run view --log-failed returns 403 | Fine-grained PAT lacks checks:read (unavailable on fine-grained) | Use `gh api repos/OWNER/REPO/actions/jobs/JOB_ID/logs` instead, or view on GitHub Web UI |
| Actions succeeds but "No changes, skipping push" | HF Space files already match GitHub files (idempotent) | Force update: change a file in GitHub repo, or add empty commit `git commit --allow-empty -m "force deploy"` |
| Fine-grained PAT 403 on gh api actions/permissions | PAT missing Actions: Read permission | Regenerate PAT with `Actions: Read and write` scope |
| Frequent HF rebuilds risk ban | Actions deploying on every push | Switch to workflow_dispatch only or tag-based |
| sudo: command not found | HF Space has no root access | Use binary-only install method (Step 1 above) |
| /usr/local/bin not writable | No root on HF Space | Install to ~/bin and add to PATH |
| Application crash: `sqlite3.OperationalError: unable to open database file` | start.sh didn't create directories the app expects | Add `mkdir -p /app/<dir>` in start.sh before launching |
| HF Space runs old code despite successful Actions deploy | HF Space had files from a previous project; `cp` only overwrites, doesn't delete stale files | Add cleanup loop in deploy-hf.yml: after `cp`, `rm -rf` all files except Dockerfile/README.md/start.sh/.gitattributes. See `templates/deploy-hf.yml` for the pattern |
| HF token from account A can't push to account B's Space | "Invalid username or password" error from HF API | Generate token from the Space owner's account (account B), not the agent's account (account A) |
| HF Space URL returns 404 even though Space exists | Space is paused/stopped by user, or building | Unpause Space in HF UI, wait for build to complete |
| HF API create Space: "You don't have the rights to create a space under the namespace" | Token doesn't have Space creation permission (read-only or dataset-only token) | Use a token with `Spaces: Write` permission, or create Space via HF web UI |
| HF API create repo: "Only regular characters..." error | `name` field included namespace prefix (e.g., `nmem/memg`) | Use just the repo name without namespace: `{"name":"memg"}` not `{"name":"nmem/memg"}` |
| `huggingface_hub.upload_file() got unexpected keyword argument` | Older huggingface_hub version uses different param names | Check signature: `inspect.signature(api.upload_file)` — v1.26 uses `path_or_fileobj` not `path_or_pathobj` |
| sonoke's HF token can't see nmem's private Space/Dataset | Different HF accounts, no shared visibility | Use the Space owner's token (nmem's), not the agent's token (sonoke's) |
| Two HF_TOKENs in environment | daemon environ has one account's token, `.env` has another's | Use `whoami-v2` API to identify which account each token belongs to: `curl -H "Authorization: Bearer $TOKEN" https://huggingface.co/api/whoami-v2` |
| `huggingface-cli upload` prints `hf` command suggestions instead of uploading | Old CLI version (1.26+) redirects `huggingface-cli` to `hf` subcommands | Use `huggingface_hub` Python API directly: `from huggingface_hub import HfApi; api.upload_file(...)` — check `inspect.signature(api.upload_file)` for correct param names (v1.26 uses `path_or_fileobj`, not `path_or_pathobj`) |
| Space renamed (not created) but API returns 404 | HF Space was renamed (e.g., 0→memg→memgraph); old references break | Update ALL references (GitHub Secrets HF_SPACE_PATH, docs/, URL endpoints) to the new Space name. Verify with `curl /api/spaces/<owner>/<current-name>` using the owner's token |
| HF Space paused by user to avoid rebuild risk | Space is stopped/paused in HF UI; API returns 404, git clone fails | User must unpause Space in HF UI before deploy. cron-job.org pings will also fail while paused |
| `/health` endpoint not injected — "already present" false positive | main.py contains `/health` as a substring (e.g., mem0 server has `"/api/health"` in `SKIPPED_REQUEST_LOG_PATHS`) | Match the decorator exactly: `if '@app.get("/health"' not in code and "@app.get('/health'" not in code:` — never use bare `'/health' in code` |
| `openai.APIConnectionError: Illegal header value b'Bearer  '` (two spaces) | `OPENAI_API_KEY` env var is empty/unset; mem0 default config tries to use it as embedder key | Set `OPENAI_API_KEY` in HF Space Secrets to the embedder provider's key (e.g., NIM key `nvapi-...`). Either use `/configure` to override at runtime, or just put the embedder key directly in `OPENAI_API_KEY` (simpler, user-preferred) |
| mem0 `/configure` returns `{"detail":"JWT_SECRET is not configured."}` | `AUTH_DISABLED=true` skips auth on normal endpoints but `/configure` still requires admin JWT | Set `JWT_SECRET` (random string) and `ADMIN_API_KEY` in HF Space Secrets. Then call `/configure` with `Authorization: Bearer <ADMIN_API_KEY>` |
| POST to HF Space URL returns HF 404 HTML page (not FastAPI JSON) | HF Space proxy sometimes intercepts POST requests when Space is building/transitioning | Wait for Space to finish building (check with `GET /health` first), then retry POST. If persistent, try without `Authorization` header or use `X-API-Key` instead of `Bearer` |
| Need to set HF Space Secrets programmatically | Manual UI is tedious, agent has no browser access | `python3 -c "from huggingface_hub import HfApi; api = HfApi(token='<token>'); api.add_space_secret('<owner>/<space>', '<KEY>', '<value>')"` — triggers automatic Space rebuild |
| GET to private HF Space returns HF 404 HTML (not FastAPI JSON) | HF private Spaces require `Authorization: Bearer <HF_TOKEN>` for ALL HTTP access — even GET `/health`, `/docs` | Always pass `-H "Authorization: Bearer <token>"` in curl. Without it, HF's proxy returns its own 404 HTML page. This is HF Space access control, NOT mem0 auth |
| mem0 `/configure` returns `JWT_SECRET is not configured` even with `AUTH_DISABLED=true` | `/configure` is admin-only, always requires JWT regardless of `AUTH_DISABLED` | **Option A**: Set `JWT_SECRET` + `ADMIN_API_KEY`, call `/configure` with `Authorization: Bearer <ADMIN_API_KEY>` — BUT this enables JWT on ALL endpoints. **Option B (recommended)**: Patch `DEFAULT_CONFIG` in `start.sh` to inject per-provider keys directly — no JWT needed, no `/configure` call. See § "Patching DEFAULT_CONFIG" above |
| Setting `JWT_SECRET` blocks `/search` and `/memories` for callers without JWT | `JWT_SECRET` enables JWT auth on ALL endpoints, not just `/configure` | Delete `JWT_SECRET` and `ADMIN_API_KEY` from HF Space Secrets. Use DEFAULT_CONFIG patch approach instead. `AUTH_DISABLED=true` alone keeps all endpoints open |
| HF Space "Collision on variables and secrets names" configuration error | A Secret/Variable name collides with an HF-reserved runtime variable name (e.g., `HF_TOKEN` is auto-injected by HF — setting it as a Secret causes collision) | Use a non-reserved name (e.g., `WORKER_TOKEN` instead of `HF_TOKEN`). Update `start.sh` to read from the new name: `os.environ.get('WORKER_TOKEN') or os.environ.get('HF_TOKEN', '')`. Other reserved names: `SPACE_ID`, `SPACE_AUTHOR`, `SPACE_REPO_NAME` |
| `AUTH_DISABLED` Variable causes "Collision on variables and secrets names" error | `AUTH_DISABLED` is also an HF-reserved variable name — HF's own auth system reads it. Setting it as a Space Variable or Secret causes collision | Do NOT set `AUTH_DISABLED` as an HF Variable/Secret. Inject it in `entrypoint.sh` (HF Dataset, hot-reloadable) at process launch: `exec env AUTH_DISABLED=true uvicorn main:app --host 0.0.0.0 --port 7860`. This makes it available to the uvicorn process without HF's build system seeing the name. **Note**: If switching to `ADMIN_API_KEY` auth, remove the `env AUTH_DISABLED=true` from this line entirely (see `references/mem0-server-auth.md` § Switching) |
| `/search` `/memories` return 401 "Invalid or expired token" even with `AUTH_DISABLED=true` | HF private Space auto-injects `Authorization: Bearer <HF_TOKEN>` into the request. mem0 `verify_auth` checks Bearer FIRST (line 158), tries JWT decode, RAISES 401 — never reaches `AUTH_DISABLED` check (line 171). **Private Space + AUTH_DISABLED are fundamentally incompatible** | Make the Space **public** (`api.update_repo_visibility(..., private=False)`). Public Space → HF doesn't inject Bearer → mem0 skips to `AUTH_DISABLED` → 200 OK. See `references/mem0-server-auth.md` for the full `verify_auth` flow analysis |
| After removing `AUTH_DISABLED=true` from `entrypoint.sh`, Space still allows anonymous POST /memories (no 401) | `AUTH_DISABLED` was previously set as a **Space Variable** (not just in entrypoint.sh). HF injects Space Variables into the container environment independently of the entrypoint script. mem0's `os.environ.get("AUTH_DISABLED","")` still reads `true` from the HF-injected env var, bypassing auth even though the entrypoint no longer sets it | **Delete the `AUTH_DISABLED` Space Variable**: `api.delete_space_variable("<owner>/<space>", key="AUTH_DISABLED")` or remove in HF UI → Settings → Variables. This is a separate step from editing entrypoint.sh — both must be done. Then restart Space |
| Worker `/worker/run` returns 401 even with correct X-API-Key | Worker router's auth check failed silently (exception swallowed by broad `except`) — typically because `from auth import ADMIN_API_KEY` failed (module not in sys.path at import time). The worker graph code runs in a different module context than `main.py` | Use `try/except` around the import with a fallback: `try: from auth import ADMIN_API_KEY\nexcept: ADMIN_API_KEY = os.environ.get("ADMIN_API_KEY","")`. Or use the `/worker/health` debug endpoint to verify component status before testing `/worker/run` |
| LangGraph worker `plan` node returns empty string (not error message) | LLM call returned empty response or langgraph graph didn't execute the `plan()` function. Check: (1) `GET /worker/health` → `langgraph: True`? (2) `llm_api_key: True`? (3) Space logs for `[plan]` or `[LLM]` messages. Common cause: Zhipu 429 rate-limit during Space boot (mem0 server's own LLM + worker's plan node hit Zhipu simultaneously) | Add `retry+backoff` to LLM calls (3s/6s wait). Wait 15-20s after Space restart before testing (let rate-limit window clear). Use `timeout=15` not `30`, `max_tokens=512` not `1024` |
| Worker `mem0_search()` in-process returns 0 results but HTTP `/search` returns results (or vice versa) | `user_id` mismatch: hermes sends `filters={{"user_id": "hermes-user"}}` (no agent_id). Worker in-process `mem.search(query=..., user_id=...)` uses different user_id or no filter. Also, hermes may not have restarted → `mem0_search` uses old backend → stale data | Ensure worker uses `user_id` from the request body, and hermes process is restarted after config change. Check `GET /memories` (no user_id filter, admin) to see ALL stored memories and their user_id values |
| Rule-match keywords cause false-positive routing (e.g., "翻译成英文: 今天天气很好" → search instead of direct) | Broad keywords like "今天" or "状态" match inside non-search tasks. Chinese keyword substring matching is especially prone to this | Remove ambiguous keywords from `search_keywords` ("今天", "状态", "check"). Add coding keywords that are specific ("部署", "重命名"). Test each keyword against a set of representative tasks before deploying. Keyword lists are in the `plan()` function of `templates/langgraph_worker_skeleton.py`. The five keyword lists are: `coding_keywords`→delegate, `search_keywords`→search, `research_keywords`→search_and_extract, `file_keywords`→write_file, and LLM fallback→direct |
| Hermes `mem0_search` returns 0 results after restarting hermes with new self_hosted config | The Neon `memories` table was rebuilt (e.g. after `DROP TABLE` in patch 30) — it's genuinely empty. `mem0_search`/`mem0_add` work fine (verifiable via direct HTTP to HF Space `/memories`), there's just nothing stored yet. Solved automatically after first `mem0_add` writes go through. Also: hermes process must be fully restarted after changing `mem0.json` — `hermes serve --stop` + restart gateway, verify with `hermes mcp list` showing the server | Run `mem0_add` to store a test fact, then `mem0_search` to verify. Also curl `GET /memories?user_id=916612938` with `X-API-Key` to see all stored memories on the server side |
| Hermes MCP worker tools registered but LLM doesn't call them for simple tasks | `run_worker` tool description says "不合适简单的直接问答" — LLM correctly bypasses for simple search/Q&A, using built-in `anysearch` skill instead. This is **by design**, not a bug | To verify MCP routing: send a task that needs orchestration (memory + search + write) or use explicit phrasing ("调用 run_worker"). See `references/mcp-server-for-hermes.md` § "LLM May Not Route to MCP Worker" |
| `anysearch_extract` returns empty string in `search_and_extract` branch | `anysearch_extract()` didn't parse `result.content[0].text` format — old code expected a bare string. New code checks `result.content` list first | Patch `anysearch_extract()` to parse `result.content[].text` (same as `anysearch()`). See `references/langgraph-worker-llm-quota-conservation.md` § AnySearch API Protocol |
| `snapshot_download` pulls stale Dataset code after uploading new `graph/__init__.py` and restarting Space — new routes return 404 | Space restarts in ~10s (health check passes) but `snapshot_download` may serve cached files from a previous revision. A single restart doesn't always pick up the latest Dataset commit | Upload the file, restart Space, check `GET /openapi.json` for expected routes. If missing, restart Space AGAIN (second restart forces fresh pull). Or use `revision=<commit_id>` in `snapshot_download` to lock the atomic snapshot |
| `hf buckets list` returns "No results found" or 404 for a bucket you know exists | Token `whoami` belongs to a different HF account than the bucket namespace | `api.whoami()` to check token owner; use a token from the bucket owner's account. Cross-account bucket access requires the owner's token |
| `set_space_volumes` call triggers unexpected Space restart | This is by design — any volume config change restarts the Space | Plan for ~30s downtime. Verify boot script (start.sh) pulls code from the newly mounted path before calling |
| HF persistent storage (`/data` as overlay) deprecated | HF staff @Wauplin (issue #3806, 2026-02): "persistent storage is currently being a slowly deprecated feature" | Use Storage Buckets instead — Buckets GA 2026-03, NOT deprecated. Check `df -T /data`: `fuse` = Bucket mount (good), `overlay` = old persistent storage (migrating off recommended) |
| `/worker/tasks` returns 404 even though the route is in the code | Same root cause as stale `snapshot_download` — the Space is running an older version of `graph/__init__.py` from the Dataset cache | Verify with `GET /openapi.json` → check `paths` dict for `/worker/tasks`. If not present, the Space didn't load the new code. Re-upload + restart again. The `openapi.json` check is the definitive route-verification method (not just `/health` returning 200) |
| AnySearch `search` branch returns results but all fields (title, url, snippet) are empty | AnySearch uses JSON-RPC 2.0 (NOT REST). The old code called `api.anysearch.pro/v1/search` (wrong endpoint, returns empty). Correct: `api.anysearch.com/mcp` with `method=tools/call`. Response is `result.content[0].text` — a Markdown string, NOT structured JSON with title/url/snippet keys | Read the AnySearch CLI script source (`anysearch_cli.py`) to find the real protocol. Fix `anysearch()` to use JSON-RPC format. Parse `result.content[0].text` as Markdown text (not structured fields). See `references/langgraph-worker-llm-quota-conservation.md` § "AnySearch API Protocol" |
| `huggingface_hub` returns 401/404 `Repository Not Found` for a private Dataset/Space you own | Token belongs to a different HF account than the repo owner. hermes `.env` `HF_TOKEN` may be from account A while the repo is under account B. Or token expired/revoked | `api.whoami()` to verify token owner matches repo owner. Regenerate token from the correct account at huggingface.co/settings/tokens. Read full token from `.env` file directly (not `os.environ.get()` — may truncate long values) |
| `execute_code` or `os.environ.get("HF_TOKEN")` returns truncated token (13 chars instead of 37) | Long env values passed as string literals through `execute_code` get truncated OR `os.environ` in the sandbox does not inherit the parent process env at all | Read `.env` file directly: `Path("/opt/data/.hermes/.env").read_text()` then parse line for full 37-char token. Do NOT pass tokens as string literals into `execute_code` — sandboxed environments truncate or drop them. Use `terminal(command="python3 << PYEOF ... PYEOF")` heredoc pattern which inherits the parent shell env including all `.env` variables |
| `/search` returns `Upstream provider error`; `/memories` returns `datastore_unavailable` after a restart that followed a `POST /configure` call | `server_state.py` `_load_overrides()` reads Neon `settings` table `config_overrides` and deep-merges over DEFAULT_CONFIG. Pydantic models strip `openai_base_url` (Issue #4910) — embedder sends to `api.openai.com` instead of NIM/智谱 | Delete the DB override: `DELETE FROM settings WHERE key='config_overrides';` then restart. Do NOT call `/configure` when using the DEFAULT_CONFIG patch — see `references/mem0-server-config.md` § "DB Config Overrides Can Strip openai_base_url" |
| `POST /memories` returns 422 `{"detail":[{"type":"missing","loc":["body","messages"],"msg":"Field required"}]}` | mem0 requires `messages` array, not bare `content` | Use `{"messages":[{"role":"user","content":"text"}],"user_id":"test"}` — see `references/mem0-server-config.md` § "mem0 API Payload Requirements" |
| `POST /add` returns 404 Not Found | mem0 server has **no `/add` endpoint** — correct route is `POST /memories` | Not a bug — 404 on `/add` is scanner noise. Use `POST /memories` for creating memories |
| `POST /memories` returns `datastore_unavailable` on first request, then `unknown` (Upstream provider error) on subsequent requests | **Neon scale-to-zero severs pooled psycopg3 connections silently**. `ConnectionPool` has no `check` callback — it hands out dead connections → `psycopg.OperationalError: the connection is lost` in `_get_cursor` → classified as `datastore_unavailable`. Second request reaches a new connection but a later step fails → `unknown` | **Add `check=ConnectionPool.check_connection` to ConnectionPool** in patch 20 — psycopg3's built-in static method (psycopg-pool 3.2+, ticket #790, sends empty query). Dead connections discarded and re-created automatically. See `references/mem0-server-config.md` § "Neon scale-to-zero + psycopg3 ConnectionPool" |
| `POST /memories` and `POST /search` return `unknown` (Upstream provider error) even after `check_connection` fix and even after `GRANT CREATE ON SCHEMA public` was already run | `psycopg.errors.InsufficientPrivilege: permission denied for schema public` persists because **mem0 server uses TWO different database connections**: SQLAlchemy reads `APP_DB_NAME` (e.g. `neondb`) for auth/settings tables, but pgvector reads `POSTGRES_DB` (default `postgres`) for the `memories` table. The GRANT was run on `neondb` but pgvector connects to `postgres` — each database has its own independent `public` schema ACL (PostgreSQL 15+). `InsufficientPrivilege` not in `_DB_NAMES` → classified as `unknown` (misleading) | **Option A (recommended)**: Set `POSTGRES_DB` HF Space Variable to match the database where the GRANT was run (e.g., `neondb`). **Option B**: Switch Neon SQL Editor to the `postgres` database and run `GRANT CREATE ON SCHEMA public TO neondb_owner;` there too. See `references/mem0-server-config.md` § "mem0 server uses TWO different database connections" for the full env var mapping |
| `POST /memories` returns 200 OK but logs show `DataException: expected 1536 dimensions, not 2048`; `POST /search` returns `{"results":[]}` | NIM `nemotron-3-embed-1b` outputs 2048-dim vectors but pgvector table created with 1536-dim (OpenAI default). `Memory.add()` catches the insert error silently — API returns 200 but vector is never stored | Set `"embedding_model_dims": 2048` in the **vector_store config** (NOT embedder config) in `patches/10_default_config.py` — the field is `embedding_model_dims` (psycopg pgvector config) NOT `embedding_dims`. Also `DROP TABLE IF EXISTS memories CASCADE` in patch 30 (or Neon SQL Editor) before restart — `CREATE TABLE IF NOT EXISTS` won't recreate an existing 1536-dim table. Restart Space. Common dims: OpenAI text-embedding-3-small=1536, NIM nemotron-3-embed-1b=2048, OpenAI text-embedding-3-large=3072 |
| After setting `embedding_model_dims: 2048`, all endpoints return `Upstream provider error` (`unknown`); logs show `ProgramLimitExceeded: column cannot have more than 2000 dimensions for hnsw index` | pgvector's HNSW index has a **hard 2000-dimension limit**. 2048-dim NIM embeddings exceed it. mem0's pgvector config defaults `hnsw=True`, so `create_col()` tries `CREATE INDEX ... USING hnsw` and fails | Set `"hnsw": False` in the **vector_store config** alongside `embedding_model_dims: 2048`. Without HNSW, pgvector uses brute-force sequential scan (fine for <100K vectors). Alternative: switch to an embedder with ≤2000 dims (e.g., `nvidia/nemotron-mino-embed-2b-8k` at 1536 dims). See `references/mem0-server-config.md` § "HNSW Index 2000-Dimension Limit" |
| Container pulls stale Dataset code on restart | `snapshot_download` defaults to `main` HEAD; if a push is in-flight during boot, you pull an older snapshot (race: boot vs sync push) | **Race fix**: fetch HEAD `commit_id` via `HfApi().list_repo_commits()`, pass as `revision=` to lock atomic snapshot. See `references/hf-space-perpetual-architecture.md` § "Race-Condition Fix" |
| `hf download` stderr leaks HF_TOKEN in logs | HF error messages echo the auth token | Redact: `sed "s/$HF_TOKEN/[REDACTED]/g" "$_err" >&2`. Do NOT use `2>&1 \| sed` — POSIX sh has no PIPESTATUS, `$?` reflects sed's exit not download's. Use `2>file; then sed file` pattern |

## Keep-Alive (Neon + cron-job.org)

When deploying to HF Space with Neon Postgres backend, use cron-job.org to ping `/health` every 4 minutes. One ping keeps alive all three free-tier services (cron-job.org → HF Space → Neon). See `references/neon-cronjob-keepalive.md` for full setup, including:
- `/health` endpoint injection (idempotent, dynamic patch at startup; see `references/mem0-server-config.md` for the false-positive substring match pitfall)
- Neon region selection (AWS us-east-1 matches HF Space)
- Neon multi-project for stacked quotas (100 projects × 0.5GB each)
- Neon vs Supabase comparison

## Hot-Reload Pattern (HF Dataset OR HF Bucket for frequently-changed code)

When a Space runs multiple components (e.g., mem0 server + LangGraph worker) and the three-file budget is frozen, store hot-reloadable code in an HF Dataset repo OR an HF Storage Bucket. `start.sh` pulls code on boot — no Docker rebuild needed, only container restart.

**Dataset** (`snapshot_download`): simpler, read-only in Space, has git history (bloats on every sync). Best when code only needs to be pulled at boot, not written at runtime.

**Bucket** (`hf buckets sync`): rw mount via Volume, no git history (overwrite in place), rsync-style incremental transfer, delete frees quota. Best when Space needs runtime read-write access to persistent files or when syncing frequently. See `references/hf-bucket-vs-dataset.md` for the full comparison + Python API (`create_bucket`, `set_space_volumes`, Volume mount config).

**Unified Bucket pattern** (user's choice for multi-Space deployments): Use GitHub private repo as version-controlled source of truth, GitHub Actions to `hf buckets sync` logic layer to Bucket on push. Bucket provides rw runtime storage without git bloat. Two Spaces each mount their own Bucket volume (`sonoke/logic`, `nmem/logic`) to `/data`. Three files stay in HF Space git repo (frozen). Config via HF Secrets only — zero file persistence needed for config. See `references/nexus-repo-organization.md` for the `spaces/<name>/` directory structure pattern, STATUS.md per-Space doc format, and the Bucket boot-pull migration from `snapshot_download` to `hf buckets sync`.

## 永续架构 Pattern (Three-Layer Decoupling + start.sh → entrypoint.sh)

For long-term unattended HF Space operation, follow the perpetual-architecture pattern distilled from production repos (n-omn/nexus血统): three-layer decoupling (环境层/逻辑层/运行态), `start.sh` as thin bootstrap that `exec`s into the Dataset's `entrypoint.sh`, race-condition fix (HEAD commit_id lock before pull), HF_TOKEN stderr redaction, and HEALTHCHECK in Dockerfile. See `references/hf-space-perpetual-architecture.md` for the full pattern with code examples.

**Adaptation rule (user preference)**: When studying reference repos (n-omn, nexus, etc.) for patterns, **read the FULL source first** — understand why each design choice was made for that specific stack — then adapt to your stack's constraints. Do NOT blindly copy code blocks from a Node.js service into a Python service, or vice versa. The reference repo's `entrypoint.sh` may manage multi-process orchestration (Node.js clusters, litestream restore, trap handlers) that a single-process Python uvicorn deployment doesn't need. Look at the pattern's *purpose*, then implement the *equivalent* for your stack.

## Anti-Detection (HF 风控)

For HF accounts where frequent Docker rebuilds may trigger risk control:
- Keep `README.md` minimal — only HF frontmatter (title, emoji, sdk, app_port, pinned). Move all documentation to `docs/` (not deployed to HF, stays in GitHub only).
- Minimize pushes that trigger Actions deploy. Use `workflow_dispatch` (manual) or tag-based triggers instead of auto-deploy on every push.
- Use a separate HF account for the deployment Space (different from primary hermes Space account) to isolate risk.

## Nexus Architecture (Multi-Component Worker Space)

When deploying a Worker Space that runs multiple components (e.g., LangGraph orchestrator + mem0 memory server), define clear roles:

| Component | Role | What it does NOT do |
|---|---|---|
| **mem0 server** | Memory storage + retrieval (HTTP API on port 7860) | Not an orchestrator, not a workflow engine |
| **LangGraph** | Workflow orchestration (state graph, branching, retry) | Not a memory store — uses mem0 as a node/tool |
| **Neon** | Source of truth (pgvector memory + task_logs + app state) | Not ephemeral, not optional |
| **HF Dataset** | Hot-reloadable code store for LangGraph worker code | Not deployed to Space git, no rebuild trigger |

**Call hierarchy**: External request → Hermes (entry point, decides: direct answer / query memory / run graph) → Worker Space (LangGraph orchestrates multi-step, calls mem0 search/add as nodes) → Neon (persists everything).

**LangGraph on free HF = 瘦编排 (thin orchestrator), not full agent runtime**: State graphs, conditional edges, retry, and Neon checkpoint all work. But no large-scale parallel subgraphs, no heavy in-graph coding agents (offload to external Claude Code / Codex), no hour-long uninterrupted runs (sleep/timeout). Heavy work goes to external compute; HF Space only orchestrates and calls APIs.

### LangGraph Worker Implementation (Production Lessons)

The `templates/langgraph_worker_skeleton.py` file contains a battle-tested worker implementation. Key design decisions learned from production deployment:

1. **In-process mem0 calls, NOT HTTP self-call**: When LangGraph and mem0 server run in the SAME HF Space container, call `server_state.get_memory_instance()` directly for `.search()` / `.add()`. Do NOT use `requests.post("http://127.0.0.1:7860/memories", ...)` — the HTTP round-trip goes through the full NIM embedding + LLM pipeline and 10s timeout is not enough. In-process calls skip HTTP and use the already-initialized Memory object.

2. **LLM quota conservation — rule-match in plan, local heuristic in reflect**: Zhipu GLM-4.7-flash has tight QPS limits (429 on consecutive calls). The `plan` node matches coding/search keywords FIRST (zero LLM cost) — only ambiguous/direct tasks fall through to an LLM call. The `reflect` node uses LOCAL heuristics (result length, prefix checks) — NEVER calls the LLM. This reduces LLM calls from 3 (plan+act+reflect) to at most 2 (plan-LLM-fallback + act-direct), and usually 0-1 (delegate & search branches skip LLM entirely). The `llm_chat()` function also retries with 3s/6s backoff. Use `timeout=15` (not 30) and `max_tokens=512` (not 1024).

3. **Six-node graph with conditional retry**: `retrieve → plan → act → verify →(conditional)→ reflect → write → END`, with a retry edge from `verify` back to `act` (max 1 retry, triggered when result is suspiciously short). `act` has FIVE branches: `direct` (LLM answers), `search` (AnySearch JSON-RPC — returns Markdown text, NOT structured JSON), `search_and_extract` (search + regex-extract URL from results + AnySearch extract for full page content), `write_file` (write to HF Space `/data` directory with filename sanitization + 50KB cap), `delegate` (writes task to Neon `task_queue` table via psycopg direct connect — creates table if not exists, inserts pending task with UUID task_id, returns `task_id` for external consumers to poll). **AnySearch protocol**: JSON-RPC 2.0 at `api.anysearch.com/mcp`, `method=tools/call`, `params.name=search|extract`, response is `result.content[0].text` (Markdown, not structured JSON). See `templates/langgraph_worker_skeleton.py` for the full implementation including the `anysearch()`, `anysearch_extract()`, `_write_file_to_space()`, and `write_task_to_neon()` helpers.

4. **Worker task lifecycle API**: The delegate branch writes tasks to Neon `task_queue` with status `pending`. External consumers (local machine, NPC, another agent) need to poll, claim, and complete these tasks. Add two endpoints to the worker router:
   - `GET /worker/tasks?status=pending&limit=10` — query Neon task_queue by status, returns JSON array of tasks (task_id, task, user_id, status, created_at, completed_at, result). Auth: `X-API-Key` header.
   - `PATCH /worker/tasks/{task_id}` — update task status + write result. Body: `{"status":"completed","result":"..."}`. Sets `completed_at=now()`. Auth: `X-API-Key` header.
   
   Full closed loop: `worker delegate → INSERT task_queue (pending) → external poller GET /worker/tasks → execute → PATCH /worker/tasks/{id} (completed + result) → confirm via GET ?status=completed`.

5. **Cron-based task poller**: Use `hermes cronjob` with `no_agent=True` + a Python script to poll `/worker/tasks?status=pending` every 30 minutes. Script pattern: if pending tasks exist, print a notification (stdout = delivered message); if none, `sys.exit(0)` silently (empty stdout = no notification). Store the script at `~/.hermes/scripts/poll_worker_tasks.py`. The script reads `WORKER_URL` and `WORKER_API_KEY` from `.env` and uses `requests` with a 30s timeout. See `scripts/poll_worker_tasks.py` for the implementation. If the script returns non-zero exit, it's an error alert (not silent).

6. **Auth: reuse mem0's ADMIN_API_KEY**: The worker's `/worker/run` endpoint must check `X-API-Key` against `ADMIN_API_KEY` — the same key mem0 server uses. Import `from auth import ADMIN_API_KEY` + `fastapi.security.APIKeyHeader`. Do NOT build a separate auth system. `/worker/health` (debug) can skip auth.

7. **Sequential fallback**: If `langgraph` package not installed (pip install failed), run all six nodes in sequence. Same result, just no graph engine or conditional edges.

8. **Worker deployment flow**: Upload `graph/__init__.py` to HF Dataset via `api.upload_file(repo_type="dataset")` + `api.restart_space()`. Wait ~50s for Space boot. Test with `GET /worker/health` first (checks all component statuses), then `POST /worker/run`. Wait 15-20s after Space restart before LLM-bearing tests (let Zhipu rate-limit window clear — Space boot triggers mem0 server's own LLM calls). Verify all routes registered: `GET /openapi.json` → check `paths` for `/worker/tasks`, `/worker/tasks/{task_id}`, `/worker/run`, `/worker/health`.

9. **Worker→Hermes via MCP** ⚠ **DEPRECATED (2026-08-18)**: nexus-worker MCP stdio 桥已废。换装后 Hermes Agent 走原生 plugin `scripts/plugins/nexus-r2/` 三 tool (`nexus_call_claude`/`nexus_call_codex`/`nexus_route_langgraph`) 经 `libs/shared/gateway.call_space` 直调下游 Space, 替掉本 stdio 中转。本 `nexus_worker_mcp.py` 仓内零引用, 不再 `hermes mcp add nexus-worker`。旧 MCP 桥模式见 `references/mcp-server-for-hermes.md` (已标 DEPRECATED, 留历史回溯)。`kind=graph` 异步路 (Stage B 增强) 改经 Neon `task_queue` + `FOR UPDATE SKIP LOCKED` 轮询。

### Persistence and Logging for Multi-Component Space

On HF free tier ephemeral disk, determine what needs persistence:

| Content | Needs persistence? | How |
|---|---|---|
| mem0 vector memory | ✅ | Neon pgvector (external, not ephemeral) |
| mem0 auth/settings | ✅ | Neon tables (alembic-managed) |
| LangGraph checkpoint | ✅ | Neon (store graph state in a table) |
| LangGraph run logs | ✅ | Neon `task_logs` table (write via psycopg, NOT local files — ephemeral disk wipes them) |
| SQLite history.db | ❌ | mem0 uses it for LLM call history; losing it doesn't affect memory search; optional |
| uvicorn stdout | ❌ | HF Space Logs page shows recent stdout; sufficient for live debugging |
| LangGraph worker code | ✅ | HF Dataset (pulled on boot via snapshot_download) |

**Rule: ephemeral disk is scratch space only.** Any state that matters goes to Neon or HF Dataset. No local file is a truth source. See `references/mem0-server-config.md` for mem0-specific HF Space Secrets, `/configure` endpoint auth, and `/health` injection pitfalls. See `references/mem0-server-auth.md` for the mem0 native auth chain (`verify_auth` priority, `ADMIN_API_KEY` vs `AUTH_DISABLED`, endpoint auth levels, HF Bearer passthrough, and hermes `SelfHostedBackend` client behavior). See `references/mem0-server-state.py.md` for the `server_state.py` config loading/override logic and `verify_auth` flow analysis (critical for debugging `openai_base_url` loss and auth bypass issues). See `references/langgraph-worker-llm-quota-conservation.md` for production patterns on reducing LLM calls in a LangGraph worker graph (rule matching, local heuristic reflect, conditional retry, AnySearch JSON-RPC protocol for search/extract branches). See `references/mcp-server-for-hermes.md` for exposing a worker as MCP tools to Hermes (stdio MCP server pattern, `hermes mcp add` registration, blocklist workaround). See `scripts/poll_worker_tasks.py` for the cron-based task poller script (no_agent=True, silent on no pending, notification on pending).

### Neon Project Setup (Don'ts)

When creating a Neon project for mem0 server backend:
- **Do NOT select "Neon Auth" / Backend Services** — mem0 server has its own auth (`AUTH_DISABLED=true` + `X-API-Key`). Neon Auth adds an unrelated OAuth/magic-link user system and a `neon_auth` table you don't need.
- **DO run `CREATE EXTENSION IF NOT EXISTS vector;`** in the SQL Editor after project creation.
- **Select AWS us-east-1** (same region as HF Space, <1ms latency).

## HF API for Space/Dataset Creation

Programmatically create HF Spaces and Datasets via `POST /api/repos/create`. Key gotcha: `name` field is just the repo name without namespace prefix; namespace comes from the token owner. Token must have appropriate write permissions (Spaces: Write or Datasets: Write). See `references/hf-api-create-repos.md` for curl examples, token permission matrix, and cross-account token handling. For the HF web UI token creation walkthrough (fine-grained, Write level, per-Space scope), see `references/hf-fine-grained-token-ui.md`.

## Patching DEFAULT_CONFIG to Bypass /configure (mem0 Server)

When deploying mem0 server with different providers for embedder (NIM) and LLM (智谱), the `/configure` endpoint is the intended way to set per-provider keys + base_urls. But `/configure` requires JWT admin auth even with `AUTH_DISABLED=true` — setting `JWT_SECRET` enables JWT auth which then blocks unauthenticated `/search` and `/memories` calls too.

**Workaround: patch `DEFAULT_CONFIG` directly in `start.sh` before uvicorn starts.** This avoids `/configure` entirely. The patch:
1. Reads separate env vars (`NIM_API_KEY`, `ZAI_API_KEY`, `NIM_BASE_URL`, `ZAI_BASE_URL`) — NOT the shared `OPENAI_API_KEY`
2. Replaces the `llm.config` dict in `main.py` to use `ZAI_API_KEY` + `ZAI_BASE_URL` + `glm-4.7-flash`
3. Replaces the `embedder.config` dict to use `NIM_API_KEY` + `NIM_BASE_URL` + `nvidia/nemotron-3-embed-1b`
4. Falls back to `OPENAI_API_KEY` if `NIM_API_KEY`/`ZAI_API_KEY` not set

```python
# In start.sh, before alembic migration:
python3 -c "
with open('/app/main.py', 'r') as f:
    code = f.read()
extra_vars = '''
NIM_API_KEY = os.environ.get(\"NIM_API_KEY\") or os.environ.get(\"OPENAI_API_KEY\", \"\")
ZAI_API_KEY = os.environ.get(\"ZAI_API_KEY\") or os.environ.get(\"OPENAI_API_KEY\", \"\")
NIM_BASE_URL = os.environ.get(\"NIM_BASE_URL\", \"https://integrate.api.nvidia.com/v1\")
ZAI_BASE_URL = os.environ.get(\"ZAI_BASE_URL\", \"https://api.z.ai/api/paas/v4\")
'''
code = code.replace('DEFAULT_CONFIG = {', extra_vars + '\nDEFAULT_CONFIG = {')
code = code.replace(
    '\"config\": {\"api_key\": OPENAI_API_KEY, \"temperature\": 0.2, \"model\": DEFAULT_LLM_MODEL},',
    '\"config\": {\"api_key\": ZAI_API_KEY, \"temperature\": 0.1, \"model\": \"glm-4.7-flash\", \"openai_base_url\": ZAI_BASE_URL, \"max_tokens\": 2000},'
)
code = code.replace(
    '\"embedder\": {\"provider\": \"openai\", \"config\": {\"api_key\": OPENAI_API_KEY, \"model\": DEFAULT_EMBEDDER_MODEL}},',
    '\"embedder\": {\"provider\": \"openai\", \"config\": {\"api_key\": NIM_API_KEY, \"model\": \"nvidia/nemotron-3-embed-1b\", \"openai_base_url\": NIM_BASE_URL}},'
)
with open('/app/main.py', 'w') as f:
    f.write(code)
"
```

**HF Space Secrets needed for this approach**: `NIM_API_KEY`, `ZAI_API_KEY` (and optionally `NIM_BASE_URL`, `ZAI_BASE_URL` if non-default). For auth, choose ONE mode:
- **AUTH_DISABLED mode** (insecure, public Space only): Leave `AUTH_DISABLED=true` in `entrypoint.sh`, do NOT set `JWT_SECRET` or `ADMIN_API_KEY`. All endpoints open.
- **ADMIN_API_KEY mode** (recommended for public Space): Set `ADMIN_API_KEY=<secret>` as HF Secret, remove `AUTH_DISABLED=true` from `entrypoint.sh`. Do NOT set `JWT_SECRET`. Callers send `X-API-Key` header. See `references/mem0-server-auth.md` § Switching.

**Tradeoff**: Unlike `/configure`, the `DEFAULT_CONFIG` patch is NOT stored in Neon `settings` table — it's applied on every container start. But since `start.sh` runs on every boot, this is functionally equivalent and simpler (no JWT auth needed, no one-time API call).

## HF Private Space Auth Requirement

**HF private Spaces require `Authorization: Bearer <HF_TOKEN>` for ALL HTTP access** — including GET requests to `/health`, `/docs`, `/openapi.json`. Without the header, HF's proxy returns its own 404 HTML page (not FastAPI JSON). This is NOT mem0 auth — it's HF Space access control.

When testing endpoints with `curl`, always pass `-H "Authorization: Bearer <token>"` to get through HF's proxy first. Callers like hermes `SelfHostedBackend` already send `X-API-Key` header; for external agents, they need the HF Token as well (or make the Space public).

## mem0 Server Complete Fix Chain (Order Matters)

When deploying mem0 server to HF Space + Neon pgvector with NIM embedder + 智谱 LLM, apply fixes in this order. Each fix addresses a distinct failure mode; skipping one resurfaces as a misleading `unknown` (Upstream provider error) or `datastore_unavailable`.

| Step | Fix | Symptom it resolves | Where |
|---|---|---|---|
| 1 | `POSTGRES_DB=neondb` HF Secret | `InsufficientPrivilege: permission denied for schema public` — pgvector connects to default `postgres` database, GRANT on `neondb` doesn't apply | HF Space Settings → New Secret |
| 2 | `GRANT CREATE ON SCHEMA public TO <user>` on the `neondb` database | Same as above — PG15+ revokes CREATE on public schema by default | Neon SQL Editor (on `neondb` db) |
| 3 | patch 10: `embedding_model_dims: 2048` in vector_store config | `DataException: expected 1536 dimensions, not 2048` — NIM embedder outputs 2048, pgvector defaults 1536 | `nworker/patches/10_default_config.py` |
| 4 | patch 10: `hnsw: False` in vector_store config | `ProgramLimitExceeded: column cannot have more than 2000 dimensions for hnsw index` — HNSW hard limit 2000 dims | Same patch 10 |
| 5 | patch 20: `check=ConnectionPool.check_connection` | `OperationalError: the connection is lost` — Neon scale-to-zero severs pooled connections, pool serves stale conns | `nworker/patches/20_pgvector_ext.py` |
| 6 | patch 30: `DROP TABLE IF EXISTS memories CASCADE` | Old 1536-dim table persists, `CREATE TABLE IF NOT EXISTS` won't rebuild with new dims | `nworker/patches/30_clear_db_overrides.py` |
| 7 | patch 30: `DELETE FROM settings WHERE key='config_overrides'` | Stale DB overrides strip `openai_base_url` (Issue #4910) → embedder hits api.openai.com | Same patch 30 |

After all 7 fixes: POST /memories → 200 with memory ID, POST /search → results with cosine score, GET /memories → stored memories. See `references/mem0-server-config.md` § "Final Verification" for test commands.

## Private Repo as Second Brain (User Iron Rule)

The user has three iron rules for this deployment class — encode them in your workflow:

1. **GitHub repo = second brain**: Save ALL state to `docs/STATUS.md` + `docs/SECRETS.md` + `nworker/` (patches, entrypoint, run.py) + `mcp/` (MCP server scripts) + `scripts/` (cronjob poller scripts). Next session's agent clones the repo and reads STATUS.md to resume. This is MORE reliable than mem0 memory (which compresses) or ephemeral files (which delete on restart). When the user says "保存所有" or "代码、脚本也保存到私库" (save everything / save scripts to the private repo too), sync ALL artifacts — including MCP server scripts, cronjob scripts, any helper code — to the GitHub repo and push immediately. **Do not leave scripts only in `~/.hermes/` without copying them to the repo** — the repo is the durable copy, `~/.hermes/` is the runtime copy.
2. **Three files never change**: `Dockerfile`, `README.md`, `start.sh` are frozen after initial deployment. All logic changes go through `nworker/patches/` (HF Dataset, hot-reloaded on restart) — never touch the three files. Frequent Dockerfile changes trigger HF rebuild → ban risk.
3. **Research before acting**: When facing an unfamiliar error or API behavior, use anysearch to read the official source/docs FIRST — do not trial-and-error. The user explicitly said "别总是试错啊" (stop trial-and-error) and "有问题先ask搜官方源码不试错". Understand the code path, then fix. Read `auth.py`, `_backend.py`, `main.py` source to understand exact behavior before proposing solutions.

## Hermes Persistence Architecture (Bucket Backup/Restore Model)

The hermes Space survives restarts via a Bucket-based backup/restore system: `home_files_uploader.py` + `restore_home_files.py` form a pair for individual config files (`.env`, `SOUL.md`, `config.yaml`, etc.), while `state_db_uploader.py` + `restore_state.py` handle SQLite state.db. Config files are generated from templates (`config.yaml.template`, `mem0.json.template`) when missing. Known gap: `mem0.json` is NOT in the backup/restore `_FILES` lists, and `mem0.json.template` only supports oss mode (not self_hosted) — so self_hosted mem0 config is lost on every restart. See `references/hermes-persistence-architecture.md` for the full boot flow, file lists, env vars, and the **env-var-only fix** for `mem0.json` persistence (verified: set `MEM0_HOST` + `MEM0_API_KEY` as HF Secrets, no file persistence needed). See `references/hf-bucket-vs-dataset.md` for the full-dimension Bucket-vs-Dataset comparison (2026-08 official docs查证), Bucket Python API (`create_bucket`, `set_space_volumes`), volume mount config, HF persistent storage deprecation note, and nexus 7-dimension persistence audit.

## Overlap Note

Complements bundled `github-auth` and `github-repo-management`. Those cover general GitHub operations; this covers the GitHub-to-HF-Space deployment pattern with ephemeral-safe CI/CD. Also complements `mem0-backend-troubleshooting` which covers the hermes-side mem0 OSS plugin config; this covers the server-side HF Space deployment.
