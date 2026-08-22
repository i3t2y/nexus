#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# hermes 真启动逻辑(Bucket 真源,方案 C 重构 2026-08-09)
# 此文件在 spaces/hermes/scripts/real-start.sh,sync-logic-bucket.sh
#   L54 stage_dir 自动推到 Bucket scripts/real-start.sh。镜像内 thin
#   start.sh 等挂载就绪后 `source` 本文件(继承 thin 的 APP_DIR/PYTHONPATH/
#   bootstrap_from_bucket 函数 + HF_TOKEN 等 env)。改本文件 → sync+Restart,
#   不触 Space rebuild(镜像内 thin 永不动 = 真墓碑)。
# 来源:原 start.sh L74-234 业务逻辑剥离(引导段留 thin)。
# K 形态:主进程 app.main:boot + 双 daemon thread(gateway + dashboard :7860)。
# ─────────────────────────────────────────────────────────────
# 防御:source 调用时 thin 已设 APP_DIR/PYTHONPATH/HERMES_HOME,但若独立调用补全。
#   set -u 在 thin 已设;source 同 shell 继承。补设防独立 debug 跑。
set -u 2>/dev/null || true
export PATH="$HOME/.local/bin:${PATH:-}"
export PYTHONUNBUFFERED=1
export PYTHONPATH="/data/libs:${PYTHONPATH:-}"
APP_DIR="${HERMES_APP_DIR:-/data}"
LOG_DIR="${HERMES_LOG_DIR:-/opt/data/logs}"
HERMES_HOME="${HERMES_HOME:-/opt/data/.hermes}"

# bootstrap_from_bucket 由 thin source 前定义;独立调用补定义(boot while 循环 rebootstrap 用)
if ! command -v bootstrap_from_bucket >/dev/null 2>&1; then
  bootstrap_from_bucket() {
    local token="${HF_TOKEN:-}" owner="${HF_OWNER:-}" bucket="${NEXUS_LOGIC_BUCKET:-nexus-logic}"
    [ -z "$token" ] && { echo "[real-start] bootstrap skip: no HF_TOKEN"; return 1; }
    [ -z "$owner" ] && { echo "[real-start] bootstrap skip: no HF_OWNER"; return 1; }
    command -v hf >/dev/null 2>&1 || { echo "[real-start] bootstrap skip: no hf CLI"; return 1; }
    echo "[real-start] bootstrap: pulling bucket ${owner}/${bucket} → $APP_DIR ..."
    HF_TOKEN="$token" hf buckets sync "hf://buckets/${owner}/${bucket}" "$APP_DIR/" --no-delete \
      || { echo "[real-start] bootstrap failed"; return 1; }
    echo "[real-start] bootstrap OK"
  }
fi

# ── 挂载就绪后建日志目录 + HERMES_HOME(/opt/data 本地盘,WAL 稳治 malformed) ──
# A 方案(2026-08-05 治本):HERMES_HOME 移出 bucket FUSE 放容器本地盘 /opt_data。
#   根因=/data 实为 HF Bucket mount(FUSE/Xet)+ litestream 旁路并发改 state.db WAL
#   → SQLite corruption(官方实证 SQLite 不允许他进程并发改文件)。本地盘 ext4
#   无 FUSE 无旁路 → WAL 稳定。两实战项目(HermesFace+HuggingMes)同用 /opt/data 本地 SQLite 0 malformed。
# 代价:重启丢 dashboard 会话历史(transient state.db);核心四表(agent_states/
#   task_logs/long_memory/skills_index)在 Neon(persist_to_neon.py 主路)+ R2 快照
#   (persist_to_r2.py 副路,2026-08-18 恢复读源=Neon)双写不丢,AI 长期记忆不丢。
mkdir -p "$LOG_DIR" 2>/dev/null || echo "[real-start] WARN: mkdir $LOG_DIR failed (mount not ready?)"
mkdir -p /opt/data 2>/dev/null || echo "[real-start] WARN: mkdir /opt/data failed"
export HERMES_HOME="$HERMES_HOME"
mkdir -p "$HERMES_HOME/plugins" 2>/dev/null || echo "[real-start] WARN: mkdir $HERMES_HOME/plugins failed"
mkdir -p "${HERMES_HOME}/home" 2>/dev/null || echo "[real-start] WARN: mkdir ${HERMES_HOME}/home failed"
# hermes get_subprocess_home()(hermes_constants.py:886)容器内自动设 subprocess HOME={HERMES_HOME}/home;
#   HERMES_HOME 与 OS HOME(=/home/user)刻意分离(L859-861:HERMES_HOME scopes state,HOME 为 OS user)。
# mkdir 此目录保子进程 HOME 落地有路径。

