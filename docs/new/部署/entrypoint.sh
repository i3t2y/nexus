#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# [骨架留档·2026-07-29 标注] 本件为 omn 血统 entrypoint.sh daemon 编排模板,
# 非 Nexus 现役代码。Nexus hermes 现役 Space 用 start.sh 单进程直接 exec uvicorn
# (见 spaces/hermes/start.sh),无此 omn 多子进程 daemon 监督循环。
# 本件含 omn 三大设计要点可速查借鉴:
#   ① entrypoint npm install 一次性写 /logic/node_modules(omn 唯一写逻辑层处,
#      对应 Python 侧 __pycache__ 写态,PYTHONDONTWRITEBYTECODE=1 避噪);
#   ② STRICT(gate/上游死 exit 1)vs WARN(init/litestream 故意 fail-open 只告警不 exit:
#      omn 上游前滚迁移让旧库自动进新 schema,版本不齐仍可跑)分级,
#      真 exit 1 在 gate.js PSK 缺失(gate.js:46-49 process.exit(1)),
#      版本硬断言 EXPECTED_VERSION 在 omn 不存在(--用户要硬断言须自写);
#   ③ SCHED/LS/init/log 全进 /data RW(运行态写件全去 /data 或 /tmp,
#      Bucket rw /data 挂载专职持久)。
# Nexus 若移植须先建 goland/Python 等价编排(现役不需要,留作演进蓝图)。
# ══════════════════════════════════════════════════════════════════
set -eo pipefail

SVC_PID=""; INIT_PID=""; LS_PID=""; GATE_PID=""; SCHED_PID=""

_cleanup_done=0
_forward_signal() {
  for pid in "$SVC_PID" "$INIT_PID" "$LS_PID" "$GATE_PID" "$SCHED_PID"; do
    [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && kill -"$1" "$pid" 2>/dev/null || :
  done
}
_shutdown() {
  [ "$_cleanup_done" = 1 ] && return; _cleanup_done=1
  echo "[entry] shutdown signal received, graceful stop..."
  _forward_signal TERM
  g=0; while [ "$g" -lt 50 ]; do
    all_dead=1
    for pid in "$SVC_PID" "$INIT_PID" "$LS_PID" "$GATE_PID" "$SCHED_PID"; do
      [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null && all_dead=0
    done
    [ "$all_dead" = 1 ] && break
    sleep 0.1; g=$((g+1))
  done
  _forward_signal KILL
  for pid in "$SVC_PID" "$INIT_PID" "$LS_PID" "$GATE_PID" "$SCHED_PID"; do
    [ -n "$pid" ] && wait "$pid" 2>/dev/null || :
  done
  echo "[entry] shutdown complete"
}
trap '_shutdown' TERM INT

DATA_DIR="${DATA_DIR:-/data}"
COMPONENT="${NEXUS_COMPONENT:-omniroute}"
DB_PATH="${DATA_DIR}/${COMPONENT}.sqlite"
mkdir -p "$DATA_DIR"
echo "[entry] DATA=$DATA_DIR DB=$DB_PATH COMPONENT=$COMPONENT"

# ── 1. litestream restore ──
if [ -n "$R2_BUCKET" ] && command -v litestream >/dev/null 2>&1; then
  echo "[entry] litestream restore 开始..."
  DB_TMP=$(mktemp "/tmp/ls_restore_XXXXXX.sqlite")
  rc=0
  litestream restore -config /logic/litestream.yml -o "$DB_TMP" \
    2>/tmp/ls_restore.err || rc=$?
  if [ "$rc" = "0" ] && [ -s "$DB_TMP" ]; then
    _qc=$(sqlite3 "$DB_TMP" "PRAGMA quick_check;" 2>/dev/null | head -1)
    if [ "$_qc" = "ok" ]; then
      mv "$DB_TMP" "$DB_PATH"
      echo "[entry] restore 成功: DB=$DB_PATH quick_check=ok"
    else
      echo "[entry] WARN: restore quick_check 失败($_qc), 保留空库继续"
      rm -f "$DB_TMP"
    fi
  else
    echo "[entry] WARN: restore rc=$rc 或空文件, 新库启动 (R2 可能首次)"
    rm -f "$DB_TMP"
    cat /tmp/ls_restore.err 2>/dev/null | head -5 || true
  fi
else
  echo "[entry] WARN: R2_BUCKET 未注入, 跳过 restore (ephemeral 模式)"
fi

# ── 2. 启动上游业务服务 ──
SVC_PORT="${SVC_PORT:-3000}"
case "$COMPONENT" in
  omniroute)
    echo "[entry:omniroute] 启动 OmniRoute Next.js 服务..."
    export NODE_OPTIONS="--max-old-space-size=4096"
    cd /app && node server.js & SVC_PID=$!
    ;;
  hermes)
    echo "[entry:hermes] 启动 Hermes Agent 服务..."
    export HERMES_HOME="${HERMES_HOME:-/data/hermes}"
    mkdir -p "$HERMES_HOME"
    cd /app && python3 -u app.py --port "$SVC_PORT" --data-dir "$HERMES_HOME" & SVC_PID=$!
    if [ -f /logic/keepalive_relay.sh ]; then
      bash /logic/keepalive_relay.sh & echo "[entry:hermes] 保活辅助进程已启动"
    fi
    ;;
  langgraph)
    echo "[entry:langgraph] 启动 LangGraph Library Mode..."
    export SUPABASE_URL="${SUPABASE_URL?FATAL: 缺 SUPABASE_URL}"
    export SUPABASE_SERVICE_KEY="${SUPABASE_SERVICE_KEY?FATAL: 缺 SUPABASE_SERVICE_KEY}"
    cd /app && uvicorn app:app --host 0.0.0.0 --port "$SVC_PORT" & SVC_PID=$!
    ;;
  claude)
    echo "[entry:claude] 启动 Claude Code Headless 服务..."
    export ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY?FATAL: 缺 ANTHROPIC_API_KEY}"
    cd /app && node claude_server.js --port "$SVC_PORT" & SVC_PID=$!
    ;;
  codex)
    echo "[entry:codex] 启动 Codex CLI Headless 服务..."
    export OPENAI_API_KEY="${OPENAI_API_KEY?FATAL: 缺 OPENAI_API_KEY}"
    cd /app && node codex_server.js --port "$SVC_PORT" & SVC_PID=$!
    ;;
  *)
    echo "[entry] FATAL: 未知组件 $COMPONENT"
    exit 1
    ;;
