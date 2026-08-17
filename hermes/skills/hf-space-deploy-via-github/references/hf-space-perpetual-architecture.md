# HF Space 永续架构 (Perpetual Architecture) — n-omn/nexus 血统

Reference architecture distilled from user's production repos `i3t2y/n-omn` (OmniRoute node) and `i3t2y/nexus` (Hermes Agent space). Use when hardening a Space for long-term unattended operation.

**Principle: Adapt, don't copy.** When studying reference repos, extract the *principles* (three-layer decoupling, race fix, token redaction, HEALTHCHECK) and adapt them to your target stack. n-omn is Node.js multi-process with litestream+R2 and trap/SIGTERM; mem0 is Python single-process uvicorn with no litestream. Copying n-omn's entrypoint.sh trap/litestream/restore logic into a Python uvicorn Space would add complexity with zero benefit. Match the patterns to the runtime, not the other way around.

## Three-Layer Decoupling

```
┌─────────────────────────────────────────────────────┐
│ 环境层 (GHCR base or Dockerfile)  — 极低频, 触 Rebuild  │
│   Dockerfile + start.sh + README.md (冻结不改)        │
├─────────────────────────────────────────────────────┤
│ 逻辑层 (HF Dataset/Bucket)       — 高频, 零 Rebuild    │
│   entrypoint.sh + run.py + patches/ (随意改)          │
├─────────────────────────────────────────────────────┤
│ 运行态 (Neon/R2/Supabase)        — 持续                │
│   DB tables, vector memory (外部持久)                  │
└─────────────────────────────────────────────────────┘
```

Key rule: **环境层变 = HF Rebuild (风控风险); 逻辑层变 = Restart 即效 (零风险).**

## start.sh → entrypoint.sh Delegation

start.sh is a **thin bootstrap** that does only 3 things, then `exec` hands off to the Dataset's entrypoint.sh:

1. **环境自愈** — check for missing tools (python3, curl, pip3), apt install if absent
2. **拉取逻辑层** — pull Dataset, with race-condition fix (see below)
3. **exec entrypoint.sh** — hand off control, all real logic lives in Dataset

```bash
#!/bin/sh
set -e
# 1. 环境自愈
_need_install=0
for t in python3 curl pip3; do
  command -v "$t" >/dev/null 2>&1 || { _need_install=1; break; }
done
if [ "$_need_install" = "1" ]; then
  apt-get update -qq && apt-get install -y --no-install-recommends \
    curl python3 python3-pip ca-certificates && rm -rf /var/lib/apt/lists/*
  pip3 install --no-cache-dir --break-system-packages "huggingface_hub>=1.0,<2.0"
fi

# 2. 拉取 (race-fix below)
# 3. exec
chmod +x /app/worker/entrypoint.sh 2>/dev/null || true
exec /app/worker/entrypoint.sh
```

## Race-Condition Fix (HEAD commit_id lock)

