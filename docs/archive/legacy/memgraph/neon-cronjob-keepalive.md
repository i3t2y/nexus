# Neon Postgres + cron-job.org Keep-Alive Pattern

When deploying to HF Space with Neon Postgres as the database backend, three free-tier services can keep each other alive with zero cost. This pattern is verified for the mem0 server deployment (2026-08-15).

## Why Keep-Alive Is Needed

| Service | Sleep Policy | Wakeup |
|---|---|---|
| HF Space (free tier) | 48h no access → sleep | Auto on first request (cold start ~10-30s) |
| Neon Postgres (free tier) | 5min no activity → scale-to-zero | Auto on first query (~500ms cold start) |

Both auto-wake, but cold starts add latency. cron-job.org prevents sleep entirely.

## Architecture

```
cron-job.org (free, every 4min)
    ↓ HTTP GET https://<hf-user>-<space-name>.hf.space/health
HF Space /health endpoint (Docker, port 7860)
    ↓ psycopg "SELECT 1"
Neon Postgres (free, AWS us-east-1)
    ↓ queried within 5min → no scale-to-zero
```

One cron ping keeps all three services alive:
- **cron-job.org** → ping keeps running (free, unlimited jobs)
- **HF Space** → HTTP access resets 48h sleep timer
- **Neon** → SELECT 1 within 5min window prevents scale-to-zero

## Setup

### 1. HF Space /health Endpoint

If the service doesn't have a `/health` route, inject one at startup. For mem0 server, `start.sh` patches `main.py` dynamically:

```bash
python3 -c "
import ast, sys
with open('server/main.py') as f: src = f.read()
if '/health' not in src:
    inject = '''

# Injected /health endpoint for keep-alive
@app.get('/health')
async def health():
    from db import SessionLocal
    try:
        db = SessionLocal()
        db.execute('SELECT 1')
        db.close()
        return {'status': 'ok'}
    except Exception as e:
        return {'status': 'error', 'detail': str(e)}, 500
'''
    with open('server/main.py', 'a') as f: f.write(inject)
    print('[keep-alive] /health endpoint injected')
"
```

This is idempotent (checks for `/health` in source before injecting).

### 2. cron-job.org Configuration

1. Register a dedicated cron-job.org account (separate from other uses, for management clarity)
2. Create a job:
   - **URL**: `https://<hf-user>-<space-name>.hf.space/health`
   - **Schedule**: Every 4 minutes
   - **Method**: GET
   - **Timeout**: 30 seconds
3. cron-job.org is free, 15+ years history, supports 1 HTTP request per minute

### 3. Neon Free Tier Specs

- **Storage**: 512 MB per project
- **Compute**: 1919 CU-hours/month (keep-alive uses ~186 CU-h = 9.7%)
- **pgvector**: `CREATE EXTENSION IF NOT EXISTS vector;`
- **Connection pool**: Built-in PgBouncer, up to 10000 connections
- **Scale-to-zero**: After 5min no activity, auto-wakes ~500ms
- **Projects**: Up to 100 free projects, each with independent 0.5GB + 100 CU-h (quotas stack)

### 4. Neon Region Selection

Choose the same AWS region as HF Space for minimal latency:

```bash
# Detect HF Space region
curl -sL https://ipinfo.io/json | python3 -c "
import json, sys
d = json.load(sys.stdin)
print(f'{d[\"city\"]}, {d[\"region\"]} ({d[\"org\"]})')
"
# Result: Ashburn, Virginia (Amazon.com) = AWS us-east-1
```

HF Space free tier runs in **AWS us-east-1 (Ashburn, Virginia)**. Select Neon AWS us-east-1 for same-region (<1ms latency).

### 5. Neon Multi-Project for Stacked Quotas

Neon free tier allows 100 projects, each with independent 512MB storage + 100 CU-h/month. Quotas stack across projects.

| Project | Usage | Storage | Keep-alive |
|---|---|---|---|
| project 1 | mem0 vector store + auth | 0.5GB | 1 cron-job.org job |
| project 2 | agent program data | 0.5GB | 1 cron-job.org job (or skip if accessed regularly) |

Each project has an independent endpoint → each needs its own keep-alive cron job. cron-job.org is free and unlimited in job count.

Alternative: single project with multiple databases (up to 500 per branch), but storage is shared (0.5GB total for the project).

## Neon vs Supabase (for mem0/pgvector backend)

| | Supabase Free | Neon Free |
|---|---|---|
| Storage | 500MB | 512MB |
| pgvector | ✅ | ✅ |
| Sleep policy | 7 days no activity → full project pause (manual restore) | 5min no activity → scale-to-zero (auto-wake ~500ms) |
| Keep-alive | Natural (if used daily) | cron-job.org ping required |
| Connection pool | Direct 60 / pooler 200 | Built-in PgBouncer 10000 |
| Multi-project quota | 2 projects | 100 projects (quotas stack) |

Neon is better if: worried about 7-day pause, need more than 2 projects, want auto-wake without manual restore. Supabase is simpler if: already using it, daily access prevents pause, don't need multi-project stacking.

## Official Endorsement

Neon official employee (tristan957) confirmed cron keep-alive on Hacker News:
> "If you want an always on compute on the free tier, setup a cron job, and every 4 minutes or so send a `SELECT 1` which will keep the database awake."

Neon also supports SQL over HTTP via `@neondatabase/serverless` driver, enabling keep-alive from edge functions (Cloudflare Workers) without a TCP connection.
