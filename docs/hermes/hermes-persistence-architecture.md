# Hermes Persistence Architecture (Bucket Backup/Restore Model)

Session-specific reference for analyzing the hermes Space's internal persistence mechanism — specifically how config files survive (or don't survive) Space restarts.

## Overview: Four-Layer Separation

The hermes Space (running on HF Space) uses a four-layer decoupling pattern to survive restarts without triggering Docker rebuilds:

| Layer | Where stored | Changed by | Rebuild trigger? |
|---|---|---|---|
| 镜像层 (Image) | GHCR `ghcr.io/<owner>/nexus-base:stable` | Local build + push to GHCR | Yes (only on base image upgrade) |
| 环境层 (Environment) | HF Space git (Dockerfile, README.md, start.sh) | User manual git push to HF | Yes (one-time, freezes after first push) |
| 逻辑层 (Logic) | HF Storage Bucket `rw /data` mount | `sync-logic-bucket.sh` + Settings → Restart | **No** (only restart, no rebuild) |
| 配置层 (Config) | HF Secrets | HF UI / API | **No** (only restart) |

## Bucket Mount & Boot Flow

```
Space Start
  → start.sh (thin, in-image, never changes)
    → wait for /data mount (Bucket rw)
    → bootstrap_from_bucket(): hf buckets sync → /data/
      → /data/app/         (logic layer: main.py etc.)
      → /data/scripts/    (scripts: real-start.sh, config.yaml.template, etc.)
      → /data/libs/        (shared libraries)
      → /data/.hermes/    (runtime state: home-backups/, state-backups/)
    → source real-start.sh (from Bucket)
      → restore_home_files.py: pull home-backups/ → /opt/data/.hermes/
      → restore_state.py: pull state-backups/state.db → /opt/data/.hermes/
      → generate config.yaml from template (if missing)
      → generate mem0.json from template (if missing, oss mode only)
      → start home_files_uploader.py (daemon, 600s interval)
      → start state_db_uploader.py (daemon, 300s interval)
      → exec hermes (gateway + dashboard)
```

## Home Files Backup/Restore Pair

The core persistence mechanism for individual hermes config files. Two scripts form a pair:

### restore_home_files.py (boot-time restore)
- Reads from: `hf://buckets/<owner>/nexus-logic/home-backups/`
- Writes to: `/opt/data/.hermes/` (HERMES_HOME, local disk wiped on restart)
- Files restored (`_FILES` list):
  - `.env` (channel credentials written by dashboard)
  - `SOUL.md` (persona)
  - `memories/MEMORY.md` (individual memory index)
  - `memories/USER.md` (user profile)
  - `config.yaml` (dashboard settings: provider, parameters, plugins)
  - `.no-bundled-skills` (opt-out marker for bundled skill seeding)
- Directories restored via `hf buckets sync`:
  - `plugins/` (user-installed plugin code; excludes nexus-r2, nexus-ops builtins)
  - `skills/` (user-installed skills; based on `.hub/lock.json`, excludes bundled)

### home_files_uploader.py (periodic upload)
- Reads from: `/opt/data/.hermes/`
- Writes to: `hf://buckets/<owner>/nexus-logic/home-backups/`
- Interval: `HOME_FILES_UPLOAD_INTERVAL` (default 600s)
- Files uploaded (`_FILES` list): **same 6 files as restore**
- Directories: `plugins/` + `skills/` (same excludes)
- Incremental: compares mtime+size vs last-upload state file, skips unchanged

### state.db Backup/Restore Pair (parallel mechanism)
- `restore_state.py` / `state_db_uploader.py`
- SQLite state.db (4MB): agent states, task logs, long memory, skills index
- state_db_uploader: WAL checkpoint → sqlite3 backup API (consistent snapshot) → push to `state-backups/`
- Interval: 300s (more frequent than home files — state changes more often)

## The mem0.json Gap (Known Issue)

### Problem
`mem0.json` is NOT in the `_FILES` list of either `home_files_uploader.py` or `restore_home_files.py`. It has its own generation path in `real-start.sh`:

```bash
# real-start.sh L152-175
if [ "${MEM0_MODE:-}" = "oss" ] && [ -f "$APP_DIR/scripts/mem0.json.template" ]; then
  if [ -n "${FORCE_TEMPLATE_APPLY:-}" ] || [ ! -f "$HERMES_HOME/mem0.json" ]; then
    # Generate from template (envsubst: ${VAR} → os.environ[VAR])
    python3 -c '...' "$APP_DIR/scripts/mem0.json.template" "$HERMES_HOME/mem0.json"
    # → "mem0.json generated (oss mode, env-substituted)"
  else
    # → "mem0.json retained (existing)"
  fi
elif [ "${MEM0_MODE:-}" != "oss" ]; then
  # → "mem0.json skip (MEM0_MODE!=oss)"
fi
```

### Two gaps:
1. **Template hardcodes oss mode**: `mem0.json.template` only supports `MEM0_MODE=oss` (Supabase pgvector). There is no `self_hosted` branch. Setting `MEM0_MODE=self_hosted` causes the script to skip generation entirely — `mem0.json` remains absent.
2. **Not in backup/restore `_FILES`**: Even if `mem0.json` is manually created (e.g., by user configuring self_hosted mode via dashboard), neither `home_files_uploader.py` nor `restore_home_files.py` backs it up. On restart:
   - `/opt/data` wipes → `mem0.json` deleted
   - `restore_home_files.py` doesn't restore it (not in `_FILES`)
   - `real-start.sh` detects missing `mem0.json` → generates from template → **oss + Supabase** (the configured self_hosted → HF Space is lost)

