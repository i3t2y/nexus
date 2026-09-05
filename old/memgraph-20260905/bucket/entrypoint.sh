#!/bin/bash
set -e

echo "=== entrypoint.sh — mem0 server runtime ==="

# ──────────────────────────────────────────────────────────────
# Phase 1: Install worker dependencies
# ──────────────────────────────────────────────────────────────
if [ -f /app/worker/requirements.txt ]; then
    echo "[1/4] Installing worker requirements..."
    pip install --no-cache-dir -r /app/worker/requirements.txt \
        || echo "WARNING: worker pip install failed, continuing..."
else
    echo "[1/4] No worker requirements.txt found, skipping"
fi

# ──────────────────────────────────────────────────────────────
# Phase 2: Apply runtime patches
#   run.py 协调所有 patch 执行顺序 (pre-alembic → alembic → post-alembic)
#   以后加 patch: 在 patches/ 目录加 .py 文件 + 改 run.py 里的列表
# ──────────────────────────────────────────────────────────────
echo "[2/4] Applying runtime patches..."
if [ -f /app/worker/run.py ]; then
    python3 /app/worker/run.py
else
    echo "WARNING: run.py not found, starting bare mem0"
fi

# ──────────────────────────────────────────────────────────────
# Phase 3:alembic migration (creates auth/api_key/settings tables)
#   run.py 里已经跑过一次 alembic, 但如果 patch 动了 DB schema 可能需要再跑
#   保守起见这里只跑一次 (run.py 那次), 此处跳过
# ──────────────────────────────────────────────────────────────
echo "[3/4] Patches + alembic done (see run.py output above)"

# ──────────────────────────────────────────────────────────────
# Phase 4: Start uvicorn
#   mem0 官方原生鉴权: ADMIN_API_KEY (HF Secret) + 去掉 AUTH_DISABLED
#   verify_auth 链路 (auth.py):
#     1. Bearer JWT → 解析 (HF private Space 会注入 Bearer, public 不会)
#     2. X-API-Key → 匹配 ADMIN_API_KEY → admin (hermes SelfHostedBackend 用此)
#     3. 都没有 → 401 (AUTH_DISABLED 未设)
#   /health 端点 (patch 40 注入) 不走 verify_auth → cron 保活不受影响
# ──────────────────────────────────────────────────────────────
echo "[4/4] Starting uvicorn on port 7860 (mem0 native auth: ADMIN_API_KEY)..."
cd /app
exec uvicorn main:app --host 0.0.0.0 --port 7860
