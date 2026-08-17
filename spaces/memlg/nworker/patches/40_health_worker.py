#!/usr/bin/env python3
"""
Inject /health keep-alive endpoint + mount LangGraph worker router into main.py.

/health endpoint:
  - Lightweight: returns {"status":"ok"} if uvicorn is up + Neon SELECT 1
  - Used by cron-job.org keep-alive (pings every 4 min → prevents Neon scale-to-zero)
  - Neon compute suspend issue: connections denied while compute is suspending
    (neondatabase/neon issue #5838) → keep-alive prevents this

LangGraph worker router:
  - Mounts /app/worker/graph/__init__.py's router at /worker
  - LangGraph StateGraph wraps mem0 server for multi-step memory orchestration
  - FastAPI integration: app.include_router(worker_router)
  - Reference: https://www.zestminds.com/blog/build-ai-workflows-fastapi-langgraph/
"""
from pathlib import Path

MAIN = Path("/app/main.py")
code = MAIN.read_text()

# Inject /health endpoint
if "@app.get('/health'" not in code and '@app.get("/health"' not in code:
    health_patch = '''

# --- /health keep-alive endpoint (patched by dataset) ---
@app.get('/health', summary='Health check + Neon keep-alive')
async def health_check():
    """Lightweight health check. Also runs SELECT 1 to keep Neon awake."""
    try:
        from db import SessionLocal
        db = SessionLocal()
        db.execute(__import__('sqlalchemy').text('SELECT 1'))
        db.close()
        return {'status': 'ok', 'db': 'connected'}
    except Exception as e:
        return {'status': 'degraded', 'db': str(e)}, 200

'''
    code = code + health_patch
    print("[40] /health endpoint injected")
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