# nexus plugin 从 Bucket 逻辑层拷到 HERMES_HOME/plugins(K1 决策-3 插件目录)
#   - nexus-r2: R2 文件 CRUD dashboard tab (+ 原三下游 bridge tool 已随收口取消)
#   - nexus-ops: 2026-08-22 删。探活下游三 Space(已取消) + 只读查废弃 Supabase 四表 → 归 old/
# hermes 用户插件目录 = $HERMES_HOME/plugins(hermes_cli/plugins.py 扫此 + web_server dashboard discovery 扫此)
# ★2026-08-09 方案 C:plugin source 在 $APP_DIR/scripts/plugins/(Bucket 挂载后才有)。
for pname in nexus-r2; do
  if [ -d "$APP_DIR/scripts/plugins/$pname" ]; then
    rm -rf "$HERMES_HOME/plugins/$pname" 2>/dev/null
    cp -r "$APP_DIR/scripts/plugins/$pname" "$HERMES_HOME/plugins/" \
      && echo "[real-start] $pname plugin staged → $HERMES_HOME/plugins/$pname" \
      || echo "[real-start] WARN: stage $pname plugin failed"
  else
    echo "[real-start] WARN: $pname plugin source missing at $APP_DIR/scripts/plugins/$pname"
  fi
done
# 清 pycache(避免旧版名 nexus 残留字节码)
find "$HERMES_HOME/plugins" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true

# ── hermes home 关键文件持久层(★2026-08-07 全面持久补全,Bucket 路) ──
# A 方案代价=重启 /opt/data 清盘丢:.env/SOUL.md/memories/config.yaml。
# restore_home_files.py boot 期(hermes 起前)从 Bucket home-backups/ 拉回 → 落 HERMES_HOME。
# ★须在 config cp 模板前:拉回 config.yaml 后 cp "缺才不覆" 逻辑才优先用拉回的,
#   非首种 template(否则拉回的 config 被 template 覆丢 dashboard 设置)。
# home_files_uploader.py nohup 后台周期(默认 600s)推回 Bucket(增量 mtime+size 跳)。
# 凭证:HF_TOKEN + HF_OWNER + NEXUS_LOGIC_BUCKET 三 env 缺则脚本内自降级 no-op 不阻断 boot。
if [ -f "$APP_DIR/scripts/restore_home_files.py" ]; then
  python "$APP_DIR/scripts/restore_home_files.py" 2>&1 | sed 's/^/[real-start] /'
fi

# config.yaml 从模板生成(K 形态:platform_toolsets 三行 + plugins.enabled 两 plugin
#   + disabled_toolsets + database.journal_mode:wal)。
# ★2026-08-07 "缺才 cp"(治"dashboard 设置无法保存"):
#   本地无 config.yaml 才 cp template 首种;已有则保留(保 dashboard 改动)。dashboard PUT
#   /api/config → save_config() → $HERMES_HOME/config.yaml;restart 触 start.sh 覆回 template 会丢。
#   FORT_TEMPLATE_APPLY=1 强制 cp(template 升级时 HF Secrets 临时加触一次,升级后删)。
# ★config.yaml 本地盘 /opt/data 重启清盘丢(A 方案代价),故 restore_home_files.py boot 期
#   从 Bucket home-backups 拉回;本 cp 仅"本地无 + 无 Bucket 拉回"首种 case 兜底。
if [ -f "$APP_DIR/scripts/config.yaml.template" ]; then
  if [ -n "${FORCE_TEMPLATE_APPLY:-}" ]; then
    cp "$APP_DIR/scripts/config.yaml.template" "$HERMES_HOME/config.yaml"
    echo "[real-start] config.yaml FORCE overwritten from template (FORCE_TEMPLATE_APPLY set for upgrade)"
  elif [ ! -f "$HERMES_HOME/config.yaml" ]; then
    cp "$APP_DIR/scripts/config.yaml.template" "$HERMES_HOME/config.yaml"
    echo "[real-start] config.yaml seeded from template (first boot, no existing config)"
  else
    echo "[real-start] config.yaml retained (dashboard edits preserved; FORCE_TEMPLATE_APPLY=1 force overwrite)"
  fi
