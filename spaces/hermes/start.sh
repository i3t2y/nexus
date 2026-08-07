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
# LOG_DIR 移出 bucket FUSE,放容器本地盘 /opt/data(与 HERMES_HOME 同盘)。
# 根因:HF /data 实为 bucket mount(FUSE/Xet),日志写 FUSE 慢且与 state 同区割裂。
# /opt/data 本地盘 ephemeral 但重启不损(日志非关键持久,核心四表在 Supabase+R2)。
LOG_DIR="${HERMES_LOG_DIR:-/opt/data/logs}"

# ─────────────────────────────────────────────────────────────
# 等待 Bucket 挂载就绪(/data 仅 runtime,挂载于容器启动前完成)
# 判断点:/data/libs/storage/__init__.py + /data/app/main.py
#        + /data/scripts/config.yaml.template + /data/scripts/plugins/nexus-r2/dashboard/manifest.json
# 四存才进 boot;最多 30s;超时试 hf CLI bootstrap fallback 拉 Bucket;仍败进 while 5s 自愈重试
# (★2026-08-05 删 litestream.yml 判断点:litestream 全段弃,A 方案移 HERMES_HOME 出 bucket)
# ─────────────────────────────────────────────────────────────
wait_for_mount() {
  local key_pkg="$APP_DIR/libs/storage/__init__.py"
  local key_app="$APP_DIR/app/main.py"
  local key_cfg="$APP_DIR/scripts/config.yaml.template"
  local key_plugin="$APP_DIR/scripts/plugins/nexus-r2/dashboard/manifest.json"
  local waited=0
  while [ $waited -lt 30 ]; do
    if [ -f "$key_pkg" ] && [ -f "$key_app" ] \
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

# ── Hermes Agent 内核永续层:HERMES_HOME + 插件双目录 + config ──
# ★2026-08-05 治本方案 A(根因实证):
#   HFM State Space `sonoke/h` 长期 "database disk image is malformed" 致 dashboard chat
#   连续 "No reply"。根因 = /data 实为 HF Bucket mount(FUSE/Xet 后端,非 ext4)+ litestream
#   replicate 旁路进程并发读 state.db WAL → SQLite-被-sidebar-进程 corruption(同构雷:Tropy/
#   OneDrive/sync-folder SQLite 官方实证:SQLite 不允许他进程并发改文件)。
#   hermes 原生畸形自愈跑过(`_try_runtime_fts_rebuild`)但 retry 仍 malformed = 库整体损。
# 治本:HERMES_HOME 移出 bucket FUSE,放容器本地盘 /opt/data(本地盘 ext4/overlay,WAL 正常稳定,
#   无 FUSE 后端无旁路进程)。跟两实战项目一致:HermesFace + HuggingMes 都用 /opt/data 本地 SQLite,
#   都不挂 bucket 都无 litestream → 0 malformed 报告。
# 代价:重启丢 dashboard 会话历史(transient state.db ephemeral);核心四表 agent_states/
#   task_logs/long_memory/skills_index 在 Supabase+R2 双写(persist_to_r2.py)不丢,AI 长期记忆不丢。
# state.db 仅管 dashboard 会话历史索引,非 AI 记忆源。
# fs-type 零臆断验证:keepalive.py boot 期 df -T /opt/data /data → 日志坐实 /opt/data ext4 ext4/overlay。
mkdir -p /opt/data 2>/dev/null || echo "[start] WARN: mkdir /opt/data failed"
export HERMES_HOME="${HERMES_HOME:-/opt/data/.hermes}"
mkdir -p "$HERMES_HOME/plugins" 2>/dev/null || echo "[start] WARN: mkdir $HERMES_HOME failed"
mkdir -p "${HERMES_HOME}/home" 2>/dev/null || echo "[start] WARN: mkdir ${HERMES_HOME}/home failed"
# hermes get_subprocess_home()(hermes_constants.py:886)容器内自动设 subprocess HOME={HERMES_HOME}/home;
#   HERMES_HOME 与 OS HOME(=/home/user)刻意分离(L859-861 注释:HERMES_HOME scopes Hermes state,
#   HOME 为 OS user)。我们 mkdir 此目录保子进程 HOME 落地有路径。

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

# ── hermes home 关键文件持久层(★2026-08-07 全面持久补全,Bucket 路) ──
# A 方案把 HERMES_HOME 移 /opt/data 本地盘治 state.db malformed,代价=重启清盘丢:
#   - .env(dashboard "Credentials" 写的 channel token,hermes 写此非 HF Secrets)
#   - SOUL.md(个体人设 prompt_builder.py:1326 装入 system prompt)
#   - memories/MEMORY.md + memories/USER.md(个体记忆)
#   - config.yaml(dashboard 设置项;★上面 cp 改"缺才 cp"防 start.sh 覆,但 /opt/data 仍清盘)
# restore_home_files.py boot 期(hermes 起前)从 Bucket home-backups/ 拉回 → 落 HERMES_HOME。
# ★须在 config cp 模板前:拉回 config.yaml 后 cp "缺才不覆" 逻辑才优先用拉回的,
#   非首种 template(否则拉回的 config 被 template 覆丢 dashboard 设置)。
# home_files_uploader.py nohup 后台周期(默认 600s,文件改不频繁)推回 Bucket(增量 mtime+size 跳)。
# 凭证同 state 双脚本:HF_TOKEN + HF_OWNER + NEXUS_LOGIC_BUCKET 三 env 缺则脚本内自降级 no-op 不阻断 boot。
if [ -f "$APP_DIR/scripts/restore_home_files.py" ]; then
  python "$APP_DIR/scripts/restore_home_files.py" 2>&1 | sed 's/^/[start] /'
fi

# config.yaml 从模板生成(K 形态:platform_toolsets 三行 + plugins.enabled 两 plugin
#   + disabled_toolsets + database.journal_mode:wal)。
# ★2026-08-07 改"缺才 cp"(治"dashboard 设置无法保存"):
#   旧逻辑=start.sh 每启动 cp template→config.yaml 覆盖 dashboard 在 UI 改的 yaml 级设置
#   (model 参数/auxiliary timeout/plugins toggle 等)。dashboard PUT /api/config(web_server.py:1190)
#   → save_config()(config.py:4499)→ $HERMES_HOME/config.yaml;但 restart 触 start.sh 覆回
#   template → 用户改的丢。改成:本地无 config.yaml 才 cp template 首种;已有则保留(保 dashboard 改动)。
# template 升级配套(template 改 config 项不再自动 sync 旧 config):置 FORCE_TEMPLATE_APPLY=1
#   强制 cp(template 升级时用户在 HF Secrets 临时加此 env 触发一次强制覆盖,升级后删)。
# ★注意:config.yaml 本地盘 /opt/data 重启清盘仍丢(A 方案代价),故 restore_home_files.py
#   boot 期从 Bucket home-backups/ 拉回(见下);本 cp 仅"本地无 + 无 Bucket 拉回"首种 case 兜底。
if [ -f "$APP_DIR/scripts/config.yaml.template" ]; then
  if [ -n "${FORCE_TEMPLATE_APPLY:-}" ]; then
    cp "$APP_DIR/scripts/config.yaml.template" "$HERMES_HOME/config.yaml"
    echo "[start] config.yaml FORCE overwritten from template (FORCE_TEMPLATE_APPLY set for upgrade)"
  elif [ ! -f "$HERMES_HOME/config.yaml" ]; then
    cp "$APP_DIR/scripts/config.yaml.template" "$HERMES_HOME/config.yaml"
    echo "[start] config.yaml seeded from template (first boot, no existing config)"
  else
    echo "[start] config.yaml retained (dashboard edits preserved; FORCE_TEMPLATE_APPLY=1 force overwrite)"
  fi
else
  echo "[start] WARN: config.yaml.template missing, $HERMES_HOME/config.yaml not seeded"
fi

# A 方案后 litestream 全段弃(state.db 移出 bucket FUSE 本地盘,无旁路进程干扰 WAL,
#   SQLite 复一致稳定;持久化靠 persist_to_r2.py 核心四表 Supabase+R2,不靠 state.db 快照)。

echo "[start] replay packages..."
python "$APP_DIR/scripts/replay_packages.py" replay || echo "[start] replay skipped (no log yet)"

# ── state.db 会话历史持久层(★2026-08-06 A 方案补全,Bucket 路非 Dataset) ──
# A 方案把 HERMES_HOME 移 /opt/data 本地盘治 malformed,但代价=重启清盘丢 dashboard 会话历史。
# 2026-08-06 anysearch 查证:HF Storage Bucket 2026-03-10 发布/03-31 Spaces Volume 挂载 GA,
#   早于两参考项目(HermesFace 2026-04-13/HuggingMes 2026-05-03 创建),故两项目用 Dataset 非历史限制,
#   是惰性选熟悉 git endpoint。我们已有 Bucket 挂载,优先于二手参考。
# 双盘分离治本:state.db 真值源在线写 /opt/data 本地盘(WAL 稳,无 FUSE 旁路雷),Bucket 纯当离线快照仓库:
#   state_db_uploader.py 周期(默认 300s)hf buckets cp 推到 bucket/state-backups/state.db
#   (覆写无 git history 累积,优于 Dataset 300s 周期天 288 commit 膨胀需 squash);restore_state.py 首启从该 path 拉回。
#   两盘分开=旧 malformed 雷根因(bucket FUSE+litestream 并发改 WAL)消除。
# 与 persist_to_r2.py(Supabase→R2 四结构化表)正交:本层只管 state.db 整库快照(会话历史索引),
#   不重复 R2 那套(核心四表 agent_states/task_logs/long_memory/skills_index 已在 R2+Supabase)。
# 拉回须在 hermes boot 前(boot 期 hermes 起 state.db 写锁,先拉避免抢锁);uploader nohup 后台并行。
# 凭证:HF_TOKEN + HF_OWNER + NEXUS_LOGIC_BUCKET(三 env 与 bootstrap_from_bucket/sync-logic-bucket 同源;
#   HF Space Secrets 补齐 HF_OWNER/NEXUS_LOGIC_BUCKET,不补则脚本自降级 no-op 不阻断 boot,会话历史重启后丢但 hermes 照起)。
if [ -f "$APP_DIR/scripts/restore_state.py" ]; then
  python "$APP_DIR/scripts/restore_state.py" 2>&1 | sed 's/^/[start] /'
fi

# 后台：Supabase→R2 双写快照（如配置了凭证才起）
if [ -n "${SUPABASE_URL:-}" ]; then
  echo "[start] persist daemon up"
  nohup python "$APP_DIR/scripts/persist_to_r2.py" >"$LOG_DIR/persist.log" 2>&1 &
fi

# 后台：state.db → HF Bucket 快照(会话历史持久,防重启丢)
# 缺 HF_TOKEN/HF_OWNER/NEXUS_LOGIC_BUCKET 任一则跳(脚本内亦自降级 no-op,不阻断 boot)。
if [ -n "${HF_TOKEN:-}" ] && [ -n "${HF_OWNER:-}" ] && [ -n "${NEXUS_LOGIC_BUCKET:-}" ]; then
  echo "[start] state-db upload daemon up (→ bucket ${HF_OWNER}/${NEXUS_LOGIC_BUCKET}/state-backups)"
  nohup python "$APP_DIR/scripts/state_db_uploader.py" >"${LOG_DIR:-/opt/data/logs}/state-upload.log" 2>&1 &
else
  echo "[start] state-db upload daemon skip (need HF_TOKEN+HF_OWNER+NEXUS_LOGIC_BUCKET)"
fi

# 后台：hermes home 关键文件 → HF Bucket 快照(.env/SOUL.md/memories/config.yaml,防重启丢)
# 与 state_db_uploader 同 env 门控;interval 默认 600s(文件改不频繁,比 state.db 300s 低频)。
# 增量:逐文件 mtime+size 未变跳,省 HF rate limit。
if [ -n "${HF_TOKEN:-}" ] && [ -n "${HF_OWNER:-}" ] && [ -n "${NEXUS_LOGIC_BUCKET:-}" ]; then
  echo "[start] home-files upload daemon up (→ bucket ${HF_OWNER}/${NEXUS_LOGIC_BUCKET}/home-backups)"
  nohup python "$APP_DIR/scripts/home_files_uploader.py" >"${LOG_DIR:-/opt/data/logs}/home-upload.log" 2>&1 &
else
  echo "[start] home-files upload daemon skip (need HF_TOKEN+HF_OWNER+NEXUS_LOGIC_BUCKET)"
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
  [ -f "$APP_DIR/app/main.py" ] || { echo "[start] main.py still missing, rebootstrap..."; bootstrap_from_bucket || true; }
  sleep 5
done