**Problem**: `snapshot_download` / `hf download` defaults to `main` HEAD. If a Dataset push is in-flight during boot, the container can pull a stale/intermediate version (n-omn boot#4 pulled files 18 min older than the sync that triggered the restart).

**Fix**: Before pulling, fetch the Dataset's current HEAD commit_id, then pull by that specific revision. This locks an atomic point-in-time snapshot of all files.

```bash
_rev=$(python3 -c '
import os
try:
    from huggingface_hub import HfApi
    api = HfApi(token=os.environ.get("HF_TOKEN") or None)
    cid = next(iter(api.list_repo_commits("nmem/nworker", repo_type="dataset"))).commit_id
    print(cid)
except Exception:
    pass  # fail-open, fall back to main HEAD
' 2>/tmp/.rev.err) || true

python3 -c "
from huggingface_hub import snapshot_download
snapshot_download('nmem/nworker', repo_type='dataset', local_dir='/app/worker',
    revision='$_rev' if '$_rev' else None,
    token=os.environ.get('HF_TOKEN') or None)
"
```

## HF Token Redaction in stderr

Stderr from `hf download` / `snapshot_download` may contain the HF_TOKEN in error messages. Always redact before forwarding to container logs:

```bash
_err=/tmp/.dl.err; : > "$_err"
# ... download command ... 2>"$_err" || {
  if [ -n "$HF_TOKEN" ]; then
    sed "s/$HF_TOKEN/[REDACTED]/g" "$_err" >&2
  else
    cat "$_err" >&2
  fi
# }
```

**Important**: Use the `2>file; then sed` pattern, NOT `2>&1 | sed`. The pipe approach makes `$?` reflect sed's exit code, not the download's — masking download failures (POSIX sh has no PIPESTATUS).

## HEALTHCHECK in Dockerfile

```dockerfile
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -sf http://127.0.0.1:7860/health || exit 1
```

`start-period=120s` gives time for entrypoint.sh to install deps, run patches, alembic migrate, and start uvicorn before healthcheck kicks in.

## entrypoint.sh — Pattern for Python/uvicorn Services

Unlike n-omn (Node.js multi-process with trap/SIGTERM/litestream), Python single-process uvicorn is much simpler:

```bash
#!/bin/bash
set -e
# Phase 1: worker deps
[ -f /app/worker/requirements.txt ] && pip install -r /app/worker/requirements.txt || true
# Phase 2: runtime patches (via run.py orchestrator)
[ -f /app/worker/run.py ] && python3 /app/worker/run.py || true
# Phase 3: launch (AUTH_DISABLED injected, not as HF Secret)
cd /app
exec env AUTH_DISABLED=true uvicorn main:app --host 0.0.0.0 --port 7860
```

## patches/ Directory Pattern

Runtime patches (for pip-installed packages, DB overrides) stored in Dataset, executed by run.py:

```
patches/
├── 10_default_config.py    # patch DEFAULT_CONFIG (embedder→NIM, LLM→Zhipu)
├── 20_pgvector_ext.py      # neutralize CREATE EXTENSION + add ConnectionPool check= callback
├── 30_clear_db_overrides.py # DELETE settings WHERE key='config_overrides'
└── 40_health_worker.py     # inject /health + mount LangGraph router
```

run.py orchestrates execution order (pre-alembic vs post-alembic):
- Patches that don't need DB tables → pre-alembic
- Patches that need `settings` table → post-alembic (after `alembic upgrade head`)

### Patch 20 — Two fixes in one file

Patch 20 (`20_pgvector_ext.py`) addresses two independent Neon compatibility issues:

1. **CREATE EXTENSION → pass**: Neon `neondb_owner` inherits `neon_superuser` but is NOT a true Postgres superuser. `CREATE EXTENSION` requires superuser even if the extension already exists. pgvector is pre-installed via Neon Console.

2. **ConnectionPool `check=` callback**: Neon scale-to-zero severs pooled connections silently. psycopg3's `ConnectionPool` has no health check by default — dead connections are handed out and fail with `OperationalError: the connection is lost` on first use. The patch adds `check=self._check_conn` to the pool creation, which runs `SELECT 1` before each checkout and discards dead connections.

**Pattern for writing patches**: Always test the patch against the real source file before deploying. Copy the target file to `/tmp`, dry-run the replacement, verify with `py_compile`, THEN upload to Dataset. This catches string-mismatch failures silently (the patch's `str.replace` returns the original string unchanged if the exact pattern isn't found).

## Active-Process Pattern (Self-Heal Loop)

nexus hermes real-start.sh wraps the main process in a `while true` loop — if the boot process exits (any daemon thread dies), it re-checks mount, waits 5s, then restarts:

```bash
while true; do
  python -c "from app.main import boot; boot()"
  echo "[real-start] boot exited code=$?, restart in 5s..."
  [ -f "$APP_DIR/app/main.py" ] || bootstrap_from_bucket || true
  sleep 5
done
```

For mem0/uvicorn (single process, `exec` replaces PID 1), this is unnecessary — HF's own supervisor restarts the container if uvicorn exits.

## Keepalive (Supabase/Neon + Downstream Spaces)

nexus keepalive.py pattern: daemon thread that pings downstream `/health` endpoints at randomized intervals (base 600s ± 180s jitter, avoiding fixed-period observability). Also writes a row to `space_health` table on each ping — the write itself keeps Supabase's free tier from pausing for inactivity.

For mem0 + Neon: use cron-job.org → `/health` every 4 min (simpler, no daemon needed). See `references/neon-cronjob-keepalive.md`.

## Source Repos

- `i3t2y/n-omn` — OmniRoute perpetual node (Node.js, litestream+R2, multi-process entrypoint with trap/SIGTERM)
- `i3t2y/nexus` — Hermes Agent space + triad (hermes + memgraph + langgraph); `spaces/memgraph/` contains mem0 server's three files + nworker logic layer, version-controlled alongside hermes scripts
- `i3t2y/n-memgraph` — mem0 server deployment (public, original standalone repo; now merged into nexus `spaces/memgraph/` for unified version control)
