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

# ── 2. 拉取逻辑层 (竞速根治: 先锁 Dataset HEAD commit_id 再按 revision 拉) ──
# 防止 boot vs dataset push 竞速拉到旧版 (参考 n-omn start.sh)
echo "[start] 拉取逻辑层: nmem/nworker"
mkdir -p /app/worker

_rev=""
_rev_err=/tmp/.rev.err; : > "$_rev_err"
_rev=$(python3 -c '
import os, sys
try:
    from huggingface_hub import HfApi
    token = os.environ.get("HF_TOKEN", "")
    api = HfApi(token=token if token else None)
    commits = list(api.list_repo_commits("nmem/nworker", repo_type="dataset"))
    print(commits[0].commit_id)
except Exception:
    pass  # fail-open, 走 main HEAD
' 2>"$_rev_err") || true

if [ -n "$_rev" ]; then
  echo "[start] Dataset HEAD 锁定 revision=$(printf %.12s "$_rev") (竞速根治)"
else
  echo "[start] WARN: 取 HEAD commit_id 失败, 回退 main HEAD"
  [ -s "$_rev_err" ] && cat "$_rev_err" >&2
fi

# hf CLI 或 snapshot_download 拉取
_err=/tmp/.dl.err; : > "$_err"
python3 -c "
import os, sys
from huggingface_hub import snapshot_download
token = os.environ.get('HF_TOKEN', '')
rev = '$(echo $_rev | head -c 4096)' or None
try:
    snapshot_download(
        'nmem/nworker',
        repo_type='dataset',
        local_dir='/app/worker',
        token=token if token else None,
        revision=rev,
    )
    print('Runtime code pulled to /app/worker')
except Exception as e:
    print(f'FATAL: {e}', file=sys.stderr)
    sys.exit(1)
" 2>"$_err" || {
  # 脱敏后打印错误
  if [ -n "$HF_TOKEN" ]; then
    sed "s/$HF_TOKEN/[REDACTED]/g" "$_err" >&2
  else
    cat "$_err" >&2
  fi
  echo "[start] FATAL: Dataset 拉取失败"
  exit 1
}

# Ensure history directory exists (mem0 SQLiteManager needs it)
mkdir -p /app/history

# ── 3. 移交控制权给 entrypoint.sh ──
echo "[start] 移交控制权给 entrypoint.sh"
chmod +x /app/worker/entrypoint.sh 2>/dev/null || true
exec /app/worker/entrypoint.sh