else
  echo "[real-start] WARN: config.yaml.template missing, $HERMES_HOME/config.yaml not seeded"
fi

# A 方案后 litestream 全段弃(state.db 移出 bucket FUSE 本地盘,无旁路进程干扰 WAL,
#   SQLite 复一致稳定;持久化靠 persist_to_neon.py 核心四表主路 + persist_to_r2.py
#   R2 快照副路(2026-08-18 恢复读源=Neon),不靠 state.db 快照)。

echo "[real-start] replay packages..."
python "$APP_DIR/scripts/replay_packages.py" replay || echo "[real-start] replay skipped (no log yet)"

# ── state.db 会话历史持久层(★2026-08-06 A 方案补全,Bucket 路非 Dataset) ──
# 双盘分离治本:state.db 真值源在线写 /opt/data 本地盘(WAL 稳),Bucket 纯当离线快照仓库:
#   state_db_uploader.py 周期(默认 300s)hf buckets cp 推 bucket/state-backups/state.db;
#   restore_state.py 首启从该 path 拉回。两盘分开=旧 malformed 雷(FUSE+litestream 并发改 WAL)消除。
# 与 persist_to_neon.py(Neon 四结构化表主路)+ persist_to_r2.py(Neon→R2 快照副路,
#   2026-08-18 恢复读源=Neon)正交:本层只管 state.db 整库快照(会话历史索引)。
# 拉回须在 hermes boot 前(boot 期 hermes 起 state.db 写锁,先拉避免抢锁)。
# 凭证 HF_TOKEN+HF_OWNER+NEXUS_LOGIC_BUCKET(不补则脚本自降级 no-op,会话历史重启丢但 hermes 照起)。
if [ -f "$APP_DIR/scripts/restore_state.py" ]; then
  python "$APP_DIR/scripts/restore_state.py" 2>&1 | sed 's/^/[real-start] /'
fi

