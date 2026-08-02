#!/usr/bin/env bash
# Hermes Space 启动脚本（K 形态,2026-08-02 实证推翻重定向）
# 永续改造:逻辑层(app/scripts/libs)从 HF Storage Bucket rw /data 挂载读取,
#           不再依赖镜像内 COPY。镜像仅含依赖(base)+ start.sh 引导。
# K 形态:弃自建 Gradio+FastAPI 自路由+agent_server.py,改 hermes 全原生三组件:
#   - 主进程 = app.main:boot(不再 uvicorn app.main:app,无自路由壳)
#   - daemon thread 1 gateway(含 api_server adapter + telegram/discord)
#   - daemon thread 2 dashboard in-proc --port 7860 直监听(非反代,非 subprocess)
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
#        + /data/scripts/config.yaml.template + /data/scripts/plugins/nexus-r2/dashboard/manifest.json
# 五存才进 boot;最多 30s;超时试 hf CLI bootstrap fallback 拉 Bucket;仍败进 while 5s 自愈重试
# ─────────────────────────────────────────────────────────────
wait_for_mount() {
  local key_pkg="$APP_DIR/libs/storage/__init__.py"
  local key_app="$APP_DIR/app/main.py"
  local key_ls="$APP_DIR/scripts/litestream.yml"
  local key_cfg="$APP_DIR/scripts/config.yaml.template"
  local key_plugin="$APP_DIR/scripts/plugins/nexus-r2/dashboard/manifest.json"
  local waited=0
  while [ $waited -lt 30 ]; do
    if [ -f "$key_pkg" ] && [ -f "$key_app" ] && [ -f "$key_ls" ] \
       && [ -f "$key_cfg" ] && [ -f "$key_plugin" ]; then
      echo "[start] bucket mounted OK (waited ${waited}s)"
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
  #     非 snapshot_download(repo_type="dataset")(那是拉 git-based dataset repo,走不通 Bucket)。
  #   - huggingface_hub 1.x Python client 暂无 bucket pull 高层 API,故直接调 CLI。
  # 需 HF_TOKEN env。仅作兜底,正常路径靠 Volume 挂载直读。
  local token="${HF_TOKEN:-}"
  local owner="${HF_OWNER:-}"
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

# 挂载就绪后建日志目录 + HERMES_HOME(rw /data 下,跨重启持久)
mkdir -p "$LOG_DIR" 2>/dev/null || echo "[start] WARN: mkdir $LOG_DIR failed (mount not ready?)"

# ── Hermes Agent 内核永续层:HERMES_HOME + 插件双目录 + config + litestream ──
export HERMES_HOME="${HERMES_HOME:-/data/.hermes}"
mkdir -p "$HERMES_HOME/plugins" 2>/dev/null || echo "[start] WARN: mkdir $HERMES_HOME failed (mount not ready?)"

# nexus 两 plugin 从 Bucket 逻辑层拷到 HERMES_HOME/plugins(K1 决策-3 两 plugin 目录)
#   - nexus-r2: 三下游 bridge tool(toolset=nexus)+ R2 文件 CRUD dashboard tab
#   - nexus-ops: 纯 dashboard tab(下游探活 + Supabase 业务表只读),无 tool
# hermes 用户插件目录 = $HERMES_HOME/plugins(hermes_cli/plugins.py 扫此 + web_server dashboard discovery 扫此)
for pname in nexus-r2 nexus-ops; do
  if [ -d "$APP_DIR/scripts/plugins/$pname" ]; then
    rm -rf "$HERMES_HOME/plugins/$pname" 2>/dev/null
    cp -r "$APP_DIR/scripts/plugins/$pname" "$HERMES_HOME/plugins/" \
      && echo "[start] $pname plugin staged → $HERMES_HOME/plugins/$pname" \
      || echo "[start] WARN: stage $pname plugin failed"
  else
    echo "[start] WARN: $pname plugin source missing at $APP_DIR/scripts/plugins/$pname"
  fi
done
# 清 pycache(避免旧版名 nexus 残留字节码)
find "$HERMES_HOME/plugins" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# config.yaml 从模板生成(K 形态:platform_toolsets 三行 + plugins.enabled 两 plugin
#   + disabled_toolsets + database.wournal_mode:wal)。
# 永远覆盖:config 是 nexus 逻辑层管项(model.provider/platform_toolsets/plugins 单源),
#   非用户可改 — sync-logic-bucket 推 Bucket 后 start 强制对齐,防旧 config 锁死 provider。
if [ -f "$APP_DIR/scripts/config.yaml.template" ]; then
  if [ ! -f "$HERMES_HOME/config.yaml" ] || ! cmp -s "$APP_DIR/scripts/config.yaml.template" "$HERMES_HOME/config.yaml"; then
    cp "$APP_DIR/scripts/config.yaml.template" "$HERMES_HOME/config.yaml"
    echo "[start] config.yaml updated from template (provider + platform_toolsets + plugins)"
  else
    echo "[start] config.yaml already in sync with template"
  fi
else
  echo "[start] WARN: config.yaml.template missing, $HERMES_HOME/config.yaml not seeded"
fi

# litestream 恢复 state.db(WAL→R2,铁律 L8):先 restore 到临时再原子 mv;
# 首启 R2 无副本 → restore 失败即开新库(hermes gateway 首构造自动建 state.db)
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
  # 后台起 WAL 复制(sidecar);fail-open(进程死仅 WARN,不拦 boot)
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
#  置 KEEPALIVE_ENABLED=0 可关。防 omniroute 休眠致首请冷启动超时。
KEEPALIVE_ENABLED="${KEEPALIVE_ENABLED:-1}"
if [ "$KEEPALIVE_ENABLED" = "1" ]; then
  echo "[start] keepalive daemon up"
  nohup python "$APP_DIR/scripts/keepalive.py" >"$LOG_DIR/keepalive.log" 2>&1 &
fi

# ── K-R7 DNS 封兜底(二线,hermes 原生 telegram_network.py DoH 是主路) ──
# hermes telegram adapter 原生自带 DoH+fallback IP 解 HF DNS 封 api.telegram.org(零改源自动解)。
# 此 n-hmes 式脚本仅兜底 hermes 适配器外触发的 Node 进程(web build/playwright DNS)。
if [ -f "$APP_DIR/scripts/dns-resolve.py" ]; then
  echo "[start] DNS-resolve (binary 二线) ..."
  nohup python "$APP_DIR/scripts/dns-resolve.py" >"$LOG_DIR/dns-resolve.log" 2>&1 &
fi

# ── 主服务:app.main:boot(K 形态双 daemon thread = gateway + dashboard) ──
# HF 必须有进程监听 7860:dashboard daemon thread 直跑 start_server --port 7860
#   (直监听非反代;host 缺省 127.0.0.1 本地免 OAuth,生产经 DASHBOARD_BIND_HOST=0.0.0.0 + auth provider)
# gateway daemon thread 同 async loop 起 api_server adapter(API_SERVER_KEY ≥16 触发)+ IM。
# 自愈循环:boot 退出(任一 daemon thread 死)则 5 秒后重启 boot 整进。
while true; do
  echo "[start] launching hermes boot (gateway + dashboard :7860) app-dir=$APP_DIR"
  python -c "import sys; sys.path.insert(0,'$APP_DIR'); from app.main import boot; boot()"
  code=$?
  echo "[start] boot exited code=$code, recheck mount + restart in 5s..."
  # litestream watchdog:boot 死后查 LS 仍活否;fail-open(死仅 WARN,不退)
  if [ -n "${LS_PID:-}" ] && ! kill -0 "$LS_PID" 2>/dev/null; then
    echo "[start] WARN: litestream sidecar (pid=$LS_PID) died after boot exit — fail-open"
    unset LS_PID
  fi
  [ -f "$APP_DIR/app/main.py" ] || { echo "[start] main.py still missing, rebootstrap..."; bootstrap_from_bucket || true; }
  if [ -z "${LS_PID:-}" ] && [ -f "$APP_DIR/scripts/litestream.yml" ]; then
    nohup litestream replicate -config "$APP_DIR/scripts/litestream.yml" >"$LOG_DIR/litestream.log" 2>&1 &
    LS_PID=$!
    echo "[start] litestream replicate respawned pid=$LS_PID"
  fi
  sleep 5
done
