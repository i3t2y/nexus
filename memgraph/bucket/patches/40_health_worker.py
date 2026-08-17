#!/usr/bin/env python3
"""
Inject /health keep-alive endpoint + mount LangGraph worker router into main.py.

/health endpoint:
  - Ultra-lightweight: returns {"status":"ok"} immediately
  - NO Neon SELECT 1 — let Neon scale-to-zero naturally (cold start ~1.8s acceptable)
  - cron-job.org pings /health only to keep HF Space awake (48h sleep threshold)
  - Neon wakes on-demand when mem0 actually queries it (via SQLAlchemy/psycopg2)

LangGraph worker router:
  - Mounts /app/worker/graph/__init__.py's router at /worker
  - LangGraph StateGraph wraps mem0 server for multi-step memory orchestration
  - FastAPI integration: app.include_router(worker_router)
  - Reference: https://www.zestminds.com/blog/build-ai-workflows-fastapi-langgraph/

=== 2026-08-17 change ===
Removed Neon SELECT 1 from /health. Rationale:
  - Neon Free plan auto-suspend 5min; cron 4min ping < 5min → compute never sleeps → 180 CU-h/mo > 100 limit
  - Neon cold start ~1.8s on first real query is acceptable for AI agent memory backend
  - /health should only verify the HTTP service is alive, not poke downstream DB
  - Neon stays suspended between real mem0 queries → CU-h ~0.5-3/mo, well under 100
"""
from pathlib import Path

MAIN = Path("/app/main.py")
code = MAIN.read_text()

# Inject /health endpoint
if "/health" not in code:
    health_patch = '''

# --- /health keep-alive endpoint (patched by dataset) ---
@app.get('/health', summary='Health check (HF Space keep-alive)')
async def health_check():
    """Ultra-lightweight health check. Does NOT ping Neon (let it scale-to-zero)."""
    return {'status': 'ok'}

'''
    code = code + health_patch
    print("[40] /health endpoint injected (no Neon ping)")
else:
    print("[40] /health endpoint already present")

# Mount LangGraph worker router
if "worker_router" not in code:
    worker_patch = '''

# --- LangGraph worker router (patched by dataset) ---
try:
    import sys
    sys.path.insert(0, '/app/worker')
    from graph import router as worker_router
    app.include_router(worker_router)
    print('LangGraph worker router mounted at /worker')
except Exception as e:
    print(f'WARNING: Failed to mount worker router: {e}')

'''
    code = code + worker_patch
    print("[40] worker router injected")
else:
    print("[40] worker router already present")

MAIN.write_text(code)
