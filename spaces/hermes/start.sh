#!/usr/bin/env bash
# Hermes Space 启动脚本（自愈 + 包重放 + 后台双写与保活）。
# 借鉴 HuggingMes 的 start.sh + 自愈 Gateway 思路。
set -u

# 用户（与 Dockerfile 的 1000 一致）；HF 注入 user
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUNBUFFERED=1
export PYTHONPATH="/home/user/app/libs:${PYTHONPATH:-}"

APP_DIR="${HERMES_APP_DIR:-/home/user/app}"
LOG_DIR="${HERMES_LOG_DIR:-/home/user/app/logs}"
mkdir -p "$LOG_DIR"

echo "[start] replay packages..."
python "$APP_DIR/scripts/replay_packages.py" replay || echo "[start] replay skipped (no log yet)"

# 后台：Supabase→R2 双写快照（如配置了凭证才起）
if [ -n "${SUPABASE_URL:-}" ]; then
  echo "[start] persist daemon up"
  nohup python "$APP_DIR/scripts/persist_to_r2.py" >"$LOG_DIR/persist.log" 2>&1 &
fi

# 后台：下游 Space 保活探测
if [ -n "${KEEPALIVE_ENABLED:-0}" ] && [ "${KEEPALIVE_ENABLED}" = "1" ]; then
  echo "[start] keepalive daemon up"
  nohup python "$APP_DIR/scripts/keepalive.py" >"$LOG_DIR/keepalive.log" 2>&1 &
fi

# 主服务：Gradio Dashboard + FastAPI 路由，监听 7860（HF 要求）
# 自愈循环：进程退出则 5 秒后重启
while true; do
  echo "[start] launching hermes app on :7860"
  python -m uvicorn app.main:app --host 0.0.0.0 --port 7860
  code=$?
  echo "[start] app exited code=$code, restarting in 5s..."
  sleep 5
done
