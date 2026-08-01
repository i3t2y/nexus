#!/usr/bin/env bash
# Hermes Space 启动脚本（自愈 + 包重放 + 后台双写与保活 + Bucket 逻辑层挂载等待）
# 永续改造:逻辑层(app/scripts/libs)从 HF Storage Bucket rw /data 挂载读取,
#           不再依赖镜像内 COPY。镜像仅含依赖(base)+ start.sh 引导。
set -u

# 用户（与 Dockerfile/base 镜像的 1000 一致）；HF 注入 user
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUNBUFFERED=1
export PYTHONPATH="/data/libs:${PYTHONPATH:-}"

# 打印当前镜像版本(Dockerfile ENV BASE_IMAGE 转存;运维排查用)
echo "[start] BASE_IMAGE=${BASE_IMAGE:-<unset-from-Dockerfile>}"

APP_DIR="${HERMES_APP_DIR:-/data}"
LOG_DIR="${HERMES_LOG_DIR:-/data/logs}"

# ─────────────────────────────────────────────────────────────
# 等待 Bucket 挂载就绪(/data 仅 runtime,挂载于容器启动前完成)
# 判断点:/data/libs/storage/__init__.py + /data/app/main.py + /data/scripts/litestream.yml
# 三存才进 boot;最多 30s;超时试 hf CLI bootstrap fallback 拉 Bucket;仍败进 while 5s 自愈重试
# ─────────────────────────────────────────────────────────────
wait_for_mount() {
  local key_pkg="$APP_DIR/libs/storage/__init__.py"
  local key_app="$APP_DIR/app/main.py"
  local key_ls="$APP_DIR/scripts/litestream.yml"
  local waited=0
  while [ $waited -lt 30 ]; do
    if [ -f "$key_pkg" ] && [ -f "$key_app" ] && [ -f "$key_ls" ]; then
      echo "[start] bucket mounted OK (waited ${waited}s): $key_pkg + $key_app + $key_ls"
      return 0
    fi
    sleep 1; waited=$((waited + 1))
  done
  echo "[start] WARN: bucket mount not ready after ${waited}s, try hf CLI bootstrap fallback..."
  return 1
}

bootstrap_from_bucket() {
  # 兜底:挂载未达,用 hf CLI 从 HF Storage Bucket 拉逻辑到 $APP_DIR。
  # 查证(CLI 官方文档):Bucket 是 Xet 后端 S3-like object storage,非 git dataset repo。
  #   - 拉 Bucket 用 `hf buckets sync remote local`(CLI 子进程),
  #     非 snapshot_download(repo_type="dataset")(那是拉 git-based dataset repo,走不通 Bucket,哑火+若建同名 dataset repo 则双份存储)。
  #   - huggingface_hub 1.x Python client 暂无 bucket pull 高层 API,故直接调 CLI。
  # 需 HF_TOKEN env。仅作兜底,正常路径靠 Volume 挂载直读。
  local token="${HF_TOKEN:-}"
  local owner="${HF_OWNER:-${SPACE_AUTHOR_NAME:-}}"
  local bucket="${NEXUS_LOGIC_BUCKET:-nexus-logic}"
  [ -z "$token" ] && { echo "[start] bootstrap skip: no HF_TOKEN"; return 1; }
  [ -z "$owner" ] && { echo "[start] bootstrap skip: no HF_OWNER"; return 1; }
  command -v hf >/dev/null 2>&1 || { echo "[start] bootstrap skip: no hf CLI"; return 1; }
  echo "[start] bootstrap: pulling bucket ${owner}/${bucket} → $APP_DIR ..."
  HF_TOKEN="$token" hf buckets sync "hf://buckets/${owner}/${bucket}" "$APP_DIR/" --no-delete \
    || { echo "[start] bootstrap failed (hf buckets sync)"; return 1; }
  echo "[start] bootstrap OK"
}

# 首启等挂载;失败试 bootstrap;仍败进 while 5s 自愈(非退出,保留容器存活)
if ! wait_for_mount; then
  bootstrap_from_bucket || true
fi

# 挂载就绪后建日志目录(rw /data 下,跨重启持久)
mkdir -p "$LOG_DIR" 2>/dev/null || echo "[start] WARN: mkdir $LOG_DIR failed (mount not ready?)"

# ── Hermes Agent 内核永续层:HERMES_HOME + 插件 + config + litestream ──
export HERMES_HOME="${HERMES_HOME:-/data/.hermes}"
mkdir -p "$HERMES_HOME/plugins" 2>/dev/null || echo "[start] WARN: mkdir $HERMES_HOME failed (mount not ready?)"