# ── R2 快照恢复(★2026-08-22 snapshots/<ts>/ 不可变 blob 恢复段) ──
# 从 R2 MANIFEST.json → objects.*.key → 下载 → sha256 校验 → upsert 回 Neon。
# 门控:R2_ENDPOINT + R2_ACCESS_KEY_ID + POSTGRES_HOST 三 presence。
# 仅在首启(无 state.db 或显式 RESTORE_FROM_R2=1)时执行,避免每次重启覆盖 Neon 最新数据。
# 幂等:ON CONFLICT 主键覆盖,重跑安全。
if [ -n "${R2_ENDPOINT:-}" ] && [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${POSTGRES_HOST:-}" ]; then
  if [ "${RESTORE_FROM_R2:-0}" = "1" ] || [ ! -f "$HERMES_HOME/.restore_done" ]; then
    echo "[real-start] r2 restore from R2 snapshots (RESTORE_FROM_R2=${RESTORE_FROM_R2:-0})"
    if [ -f "$APP_DIR/scripts/restore_from_r2.py" ]; then
      python "$APP_DIR/scripts/restore_from_r2.py" --all 2>&1 | sed 's/^/[real-start] /'
      touch "$HERMES_HOME/.restore_done"
    fi
  else
    echo "[real-start] r2 restore skip (already done, set RESTORE_FROM_R2=1 to force)"
  fi
else
  echo "[real-start] r2 restore skip (need R2_ENDPOINT+R2_ACCESS_KEY_ID+POSTGRES_HOST)"
fi

# 后台:Neon 持久化同步主路(2026-08-22 去心跳版)
# 2026-08-22 收口版合同: 移除定时 SQL 心跳, 让 Neon 自然休眠。
# persist_to_neon.py --init: boot 期一次幂等建表 + 连接测试, 不跑 daemon。
# 四表(agent_states/task_logs/long_memory/skills_index)由 hermes 进程内直接写
# (persist_to_neon.py 2026-08-17 重构后实为健康检查 daemon, 2026-08-22 改为 --init 模式)。
if [ -n "${POSTGRES_HOST:-}" ]; then
  echo "[real-start] persist-neon init (DDL once, no daemon)"
  python "$APP_DIR/scripts/persist_to_neon.py" --init 2>&1 | sed 's/^/[real-start] /'
else
  echo "[real-start] persist-neon skip (need POSTGRES_HOST)"
fi

# 后台:Neon → R2 快照备份副路(★2026-08-22 snapshots/<ts>/ 不可变 blob)
# R2 作灾备快照层:persist_to_r2.py 周期读 Neon 四表 → snapshots/<ts>/ 不可变 JSON +
#   MANIFEST.json(gen/ts/objects 指针,不进 Neon backup_snapshots 表)。
# restore_from_r2.py 反向闭环:读 MANIFEST.objects → 下载 → 校验 → upsert 回 Neon。
# 门控:R2_ENDPOINT + R2_ACCESS_KEY_ID + POSTGRES_HOST 三 presence 缺则 skip WARN(不 fail boot)。
#   POSTGRES_HOST 单有→只 Neon 主路 600s;加 R2_ENDPOINT→双起 R2 副路 1800s(30min,快照层低频)。
#   R2 Class A 写 4 表×1 次/周期×48 次/天 ≈ 192/天 << 免费额 1000万/月(移除了 _atomic_upload 三步法后)。
if [ -n "${R2_ENDPOINT:-}" ] && [ -n "${R2_ACCESS_KEY_ID:-}" ] && [ -n "${POSTGRES_HOST:-}" ]; then
  export R2_SYNC_INTERVAL_SEC="${R2_SYNC_INTERVAL_SEC:-1800}"
  echo "[real-start] persist-r2 snapshot daemon up (→ R2 ${R2_BUCKET:-nexus-checkpoints}/snapshots/<ts>/, source=Neon, interval=${R2_SYNC_INTERVAL_SEC}s)"
  nohup python "$APP_DIR/scripts/persist_to_r2.py" >"${LOG_DIR:-/opt/data/logs}/persist-r2.log" 2>&1 &
  PERSIST_R2_PID=$!
else
  echo "[real-start] persist-r2 snapshot daemon skip (need R2_ENDPOINT+R2_ACCESS_KEY_ID+POSTGRES_HOST)"
fi
# 缺 HF_TOKEN/HF_OWNER/NEXUS_LOGIC_BUCKET 任则跳(脚本内亦自降级 no-op)。
if [ -n "${HF_TOKEN:-}" ] && [ -n "${HF_OWNER:-}" ] && [ -n "${NEXUS_LOGIC_BUCKET:-}" ]; then
  echo "[real-start] state-db upload daemon up (→ bucket ${HF_OWNER}/${NEXUS_LOGIC_BUCKET}/state-backups)"
  nohup python "$APP_DIR/scripts/state_db_uploader.py" >"${LOG_DIR:-/opt/data/logs}/state-upload.log" 2>&1 &
  STATE_PID=$!
else
  echo "[real-start] state-db upload daemon skip (need HF_TOKEN+HF_OWNER+NEXUS_LOGIC_BUCKET)"
fi

# 后台:hermes home 关键文件 → HF Bucket 快照(.env/SOUL.md/memories/config.yaml,防重启丢)
# 与 state_db_uploader 同 env 门控;interval 默认 600s。增量:逐文件 mtime+size 未变跳。
if [ -n "${HF_TOKEN:-}" ] && [ -n "${HF_OWNER:-}" ] && [ -n "${NEXUS_LOGIC_BUCKET:-}" ]; then
  echo "[real-start] home-files upload daemon up (→ bucket ${HF_OWNER}/${NEXUS_LOGIC_BUCKET}/home-backups)"
  nohup python "$APP_DIR/scripts/home_files_uploader.py" >"${LOG_DIR:-/opt/data/logs}/home-upload.log" 2>&1 &
  HOME_PID=$!
else
  echo "[real-start] home-files upload daemon skip (need HF_TOKEN+HF_OWNER+NEXUS_LOGIC_BUCKET)"
fi

# ── mem0 记忆层 (OSSBackend pgvector, ★2026-08-22 三件套部署) ──
# 门控:MEM0_MODE=oss 才生成 mem0.json(HF Secrets 设此值即激活记忆层)。
#   - Hermes agent → OSSBackend → pgvector(pooler 短 TCP 用完即关,与 HTTP /sql 主路分离)
#   - embedder = NIM nvidia/nemotron-3-embed-1b(2048 维,2026-08-14 实证唯一账户可用 model;
#     bge-m3 对此账户返 500,故不用;hnsw:false 因 2048 超 pgvector HNSW 2000 维上限)
#   - llm = 智谱 glm-4.7-flash(${ZAI_API_KEY})提炼记忆
# env 齐检:MEM0_PG_URI(Neon 连接串) + ZAI_API_KEY(智谱) + NVIDIA_API_KEY(NIM)。缺则 WARN 不 fail boot。
# mem0.json 由模板 inline python envsubst(re.sub ${VAR} → os.environ)注入 $HERMES_HOME/mem0.json,
#   "缺才生成"模式(已有保留;FORCE_TEMPLATE_APPLY=1 强制重生成)。
# MEM0_TELEMETRY=false 禁 posthog 遥测(模块级读,须 export 进 hermes 子进程)。
if [ "${MEM0_MODE:-}" = "oss" ]; then
  export MEM0_TELEMETRY=false
  if [ -f "$APP_DIR/scripts/mem0.json.template" ]; then
    python3 - "$APP_DIR/scripts/mem0.json.template" "$HERMES_HOME/mem0.json" <<'PYEOF' 2>&1 | sed 's/^/[real-start] /'
import json, os, re, sys
tmpl_path, out_path = sys.argv[1], sys.argv[2]
if os.path.exists(out_path) and os.getenv("FORCE_TEMPLATE_APPLY", "") != "1":
    print(f"mem0.json retained (existing preserved; FORCE_TEMPLATE_APPLY=1 force)")
    sys.exit(0)
tmpl = open(tmpl_path).read()
filled = re.sub(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}",
                lambda m: os.environ.get(m.group(1), ""), tmpl)
