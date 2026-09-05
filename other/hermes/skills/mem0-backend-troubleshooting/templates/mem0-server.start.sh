#!/bin/bash
# mem0 server startup: alembic migration + /health injection + uvicorn on 7860
set -e

echo "=== mem0 server starting ==="

# 0. Ensure history directory exists (mem0 SQLiteManager needs /app/history/history.db)
mkdir -p /app/history

# 1. Alembic migration — create auth/api_key/settings tables in Neon
echo "[1/3] Running alembic migration..."
cd /app
alembic upgrade head || {
    echo "WARNING: alembic migration failed (tables may already exist), continuing..."
}

# 2. Inject /health keepalive endpoint (mem0 server has no /health by default)
# Idempotent: skips if /health already present. Uses Python AST-safe patching.
echo "[2/3] Injecting /health endpoint..."
python3 -c "
with open('/app/main.py', 'r') as f:
    code = f.read()
if '/health' not in code:
    patch = '''

# --- Keep-alive endpoint (patched by start.sh) ---
@app.get('/health', summary='Health check + Neon keep-alive')
async def health_check():
    \"\"\"Lightweight health check. Also runs SELECT 1 to keep Neon awake.\"\"\"
    try:
        from db import SessionLocal
        db = SessionLocal()
        db.execute(__import__('sqlalchemy').text('SELECT 1'))
        db.close()
        return {'status': 'ok', 'db': 'connected'}
    except Exception as e:
        return {'status': 'degraded', 'db': str(e)}, 200

'''
    code = code + patch
    with open('/app/main.py', 'w') as f:
        f.write(code)
    print('/health endpoint injected')
else:
    print('/health endpoint already present')
"

# 3. Start uvicorn on HF's only exposed port
echo "[3/3] Starting uvicorn on port 7860..."
exec uvicorn main:app --host 0.0.0.0 --port 7860
