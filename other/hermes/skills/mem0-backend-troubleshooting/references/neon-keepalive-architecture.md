# Neon + cron-job.org 保活 Architecture (No VPS)

User decided (2026-08-15) to switch mem0's pgvector backend from Supabase to Neon, primarily to eliminate the 7-day inactivity pause risk. cron-job.org provides free external keepalive pings.

## Why Neon over Supabase

| | Supabase (old) | Neon (new) |
|---|---|---|
| Storage | 500MB per project | 0.5GB per project (×100 projects = 50GB total) |
| pgvector | ✅ | ✅ native |
| Pause behavior | 7 days no activity → **full project pause** (manual Restore needed) | 5 min no activity → **scale-to-zero** (auto-wake ~500ms, no manual action) |
| Keepalive | hermes daily use = 天然保活 (but 7-day gap = paused) | cron-job.org every 4 min = never sleeps |
| Connection pool | 60 direct / 200 pooler | Built-in PgBouncer, 10,000 connections |
| Compute quota | N/A | 100 CU-hours/month per project (keepalive uses ~9.7%) |
| Projects | 2 active | 100 |

**Key difference**: Neon's scale-to-zero is NOT the same as Supabase's pause. Scale-to-zero = compute suspends, storage stays, next query auto-wakes in ~500ms. Supabase pause = entire project frozen, requires manual dashboard Restore.

## Neon Free Tier Multi-Database (verified 2026-08-15)

### Two ways to have "multiple databases"

**Method A: Same project, multiple databases (one branch)**
- `CREATE DATABASE hermes_config;` works normally
- Each branch supports up to **500 databases** (Neon FAQ)
- All databases share the project's 0.5GB storage + 100 CU-hours
- One endpoint = one keepalive target

**Method B: Multiple projects (额度叠加)**
- Free tier: up to **100 projects** (increased from 10 → 100 in 2026-01)
- Each project has **independent** 0.5GB storage + 100 CU-hours
- Total: 100 × 0.5GB = **50GB theoretical max** storage
- Each project has its own endpoint (own scale-to-zero)
- Each endpoint needs its own keepalive cron job

### User's requirement: "配置neon可用多库" + "免费额度是多库叠加的"

This means Method B (multi-project) for额度叠加, with the understanding that:
- Each project's 0.5GB is independent (not shared across projects)
- CU-hours are per-project (100 each), not account-shared
- Neon Blog confirms: "each project has its own resource limits"

### Recommended project layout

| Project | Database | Purpose | Keepalive |
|---|---|---|---|
| project-1 `mem0-memory` | `neondb` | mem0 vector storage + auth tables | cron-job.org every 4 min |
| project-2 `hermes-data` | `hermes_config` | persist_to_r2 backup tables | cron-job.org every 4 min |
| project-3 (future) | — | other agent memory | on-demand |

## cron-job.org Keepalive

### Evidence (all verified 2026-08-15 via AnySearch)

1. **Neon official employee** (tristan957) on Hacker News: "If you want an always on compute on the free tier, you can just setup a cron job, and every 4 minutes or so send a `SELECT 1` which will keep the database awake."
2. **Neon docs** (scale-to-zero): compute suspends after 5 min inactivity, auto-wakes on next query (~hundred ms). `pg_cron` extension only runs when compute is active → cannot self-keepalive. Must use external cron.
3. **cron-job.org**: Free, 15 years running, millions of jobs/day. Supports HTTP GET/POST every 1 min. 30s timeout, 15 consecutive failures → auto-stop + notify. REST API for programmatic management.

### Keepalive chain (three services互相保活)

```
cron-job.org (free, every 4 min)
  ↓ HTTP GET
HF Space /health (mem0 server, port 7860)
  ↓ psycopg SELECT 1
Neon Postgres (free, 0.5GB + pgvector)
  ↓ 5min内被查询 → 不 scale-to-zero
```

**Three free services keep each other alive**:
- cron-job.org pings HF Space → HF Space stays awake (48h sleep avoided)
- HF Space /health queries Neon → Neon stays awake (5min scale-to-zero avoided)
- All data persists in Neon (storage survives scale-to-zero)

### cron-job.org setup

**User preference**: Register a **dedicated cron-job.org account** exclusively for Neon keepalive — do not mix with other cron uses. This isolates keepalive monitoring/failure notifications from unrelated jobs.

1. Register a dedicated account at cron-job.org (free, use a separate email like xxx.keepalive@gmail.com)
2. Create job: URL = `https://your-mem0-space.hf.space/health`, schedule = every 4 minutes
3. Response < 1024 bytes, < 30s timeout → /health endpoint returns `{"status":"ok"}` (well within limits)
4. For multi-project Neon: create one cron job per project endpoint, all pointing to the same HF Space /health (which does `SELECT 1` on all connections)

### Neon compute budget

- Free tier: 100 CU-hours/month per project
- Always-on (0.25 CU running 24/7) would use 186 CU-hours → exceeds 100 CU-h quota
- **With cron-job.org pinging every 4 min**: compute wakes for ~1-2s per ping, not 24/7
- Actual: 43200 pings/month × ~1-2s compute per ping = ~12-24 compute minutes/month ≈ 0.2-0.4 CU-hours
- **Keepalive cost: ~0.2-0.4 CU-hours/month << 100 CU-hours free quota = ~0.2-0.4%**
- This is far more efficient than always-on — no risk of quota exhaustion

## HF Space /health endpoint

The mem0 server (thin wrapper or official Docker) needs a `/health` route:

```python
@app.get("/health")
def health():
    # Ping Neon to keep it awake
    try:
        m._memory.vector_store._client.execute("SELECT 1")  # pgvector provider
        return {"status": "ok", "db": "connected"}
    except:
        return {"status": "degraded", "db": "error"}
```

This endpoint:
1. Receives cron-job.org ping every 4 min
2. Executes `SELECT 1` on Neon → resets the 5-min scale-to-zero timer
3. Returns small JSON (< 1024 bytes for cron-job.org limit)
4. Doubles as a health check for the Space itself

## Migration from Supabase to Neon

Current Supabase usage: 11 MB, 54 rows in hermes_mem0, 8 tables total.

Migration steps:
1. Create Neon project, enable pgvector extension
2. Create `hermes_mem0` table with same schema (2048-dim vector + payload jsonb)
3. Export 54 rows from Supabase → import to Neon
4. Update mem0.json `connection_string` to Neon's
5. Update persist_to_r2.py Supabase connection to Neon (or keep on Supabase for non-mem0 tables)
6. Set up cron-job.org keepalive
7. Restart hermes daemon

**Note**: Only mem0's `hermes_mem0` table needs to move to Neon. The other 7 Supabase tables (agent_states, task_logs, etc.) can stay on Supabase or move to a second Neon project.