try:
    json.loads(filled)  # JSON valid 校验
except json.JSONDecodeError as e:
    print(f"mem0.json ERROR: template envsubst produced invalid JSON ({e}); keys missing?")
    sys.exit(1)
with open(out_path, "w") as f:
    f.write(filled)
print(f"mem0.json generated ok=1 → {out_path}")
PYEOF
  else
    echo "[real-start] WARN: mem0.json.template missing, mem0 config not generated"
  fi
else
  echo "[real-start] mem0 skip (MEM0_MODE!=oss, no mem0.json generated)"
fi

# 后台:下游 Space 保活探测
# 2026-08-22 单 Space 部署: 三下游已取消, omniroute 不依赖保活。
# 默认关(KEEPALIVE_ENABLED=0); cron-job.org 单路 GET /health 15-25min 已够。
KEEPALIVE_ENABLED="${KEEPALIVE_ENABLED:-0}"
if [ "$KEEPALIVE_ENABLED" = "1" ]; then
  echo "[real-start] keepalive daemon up"
  nohup python "$APP_DIR/scripts/keepalive.py" >"$LOG_DIR/keepalive.log" 2>&1 &
  KEEPALIVE_PID=$!
fi

# ★2026-08-09 方案 C:删 dns-resolve.py dead code 段(原 start.sh L214-220 引用
#   $APP_DIR/scripts/dns-resolve.py 但该文件仓库不存在 find 实证,原段永不进 if 分支无害。
#   hermes telegram adapter 原生自带 DoH+fallback IP(tagram_network.py)是主路,无需此二线脚本。)