esac

# ── 3. 健康等待 (最多 180s) ──
echo "[entry] 等待业务服务就绪 port=$SVC_PORT (最多 180s)..."
_waited=0
while [ "$_waited" -lt 180 ]; do
  _resp=$(curl -s -o /dev/null -w "%{http_code}" "http://127.0.0.1:${SVC_PORT}/healthz" \
    --max-time 3 2>/dev/null) || _resp="000"
  [ "$_resp" = "200" ] && { echo "[entry] 业务服务就绪 t=${_waited}s"; break; }
  sleep 2; _waited=$((_waited+2))
  kill -0 "$SVC_PID" 2>/dev/null || { echo "[entry] FATAL: 业务服务在等待期退出"; _shutdown; exit 1; }
done

# ── 4. 业务初始化 ──
if [ -f /logic/init.sh ]; then
  bash /logic/init.sh & INIT_PID=$!
  echo "[entry] init 后台运行 PID=$INIT_PID"
fi

# ── 5. litestream 后台复制 ──
if [ -n "$R2_BUCKET" ] && command -v litestream >/dev/null 2>&1; then
  litestream replicate -config /logic/litestream.yml & LS_PID=$!
  echo "[entry] litestream replicate 后台 PID=$LS_PID"
fi

# ── 6. CommitScheduler 日志归档 ──
if [ -f /logic/commit_scheduler.sh ] && [ -n "$LOG_PUBLIC_DATASET_REPO" ]; then
  bash /logic/commit_scheduler.sh & SCHED_PID=$!
  echo "[entry] CommitScheduler 后台 PID=$SCHED_PID"
fi

# ── 7. 启动网关 ──
node /logic/gate.js & GATE_PID=$!
echo "[entry] gate 后台 PID=$GATE_PID"

# ── 8. 监督循环 ──
_init_logged=0; _sched_logged=0
while true; do
  kill -0 "$GATE_PID" 2>/dev/null || { echo "[entry] FATAL: gate 退出"; _shutdown; exit 1; }
  kill -0 "$SVC_PID"  2>/dev/null || { echo "[entry] FATAL: 业务服务退出"; _shutdown; exit 1; }
  if [ -n "$INIT_PID" ] && ! kill -0 "$INIT_PID" 2>/dev/null && [ "$_init_logged" = 0 ]; then
    wait "$INIT_PID" 2>/dev/null; _rc=$?
    echo "[entry] WARN: init 退出 rc=$_rc"; _init_logged=1
  fi
  if [ -n "$SCHED_PID" ] && ! kill -0 "$SCHED_PID" 2>/dev/null && [ "$_sched_logged" = 0 ]; then
    echo "[entry] WARN: CommitScheduler 退出"; _sched_logged=1
  fi
  if [ -n "$LS_PID" ] && ! kill -0 "$LS_PID" 2>/dev/null; then
    [ "${LITESTREAM_STRICT:-0}" = 1 ] && { echo "[entry] FATAL: litestream strict"; _shutdown; exit 1; }
    echo "[entry] WARN: litestream 退出, DB 不再备份"; LS_PID=""
  fi
  sleep 1
done
