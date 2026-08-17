#!/bin/sh
# ══════════════════════════════════════════════════════════════════
# [骨架留档·2026-07-29 标注] 本件为 omn 血统 start.sh 自愈编排模板,
# 非 Nexus 现役代码。Nexus hermes 现役 start.sh 走 hf buckets sync 拉真
# Bucket(commit 4fc098e,见 spaces/hermes/start.sh),非本件 hf download+cp 可写副本路径。
# 本件含 omn 三大设计要点可速查借鉴:
#   ① §1 环境自愈(GHCR base 预装则跳过裸上游 apt+pip+curl 补装,fallback 兜底);
#   ② §3 竞速根治=先取 Dataset HEAD commit_id 锁(list_repo_commits 首个 commit_id
#      + --revision 锁同点全件)防"boot vs sync-logic push 竞速"拉旧池;
#   ③ hf download + cp -a 出 /logic 可写副本(omn 现役非只读 Volume 挂载,
#      只读 Volume 是 audit 已准未施蓝图)。
# Nexus 若移植须注意:此走 Dataset repo(rev=Dataset HEAD),Nexus 现役 a142da9 走
# Storage Bucket rw /data(hf buckets sync),两路径挂载/同步语义相异(见 journals/最强模板 §1)。
# ══════════════════════════════════════════════════════════════════
# Nexus 集群通用 start.sh — 三件定态, 永不再改
# 与逻辑层唯一契约: Dataset 根必须存 entrypoint.sh
# ══════════════════════════════════════════════════════════════════
set -e
echo "[start] 启动 $(date '+%F %T') component=${NEXUS_COMPONENT:-(未注入)}"
echo "[start] 基础镜像: ${BASE_IMAGE:-(未注入 ENV)}"

# ── 1. 环境自愈 (GHCR base 预装则跳过; 裸上游则 apt+pip+curl 补装) ──
_need_install=0
for t in python3 curl pip3 jq sqlite3; do command -v "$t" >/dev/null 2>&1 || _need_install=1; done
command -v litestream >/dev/null 2>&1 || _need_install=1
{ command -v hf >/dev/null 2>&1 || command -v huggingface-cli >/dev/null 2>&1; } || _need_install=1

if [ "$_need_install" = "1" ]; then
  command -v apt-get >/dev/null 2>&1 || { echo "[start] FATAL: 非 Debian 系"; exit 1; }
  apt-get update -qq && apt-get install -y --no-install-recommends \
    curl jq python3 python3-pip sqlite3 ca-certificates build-essential && \
    rm -rf /var/lib/apt/lists/*
  pip3 install --no-cache-dir --break-system-packages \
    "huggingface_hub${HF_HUB_RANGE:->=1.0,<2.0}"
  # litestream 拉取 (GHCR base 已预装则此段跳过)
  _ls_v="${LITESTREAM_VERSION:-0.5.9}"
  _arch=$(uname -m | sed 's/aarch64/arm64/;s/x86_64/amd64/')
  curl -fsSL "https://github.com/benbjohnson/litestream/releases/download/v${_ls_v}/litestream-${_ls_v}-linux-${_arch}.tar.gz" \
    | tar -xz -C /usr/local/bin litestream && chmod +x /usr/local/bin/litestream
fi

echo "[start] 环境就绪: litestream=$(litestream version 2>/dev/null||echo n/a) component=${NEXUS_COMPONENT}"

# ── 2. 变量校验 (HF_TOKEN 可选: 公共 Dataset 无需令牌) ──
[ -n "$LOGIC_BUCKET_REPO" ] || { echo "[start] FATAL: 缺 LOGIC_BUCKET_REPO"; exit 1; }

# ── 3. 拉取逻辑层 (竞速根治: 先锁 Dataset HEAD commit_id 再按 revision 拉) ──
mkdir -p /tmp/logic
_rev=$(LOGIC_BUCKET_REPO="$LOGIC_BUCKET_REPO" python3 -c '
import os, sys
try:
    from huggingface_hub import HfApi
    commits = list(HfApi().list_repo_commits(os.environ["LOGIC_BUCKET_REPO"], repo_type="dataset"))
    print(commits[0].commit_id)
except Exception as e:
    sys.stderr.write(f"[start] WARN: HEAD resolve失败 回退main: {e}
")
' 2>/tmp/start_rev.err) || true

_tk=""; [ -n "$HF_TOKEN" ] && _tk="--token $HF_TOKEN"
_rev_arg=""; [ -n "$_rev" ] && _rev_arg="--revision $_rev"
echo "[start] 拉取逻辑层 repo=${LOGIC_BUCKET_REPO} rev=${_rev:-main}"

hf download "$LOGIC_BUCKET_REPO" --repo-type dataset \
  --local-dir /tmp/logic $_tk $_rev_arg --quiet \
  || { echo "[start] FATAL: 逻辑层拉取失败"; cat /tmp/start_rev.err 2>/dev/null; exit 1; }

mkdir -p /logic && cp -a /tmp/logic/. /logic/ && \
  chmod +x /logic/*.sh 2>/dev/null || true
rm -rf /tmp/logic

echo "[start] 逻辑层就绪 → exec /logic/entrypoint.sh"
exec /logic/entrypoint.sh
