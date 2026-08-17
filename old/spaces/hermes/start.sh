#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# hermes Space 启动薄引导(方案 C 重构 2026-08-09,永不动墓碑)
# 此文件在镜像内(Dockerfile COPY)首切后永不再动。真业务逻辑在
#   scripts/real-start.sh(Bucket 真源)改它 sync+Restart 不触 rebuild。
# 设计:引导段(等挂载 + bootstrap 兜底 + source real-start)必须留镜像内,
#   因 CMD 执行瞬间 /data 可能未挂完(FUSE/Xet 异步窗口);若 CMD 直接 bash
#   /data/scripts/real-start.sh 在挂载前跑 = 文件不存在容器崩无自愈。
#   thin 内 wait_for_mount polling 兜底过该窗口 → 挂载就绪后 source real-start。
#
# ★原 start.sh L74-234 全业务逻辑已搬 scripts/real-start.sh(2026-08-09 方案 C)。
#   历史:2026-08-02 K 形态推翻重定向;2026-08-05 A 方案移 HERMES_HOME 出 bucket FUSE;2026-08-07 全面持久补全。
# ─────────────────────────────────────────────────────────────
set -u

# 用户(与 Dockerfile/base 镜像的 1000 一致);HF 注入 user
export PATH="$HOME/.local/bin:$PATH"
export PYTHONUNBUFFERED=1
export PYTHONPATH="/data/libs:${PYTHONPATH:-}"

# 打印当前镜像版本(Dockerfile ENV BASE_IMAGE 转存;运维排查用)
echo "[start] BASE_IMAGE=${BASE_IMAGE:-<unset-from-Dockerfile>}"

APP_DIR="${HERMES_APP_DIR:-/data}"

# ─────────────────────────────────────────────────────────────
# 等待 Bucket 挂载就绪(/data 仅 runtime,挂载于容器启动前完成,但 FUSE 异步窗口)
# 判断点(4 存才进 real-start):
#   /data/libs/storage/__init__.py + /data/app/main.py
#   + /data/scripts/config.yaml.template + /data/scripts/plugins/nexus-r2/dashboard/manifest.json
# ★方案 C 加第 5 判断点 /data/scripts/real-start.sh(确保 source 目标在再跳)
# 最多 30s;超时试 hf CLI bootstrap fallback 拉;仍败进 while 等待持续重试(不让容器崩)
# (★2026-08-05 删 litestream.yml 判断点:litestream 全段弃,A 方案移 HERMES_HOME 出 bucket)
# ─────────────────────────────────────────────────────────────
wait_for_mount() {
  local key_pkg="$APP_DIR/libs/storage/__init__.py"
  local key_app="$APP_DIR/app/main.py"
  local key_cfg="$APP_DIR/scripts/config.yaml.template"
  local key_plugin="$APP_DIR/scripts/plugins/nexus-r2/dashboard/manifest.json"
  local key_real="$APP_DIR/scripts/real-start.sh"
  local waited=0
  while [ $waited -lt 30 ]; do
    if [ -f "$key_pkg" ] && [ -f "$key_app" ] \
       && [ -f "$key_cfg" ] && [ -f "$key_plugin" ] && [ -f "$key_real" ]; then
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

# 首启等挂载;失败试 bootstrap;仍败持续重试(非退出,保容器存活等 HF 平台补挂)
if ! wait_for_mount; then
  bootstrap_from_bucket || true
  # bootstrap 后再 polling 至 real-start 出现(FUSE 慢挂或 bootstrap 拉完确认)
  while [ ! -f "$APP_DIR/scripts/real-start.sh" ]; do
    echo "[start] still waiting for $APP_DIR/scripts/real-start.sh after bootstrap, retry in 5s..."
    sleep 5
    # 再试 bootstrap(挂载永未达则靠 hf CLI 持续拉)
    bootstrap_from_bucket 2>/dev/null || true
  done
fi

echo "[start] sourcing $APP_DIR/scripts/real-start.sh (Bucket 真逻辑,业务段全在此)"
# source 继承 thin 的 APP_DIR/PYTHONPATH/HERMES_HOME env + bootstrap_from_bucket 函数
#   (real-start 内 boot while 循环 rebootstrap 调用)。real-start 末尾 while true 循环
#   宿主在本 thin shell 跑 → 容器不退(无条件成立)。
source "$APP_DIR/scripts/real-start.sh"