# ── 主服务:app.main:boot(K 形态双 daemon thread = gateway + dashboard :7860) ──
# HF 必须有进程监听 7860:dashboard daemon thread 直跑 start_server --port 7860
#   (直监听非反代;host 缺省 127.0.0.1 本地免 OAuth,生产经 DASHBOARD_BIND_HOST=0.0.0.0 + auth provider)
# gateway daemon thread 同 async loop 起 api_server adapter(API_SERVER_KEY ≥16 触发)+ IM。
# 自愈循环:boot 退出(任一 daemon thread 死)则 5 秒后重启 boot 整进。
# rebootstrap 兜底:boot 退出后若 main.py 不在调 bootstrap_from_bucket 重拉(thin source 时函数继承)。

# ── 优雅关机钩子(★2026-08-18 Gork 总裁第一步 SIGTERM 短链补全) ──
# HF 停容器发 SIGTERM。原:主进程前台卡 `python -c boot()`(阻塞)→ SIGTERM 直杀 boot
#   Python 进程,real-start.sh 主 shell 根本不收 trap(deferred 永远太晚)+ 四 daemon 裸
#   nohup & 无 signal handler → SIGTERM 直杀无最后 flush → 半截状态丢(state.db/state快照
#   可能截半 task_logs 长记忆丢末条)。
# 改:boot 改后台 & 记 BOOT_PID + `wait $BOOT_PID`(wait 可被 trap 中断)→ trap
#   on_shutdown TERM INT 触发:
#   1. kill -TERM boot 子进程(hermes 内部 gateway/dashboard daemon thread 收子进程 TERM → shutdown)
#   2. kill -TERM 三 persist daemon + keepalive(persist 各脚本已装 signal handler → flush + exit 0)
#     ★2026-08-22: persist-neon 已改 --init 模式, 无 daemon, 不在此列
#   3. sleep 10 约等 flush 完成(各 _INTERVAL 最短 300s 但信号立即中断 sleep_check 1s 粒度 → 实际 flush 启动秒级)
#   4. exit 0(HF 容器期望干净退出,restart 复活)
BOOT_PID=""
# 三 persist daemon PID + keepalive(变量未设则空串,kill 跳)
on_shutdown() {
  echo "[real-start] SIGTERM/INT recv, graceful shutdown..."
  # boot 子进程先 TERM(让 hermes 内部 shutdown daemon thread)
  if [ -n "${BOOT_PID:-}" ]; then
    kill -TERM "$BOOT_PID" 2>/dev/null
    echo "[real-start] sent TERM to boot pid=$BOOT_PID"
  fi
  # 三 persist daemon + keepalive:各脚本 _on_sigterm handler 触发最后一次 sync_once 后 exit
  for pid in "${PERSIST_R2_PID:-}" "${STATE_PID:-}" "${HOME_PID:-}" "${KEEPALIVE_PID:-}"; do
    [ -n "$pid" ] && kill -TERM "$pid" 2>/dev/null && echo "[real-start] sent TERM to daemon pid=$pid"
  done
  # 等 flush(persist 各 _sleep_check 1s 粒度响应快;10s 兜底防慢网)
  sleep 10
  echo "[real-start] shutdown complete, exit 0"
  exit 0
}

while true; do
  echo "[real-start] launching hermes boot (gateway + dashboard :7860) app-dir=$APP_DIR"
  python -c "import sys; sys.path.insert(0,'$APP_DIR'); from app.main import boot; boot()" &
  BOOT_PID=$!
  # trap 须在 wait 前 wait 锚点设:return 后复设 trap 后,while 循环重入再设(保每轮可用)
  trap on_shutdown TERM INT
  wait "$BOOT_PID" || true
  code=$?
  echo "[real-start] boot exited code=$code, recheck mount + restart in 5s..."
  [ -f "$APP_DIR/app/main.py" ] || { echo "[real-start] main.py still missing, rebootstrap..."; bootstrap_from_bucket || true; }
  sleep 5
done
