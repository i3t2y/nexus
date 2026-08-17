# Hermes Boot Persistence: real-start.sh Mechanism

How `real-start.sh` restores/persists config across HF Space restarts, and why `mem0.json` changes get lost. Use this when mem0 config reverts to old values after a Space restart.

## Boot Sequence (real-start.sh)

```
HF Space container start
  → /data/scripts/start.sh (thin, baked in image, never changes)
    → bucket mount: hf://buckets/<owner>/nexus-logic → /data/
    → source /data/scripts/real-start.sh
      1. Restore home files from Bucket:
         .env, SOUL.md, MEMORY.md, USER.md, config.yaml, plugins/, skills/
         (NOT mem0.json — it is NOT in the restore list)
      2. Restore state.db from Bucket (SQLite, ~4MB)
      3. Start daemons: persist (state.db→Bucket), home-files (→Bucket), keepalive
      4. Generate mem0.json from template IF NOT EXIST ("缺才生成")
      5. Launch gateway + dashboard
```

## The "缺才生成" (generate-if-missing) Pattern (L152-175)

```bash
if [ "${MEM0_MODE:-}" = "oss" ] && [ -f "$APP_DIR/scripts/mem0.json.template" ]; then
  if [ -n "${FORCE_TEMPLATE_APPLY:-}" ] || [ ! -f "$HERMES_HOME/mem0.json" ]; then
    # python envsubst: ${VAR} → os.environ[VAR]
    # generates mem0.json from mem0.json.template
    echo "[real-start] mem0.json generated (oss mode, env-substituted) ok=${_mem0_env_ok}"
  else
    echo "[real-start] mem0.json retained (existing; FORCE_TEMPLATE_APPLY=1 force regenerate)"
  fi
fi
```

Key behaviors:
- **Gate**: `MEM0_MODE=oss` must be set. If unset → skip (mem0 plugin dormant).
- **Exists check**: If `mem0.json` already exists → retained, NOT overwritten.
- **Force**: `FORCE_TEMPLATE_APPLY=1` → regenerate even if exists.
- **Template**: `/data/scripts/mem0.json.template` — hardcoded `mode: oss` + Supabase pgvector + 智谱 LLM + NIM embedder.

## Why self_hosted mem0.json Gets Lost on Restart

1. `/opt/data` is ephemeral — HF Space restart wipes it.
2. We manually edit `/opt/data/.hermes/mem0.json` → `mode: self_hosted` + `host: https://nmem-memgraph.hf.space`.
3. On restart: `/opt/data` wiped → `mem0.json` gone.
4. `real-start.sh` step 1 restores home files from Bucket, but **mem0.json is NOT in the restore list** (only .env, SOUL.md, MEMORY.md, USER.md, config.yaml, plugins/, skills/).
5. `real-start.sh` step 4: `mem0.json` doesn't exist → generates from template → `mode: oss` + Supabase.
6. Result: config reverts to `oss` + Supabase. Our `self_hosted` + HF Space config is lost.

## The Persistence Gap

| File | In Bucket restore? | In home-files daemon? | In template? | Survives restart? |
|------|-------------------|---------------------|--------------|-------------------|
| .env | ✅ | ✅ | — | ✅ |
| config.yaml | ✅ | ✅ | config.yaml.template | ✅ |
| MEMORY.md | ✅ | ✅ | — | ✅ |
| **mem0.json** | ❌ NOT in restore list | ❌ NOT in upload list | mem0.json.template (oss only) | ❌ Regenerated from template → oss |

The `home-files-uploader.py` daemon uploads a **fixed list** of files to Bucket. `mem0.json` is NOT on that list, so even if you edit it, no daemon backs it up.

## Confirmed: Both _FILES Lists Exclude mem0.json (2026-08-16 session)

The exact file lists were read from source:

**`restore_home_files.py`** (`/data/scripts/restore_home_files.py`):
```python
_FILES = [
    ".env",
    "SOUL.md",
    "memories/MEMORY.md",
    "memories/USER.md",
    "config.yaml",
    ".no-bundled-skills",
]
```

**`home_files_uploader.py`** (`/data/scripts/home_files_uploader.py`):
```python
_FILES = [
    "config.yaml",
    ".env",
    "SOUL.md",
    "memories/MEMORY.md",
    "memories/USER.md",
    ".no-bundled-skills",
]
```