# nexus 插件从 Bucket 逻辑层(/data/scripts/plugins/nexus)拷到 HERMES_HOME/plugins/nexus
# (hermes 用户插件目录 = $HERMES_HOME/plugins,hermes_cli/plugins.py:1390 _discover_and_load 扫此)
cp -r "$APP_DIR/scripts/plugins/nexus" "$HERMES_HOME/plugins/" 2>/dev/null \
  && echo "[start] nexus plugin staged → $HERMES_HOME/plugins/nexus" \
  || echo "[start] WARN: stage nexus plugin failed (source missing?)"

# config.yaml 预置 plugins.enabled:[nexus](standalone 插件 opt-in,否则 register() 不调)
# 不覆盖用户既有 config(其可能改过);仅首启落盘模板
if [ ! -f "$HERMES_HOME/config.yaml" ]; then
  cat > "$HERMES_HOME/config.yaml" <<'CFG'
# Hermes Agent config(永续改造首启自动生成)
# standalone 插件须在此 enabled 列入才被加载进 agent loop
plugins:
  enabled:
    - nexus
CFG
  echo "[start] config.yaml seeded (plugins.enabled:[nexus])"
fi

# litestream 恢复 state.db(WAL→R2,铁律 L8):先 restore 到临时再原子 mv;
# 首启 R2 无副本 → restore 失败即开新库(agent_server 首构造自动建 state.db)
LS_CFG="$APP_DIR/scripts/litestream.yml"
if [ -f "$LS_CFG" ]; then
  echo "[start] litestream restore state.db ..."
  if litestream restore -config "$LS_CFG" -o /tmp/ls_restore.sqlite >/dev/null 2>&1; then
    mkdir -p "$HERMES_HOME"
    mv /tmp/ls_restore.sqlite "$HERMES_HOME/state.db"
    echo "[start] litestream restore OK → $HERMES_HOME/state.db"
  else
    echo "[start] litestream restore skipped (R2 首启无副本,新开库)"
  fi
  # 后台起 WAL 复制(sidecar);fail-open(进程死仅 WARN,不拦 uvicorn)
  nohup litestream replicate -config "$LS_CFG" >"$LOG_DIR/litestream.log" 2>&1 &
  LS_PID=$!
  echo "[start] litestream replicate up pid=$LS_PID (WAL→R2 sync 10s,L8)"
fi

echo "[start] replay packages..."
python "$APP_DIR/scripts/replay_packages.py" replay || echo "[start] replay skipped (no log yet)"

# 后台：Supabase→R2 双写快照（如配置了凭证才起）
if [ -n "${SUPABASE_URL:-}" ]; then
  echo "[start] persist daemon up"
  nohup python "$APP_DIR/scripts/persist_to_r2.py" >"$LOG_DIR/persist.log" 2>&1 &
fi

# 后台：下游 Space 保活探测
# 默认开:omniroute(主推理路径)+ 下游三 Space 均依赖保活防 48h 休眠;
#  置 KEEPALIVE_ENABLED=0 可关。防 omniroute 休眠致路B首请冷启动超时。
KEEPALIVE_ENABLED="${KEEPALIVE_ENABLED:-1}"
if [ "$KEEPALIVE_ENABLED" = "1" ]; then
  echo "[start] keepalive daemon up"
  nohup python "$APP_DIR/scripts/keepalive.py" >"$LOG_DIR/keepalive.log" 2>&1 &
fi

# 主服务：Gradio Dashboard + FastAPI 路由，监听 7860（HF 要求）
# --app-dir /data → app 包从 /data/app 解析;PYTHONPATH=/data/libs → storage/shared 顶层包解析
# 自愈循环：挂载/启动失败则 5 秒后重启(等挂载重试,不放弃)
while true; do
  echo "[start] launching hermes app on :7860 (app-dir=$APP_DIR)"
  python -m uvicorn app.main:app --app-dir "$APP_DIR" --host 0.0.0.0 --port 7860
  code=$?
  echo "[start] app exited code=$code, recheck mount + restart in 5s..."
  # litestream watchdog:uvicorn 死后查 LS 仍活否;fail-open(死仅 WARN,不退)— 修正⑤
  if [ -n "${LS_PID:-}" ] && ! kill -0 "$LS_PID" 2>/dev/null; then
    echo "[start] WARN: litestream sidecar (pid=$LS_PID) died after uvicorn exit — fail-open, will respawn replicate next round"
    unset LS_PID
  fi
  # 重启前再确认挂载(挂载可能滞后到这步)
  [ -f "$APP_DIR/app/main.py" ] || { echo "[start] main.py still missing, rebootstrap..."; bootstrap_from_bucket || true; }
  # litestream 死则重启(每轮 boot 段的 replicate 起一次;这里仅在它死后重起)
  if [ -z "${LS_PID:-}" ] && [ -f "$APP_DIR/scripts/litestream.yml" ]; then
    nohup litestream replicate -config "$APP_DIR/scripts/litestream.yml" >"$LOG_DIR/litestream.log" 2>&1 &
    LS_PID=$!
    echo "[start] litestream replicate respawned pid=$LS_PID"
  fi
  sleep 5
done