### Fix: Env-Var-Only (RECOMMENDED — verified 2026-08-17)

**No file persistence needed at all.** Set `MEM0_HOST` + `MEM0_API_KEY` as HF Secrets. The hermes mem0 plugin `_load_config()` (L78-104 of `/opt/hermes-agent/plugins/memory/mem0/__init__.py`) reads env vars as **base layer**, and `mem0.json` is only an **override layer**:

```python
def _load_config() -> dict:
    config = {
        "mode": os.environ.get("MEM0_MODE", "platform"),  # env = base
        "api_key": get_secret("MEM0_API_KEY", ""),
        "host": os.environ.get("MEM0_HOST", ""),
        "oss": {},
    }
    config_path = get_hermes_home() / "mem0.json"
    if config_path.exists():                               # mem0.json = override
        file_cfg = json.loads(config_path.read_text())
        config.update(file_cfg)  # mem0.json overrides env
    return config
```

Backend selection (`_create_backend`, L283-286):
```python
if self._mode == "oss":         → OSSBackend (needs mem0.json oss config)
if self._host:                  → SelfHostedBackend(api_key, host)  ← THIS PATH
else:                           → PlatformBackend
```

**Key**: `self._host` is checked independent of `self._mode`. Even with `mode=platform` (default), if `host` has a value → `SelfHostedBackend`. So you do NOT need `MEM0_MODE=self_hosted` — just `MEM0_HOST` suffices.

**Boot flow with this fix:**
1. HF Secrets inject `MEM0_HOST` + `MEM0_API_KEY` → env vars
2. `real-start.sh`: `MEM0_MODE` unset (default `platform`) → skip mem0.json generation
3. mem0.json absent (not in _FILES restore) → `_load_config()` reads env vars only
4. `_create_backend()`: `host` has value → `SelfHostedBackend(api_key, host)`
5. ✅ connects to self-hosted mem0 server — no mem0.json file needed

**HF Secrets required (sonoke/h Space):**
- `MEM0_HOST` = `https://nmem-memgraph.hf.space` (mem0 server URL) — 【已废弃 2026-09-05: memgraph 整体下线, 现行为进程内 OSS → Neon, 见 mem0/README.md】
- `MEM0_API_KEY` = worker API key (same value as `ADMIN_API_KEY` on memgraph Space) — 【已废弃 同上】
- Do NOT set `MEM0_MODE` (leave default `platform`)
- Do NOT set `MEM0_OSS_*` vars (not used in this path)

**Why this is better than Options A/B below:**
- Zero script changes (no `_FILES` list, no template modification)
- Zero hermes infrastructure code touched (upgrade-safe)
- Follows HF community pattern: config via Secrets, not file persistence
- mem0.json effectively becomes a cache/override that doesn't need to survive restart

**Note**: The HF_TOKEN in hermes `.env` belongs to a different HF account (e.g., `nmem`) than the Space owner (`sonoke`). Cannot use API to set Secrets on sonoke/h — user must add `MEM0_HOST` manually in sonoke's HF Dashboard.

### Alternative fix options (superseded by env-var-only above):
- **Option A**: Modify `mem0.json.template` to support `self_hosted` mode. Still needs template change + boot generation logic shift.
- **Option B**: Add `mem0.json` to `_FILES` lists. Still needs hermes infrastructure script modification (upgrade conflict risk).

## Key Environment Variables

| Variable | Purpose | Source |
|---|---|---|
| `HF_TOKEN` | Bucket access (read/write) | HF Secret (auto-injected) |
| `HF_OWNER` | Bucket namespace (HF username) | HF Secret |
| `NEXUS_LOGIC_BUCKET` | Bucket name (default `nexus-logic`) | HF Secret |
| `MEM0_MODE` | mem0 plugin mode: `oss` or `self_hosted` | HF Secret / `.env` |
| `MEM0_PG_URI` | Postgres connection string for oss mode | HF Secret |
| `FORCE_TEMPLATE_APPLY` | Force regenerate mem0.json from template | Set `1` to override "retained" logic |
| `FORCE_RESTORE` | Force restore_home_files.py to overwrite existing | Rare, debug only |

## Source Files (for debugging)

All live in `/data/scripts/` (Bucket mount, not `/opt/data/.hermes/scripts/`):

| File | Role |
|---|---|
| `real-start.sh` | Boot orchestration: restore → generate configs → start daemons → exec hermes |
| `start.sh` (thin) | In-image bootstrap: wait for mount → source real-start.sh |
| `restore_home_files.py` | Boot: pull 6 files + plugins/ + skills/ from Bucket |
| `home_files_uploader.py` | Daemon (600s): push same files to Bucket |
| `restore_state.py` | Boot: pull state.db from Bucket |
| `state_db_uploader.py` | Daemon (300s): push state.db to Bucket |
| `config.yaml.template` | Template for config.yaml generation |
| `mem0.json.template` | Template for mem0.json generation (oss mode only) |

## User Observation

User observed "hermes 持久化搞的很乱，各种缝缝补补" (the persistence is a mess, full of patches). This refers to the pattern where each new config file (like `mem0.json`) was bolted onto the existing backup/restore mechanism without being fully integrated into the `_FILES` lists or template system. The result is config files that are generated by one mechanism but not backed up by another — creating gaps like the `mem0.json` gap above. The reference repo `i3t2y/nexus` contains the original architecture design (`docs/ARCHITECTURE.md`) that shows the intended clean separation, while the live deployment has accumulated patches over multiple sessions.