Both lists are identical (just different order). Neither includes `mem0.json`. The uploader also handles `plugins/` and `skills/` directories via `hf buckets sync` (separate from the `_FILES` list), but `mem0.json` is not a directory — it's a single file that falls through the gap.

## Community Approaches: Whole-Directory Sync vs Per-File List (2026-08-16 research)

Three open-source projects deploy hermes on HF Space. All use **whole-directory sync** — no per-file maintenance:

| Project | Method | What syncs | Frequency |
|---------|--------|-----------|-----------|
| **HermesFace** (democra-ai, ⭐22) | `snapshot_download` → `/opt/data` + `upload_folder` ← `/opt/data` | Entire `/opt/data` directory | 60s periodic + on shutdown |
| **HuggingMes** (somratpro) | Same HF Dataset sync | Entire workspace; `.env` opt-in via `SYNC_INCLUDE_ENV=1` | 600s default |
| **radarcoding/hermes-agent** | HuggingMes fork | Same | Same |

**HermesFace's core sync logic** (`scripts/sync_hf.py`):
```python
# Startup: pull entire /opt/data from HF Dataset
snapshot_download(repo_id=HF_REPO_ID, repo_type="dataset", allow_patterns="hermes_data/**", local_dir=tmpdir)
# Copy everything back to /opt/data
for item in downloaded_root.rglob("*"):
    if item.is_file():
        shutil.copy2(str(item), str(dest))

# Periodic: push entire /opt/data to HF Dataset
# (upload_folder call every SYNC_INTERVAL seconds)
```

**Key insight**: HermesFace/HuggingMes don't maintain any `_FILES` list. Everything under `/opt/data` is synced as a unit — including `mem0.json`, `.env`, `config.yaml`, `MEMORY.md`, and any future config files. No file can "fall through the gap" because there is no gap — the entire directory is the unit of persistence.

**nexus architecture** (current hermes) uses the opposite strategy: a curated per-file list (`_FILES`) that must be manually maintained. Every new config file requires editing two Python scripts. `mem0.json` is the file that was missed.

### Why nexus chose per-file over whole-directory (confirmed from ARCHITECTURE.md)

Read directly from `i3t2y/nexus` repo `docs/ARCHITECTURE.md` (2026-08-16 session). Nexus uses a **fundamentally different storage architecture** than HermesFace/HuggingMes:

| Dimension | nexus (cg52) | HermesFace |
|-----------|-------------|------------|
| Architecture | 4 Space (hermes/langgraph/claude/codex) + R2 + Supabase + Bucket + GHCR | 1 Space running native Hermes Agent |
| Config storage | **Supabase tables** (structured, queryable) — config is data, not files | **Files** — mem0.json, config.yaml |
| State storage | Supabase tables + R2 blob (SQLite not primary) | **state.db** (SQLite) — the primary |
| Large files | R2 (checkpoint, skills, vectors) | HF Dataset (everything together) |
| Bucket usage | rw mount `/data` for logic layer (app/scripts/libs); selective backup for home files | Not used — HF Dataset replaces it |
| Dataset usage | "仅兜底" (fallback only, not primary) | **Primary** persistence mechanism |

**The per-file `_FILES` list works for nexus because nexus doesn't rely on config files** — config lives in Supabase tables. The `_FILES` list only needs `.env`, `config.yaml`, `MEMORY.md`, etc. — a small fixed set. mem0.json was never in scope because nexus's hermes was a custom shell (换装), not a native Hermes Agent reading mem0.json for memory config.

**The trade-off is real but asymmetric**: Per-file sync is cleaner (no junk in backup) but every new config file requires editing two Python scripts. Whole-directory sync is robust (nothing missed) but syncs transient files. The gap only matters when the config file is NOT part of the original design — which is exactly what happened when the user switched mem0 from `oss` to `self_hosted`.

### User's strategic conclusion (2026-08-16 session)

After reviewing nexus's full architecture, the user concluded: **nexus方案已过时** — the multi-Space + R2 + Supabase + Bucket + GHCR design was for a 4-Space orchestration system, but the actual deployment is 2 Spaces (hermes on sonoke + mem0/LangGraph on nmem/memgraph) with Neon. None of nexus's core components are in use. The only "legacy" is hermes's built-in `restore_home_files.py` / `home_files_uploader.py` — but those are part of the hermes Docker image, not nexus additions. The mem0.json persistence gap is a hermes-native issue, not a nexus issue.

## Fix Options (confirmed options, not yet implemented)

