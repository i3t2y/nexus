#!/bin/bash
# ══════════════════════════════════════════════════════════════════
# mem0 server 启动薄引导 — 三文件之一, 冻结不改
# 参考 nexus/n-omn 永续架构: start.sh 只做 拉Dataset + exec entrypoint.sh
# 真业务逻辑在 Dataset (entrypoint.sh + run.py + patches/) 改 Dataset 不触 Rebuild
# ══════════════════════════════════════════════════════════════════
set -e

echo "[start] mem0 server starting $(date '+%F %T')"

# ── 1. 环境自愈 (Dockerfile 已预装则跳过; 兜底补装) ──
_need_install=0
for t in python3 curl pip3; do
  command -v "$t" >/dev/null 2>&1 || { _need_install=1; break; }
done

if [ "$_need_install" = "1" ]; then
  echo "[start] 环境自愈: 补装基础工具..."
  apt-get update -qq && apt-get install -y --no-install-recommends \
    curl python3 python3-pip ca-certificates && rm -rf /var/lib/apt/lists/*
  pip3 install --no-cache-dir --break-system-packages "huggingface_hub>=1.0,<2.0"
  echo "[start] 环境补全完成"
fi

# ── 2. 拉取逻辑层 (从 HF Bucket nmem/logic 挂载的 /data 复制) ──
# 三件套统一 Bucket: 版本化走 GitHub→Actions→Bucket, 运行时 /data mount 直接读
echo "[start] 拉取逻辑层: cp /data → /app/worker"
mkdir -p /app/worker

if [ -d /data ] && ls /data/* >/dev/null 2>&1; then
  cp -r /data/* /app/worker/ 2>/dev/null || cp -r /data /app/worker/ 2>/dev/null || {
    echo "[start] FATAL: /data 复制失败"
    ls -la /data/ 2>&1
    exit 1
  }
  echo "[start] 逻辑层已复制到 /app/worker"
else
  echo "[start] FATAL: /data 未挂载或为空 (Bucket nmem/logic 未正确挂载)"
  ls -la /data/ 2>&1
  exit 1
fi

# Ensure history directory exists (mem0 SQLiteManager needs it)
mkdir -p /app/history

# ── 3. 移交控制权给 entrypoint.sh ──
echo "[start] 移交控制权给 entrypoint.sh"
chmod +x /app/worker/entrypoint.sh 2>/dev/null || true
exec /app/worker/entrypoint.sh