### Option A (minimal): Add mem0.json to both _FILES lists
Add `"mem0.json"` to the `_FILES` list in both `restore_home_files.py` and `home_files_uploader.py`. This makes `mem0.json` persist like `.env` and `config.yaml` via the existing Bucket sync.

**Pros**: One-line change in two files. Uses existing infrastructure.
**Cons**: Modifies hermes infrastructure scripts — may conflict on hermes upgrade. Any future config file has the same problem.

### Option B (template): Update mem0.json.template to support self_hosted
Change the template from hardcoded `mode: oss` to read `${MEM0_MODE}`, `${MEM0_HOST}`, `${MEM0_API_KEY}` placeholders. Set HF Secrets `MEM0_MODE=self_hosted`, `MEM0_HOST=https://nmem-memgraph.hf.space`, `MEM0_API_KEY=<key>`. The `real-start.sh` envsubst already handles `${VAR}` substitution.

**Pros**: No change to uploader/restore scripts. Template + Secrets = correct config on every boot. This is the community-recommended pattern ( configs go in Secrets + template generation, not in persistent files).
**Cons**: Need to modify `mem0.json.template` (in Bucket `scripts/`). Need `MEM0_MODE=self_hosted` as a Secret, but `real-start.sh` L152 gates on `MEM0_MODE=oss` — the gate logic needs a minor update to accept `self_hosted` too.

### Option C (community approach): Switch to whole-directory sync
Replace the per-file `_FILES` list approach with HermesFace-style whole-`/opt/data` sync to a HF Dataset. This eliminates the gap entirely.

**Pros**: No file ever missed. No `_FILES` maintenance. Simpler mental model.
**Cons**: Major architectural change. Uploads transient files (logs, caches). Conflicts with nexus's ext4-vs-FUSE SQLite corruption fix rationale. Would need to replace 4 Python scripts with 1.

### Option D (env-var via .env): self_hosted config via .env restore
`mem0.json.template` uses `${VAR}` placeholders. Set `MEM0_MODE=self_hosted`, `MEM0_HOST=https://nmem-memgraph.hf.space`, `MEM0_API_KEY=...` as HF Secrets. The `.env` file (which IS in the restore list) could carry these, but `real-start.sh` reads HF Secrets as env vars before `.env` is restored — so HF Secrets directly setting the env vars would work without `.env` involvement.

**Pros**: No script changes. `.env` restore already works. HF Secrets are the source of truth.
**Cons**: Template must be updated to support `self_hosted` placeholders. The `MEM0_MODE=oss` gate in `real-start.sh` L152 must be widened.

## ✅ RESOLVED: Env-Var-Only Fix (2026-08-16 session breakthrough)

**The fix requires NO script changes, NO template changes, NO _FILES list changes.**

### Source code evidence

`plugins/memory/mem0/__init__.py` `_load_config()` (L78-104):

```python
def _load_config() -> dict:
    """Load config from env vars, with $HERMES_HOME/mem0.json overrides."""
    config = {
        "mode": os.environ.get("MEM0_MODE", "platform"),    # ← env var base layer
        "api_key": get_secret("MEM0_API_KEY", ""),          # ← env var
        "host": os.environ.get("MEM0_HOST", ""),            # ← env var
        "oss": {},
    }
    config_path = get_hermes_home() / "mem0.json"
    if config_path.exists():
        file_cfg = json.loads(config_path.read_text(encoding="utf-8"))
        config.update(...)  # mem0.json overrides env vars ONLY if it exists
    return config
```

`_create_backend()` (L280-288):

```python
if self._mode == "oss":
    return OSSBackend(self._config.get("oss", {}))
if self._host:                                    # ← mode != oss + host set
    return SelfHostedBackend(self._api_key, self._host)  # ← THIS IS THE PATH
return PlatformBackend(self._api_key)
```

### The insight

- `mem0.json` is an **override layer**, not the only source. Env vars are the **base layer**.
- On restart: `mem0.json` is not in `_FILES` → not restored → doesn't exist → hermes reads env vars only.
- `real-start.sh` L152: gate is `if MEM0_MODE == "oss"` — if `MEM0_MODE` is unset (default "platform"), the template generation is **skipped entirely** → no mem0.json generated → hermes falls back to env vars.
- `MEM0_HOST` set in env → `_load_config()` returns `host` → `_create_backend()` takes the `self._host` branch → `SelfHostedBackend(api_key, host)` → connects to HF Space mem0 server.

### What to set in HF Secrets (sonoke/hermes Space)

| Secret | Value | Already exists? |
|--------|-------|-----------------|
| `MEM0_HOST` | `https://nmem-memgraph.hf.space` | ❌ Add this |
| `MEM0_API_KEY` | `<ADMIN_API_KEY value>` | ✅ Already in .env |
| `MEM0_MODE` | **Don't set** (default "platform" works — the `_host` check is independent of mode) | — |

**Do NOT set `MEM0_MODE=oss`** — that triggers the template generation gate and creates a mem0.json with oss config, which then overrides env vars. Leave `MEM0_MODE` unset (defaults to "platform") so `real-start.sh` skips template generation, `mem0.json` never exists, and hermes reads `MEM0_HOST` + `MEM0_API_KEY` from env → `SelfHostedBackend`.

### Restart flow with this fix

```
1. HF Secrets inject MEM0_HOST + MEM0_API_KEY into environment
2. real-start.sh: MEM0_MODE unset (default "platform") != "oss" → "mem0.json skip"
3. mem0.json NOT generated (template gate requires MEM0_MODE=oss)
4. restore_home_files.py: mem0.json not in _FILES → not restored → doesn't exist
5. hermes starts → mem0 plugin _load_config():
   - mode = env "MEM0_MODE" → "platform" (default)
   - api_key = env "MEM0_API_KEY" → "gZixCw..."
   - host = env "MEM0_HOST" → "https://nmem-memgraph.hf.space"
6. _create_backend(): mode != oss, host is set → SelfHostedBackend(api_key, host)
7. ✅ Connects to mem0 server on nmem/memgraph HF Space
```

### Why this is better than all other options

| Option | Script changes | Template changes | Secrets only? | Works on upgrade |
|--------|---------------|-----------------|---------------|-----------------|
| A: Add to _FILES | 2 Python scripts | No | No | ❌ Overwritten |
| B: Update template | No (but gate logic change) | Yes | Yes | ❌ Template overwritten |
| C: Whole-dir sync | 4 scripts → 1 | No | No | ❌ Major refactor |
| **D: Env-var only** | **None** | **None** | **Yes** | **✅ Survives upgrades** |

Option D (env-var only) is the clear winner — it works **with** hermes's existing design instead of against it. The `_load_config()` function was explicitly designed to read env vars as the base layer. HF Secrets inject env vars. The two mechanisms compose perfectly with zero modification.

### Verification (after applying HF Secrets)

```bash
# After restart, check that mem0.json was NOT generated
ls -la /opt/data/.hermes/mem0.json 2>/dev/null
# Expected: No such file (template gate skipped because MEM0_MODE != oss)

# Check boot log
grep "mem0.json" /opt/data/logs/*.log 2>/dev/null | tail -3
# Expected: "mem0.json skip (MEM0_MODE!=oss, mem0 plugin dormant)"
# NOTE: The "dormant" message is misleading — mem0 IS active via env vars, just not via template

# Verify hermes sees the env vars
python3 -c "
import os
print('MEM0_HOST:', os.environ.get('MEM0_HOST', 'NOT SET'))
print('MEM0_API_KEY:', 'SET' if os.environ.get('MEM0_API_KEY') else 'NOT SET')
print('MEM0_MODE:', os.environ.get('MEM0_MODE', 'NOT SET (default=platform)'))
"
# Expected: MEM0_HOST set, MEM0_API_KEY set, MEM0_MODE not set

# Verify mem0 server is reachable
curl -s https://nmem-memgraph.hf.space/health
# Expected: {"status":"ok","db":"connected"}
```

## Related Files

- `/data/scripts/real-start.sh` — boot logic (L134-175 for mem0.json)
- `/data/scripts/mem0.json.template` — template (hardcoded oss + Supabase)
- `/data/scripts/home_files_uploader.py` — home-files daemon (fixed file list)
- `/data/scripts/config.yaml.template` — config.yaml generation template
- `/opt/data/.hermes/mem0.json` — generated mem0 config (ephemeral)

## Diagnostic: Check if mem0 config reverted

```bash
cat /opt/data/.hermes/mem0.json | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('mode'))"
# If "oss" → config reverted to template default
# If "self_hosted" → config survived (or was manually re-applied)
```

Check boot log for which path was taken:
```bash
grep "mem0.json" /opt/data/logs/*.log 2>/dev/null | tail -5
# "generated (oss mode)" → regenerated from template (config lost)
# "retained (existing)" → kept existing mem0.json (config survived)
```
