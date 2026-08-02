#!/usr/bin/env bash
# 把 hermes 逻辑层真源推 HF Storage Bucket(rw /data 挂载点)。
# 永续改造核心入口:改逻辑后跑此脚本 + Space Settings Restart(用缓存镜像,永不 git push HF)。
#
# 真源沿用 git 内:
#   spaces/hermes/app/      +  spaces/hermes/scripts/  +  根 libs/
# 平铺进 Bucket nexus-logic/{app,scripts,libs},挂载点 /data → 容器 /data/{app,scripts,libs}
#
# 用法:
#   HF_TOKEN=xxx HF_OWNER=your-hf-name bash scripts/sync-logic-bucket.sh            # 推送(默认 --delete 镜像真源)
#   HF_TOKEN=xxx HF_OWNER=your-hf-name bash scripts/sync-logic-bucket.sh --dry-run  # 预览不执行
#   HF_TOKEN=xxx HF_OWNER=your-hf-name bash scripts/sync-logic-bucket.sh --verify   # 拉 Bucket 对比本地真源
#
# 前提:hf CLI 已装(pipx install huggingface_hub[cli]);HF_TOKEN 有写 nexus-logic bucket 权;bucket 已 create。
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HERMES="$ROOT/spaces/hermes"
LIBS="$ROOT/libs"
BUCKET_NAME="${NEXUS_LOGIC_BUCKET:-nexus-logic}"

: "${HF_TOKEN:?需 HF_TOKEN env(https://huggingface.co/settings/tokens, 需写权限)}"
: "${HF_OWNER:?需 HF_OWNER env(HF 用户名/组织,即 bucket 的 namespace)}"

DEST="hf://buckets/${HF_OWNER}/${BUCKET_NAME}"
FLAGS=(--token "$HF_TOKEN")

# 解析参数
MODE="push"
case "${1:-}" in
  --dry-run)   FLAGS+=(--dry-run);;
  --verify)    MODE="verify";;
  "")          FLAGS+=(--delete);;
  *)           echo "未知参数: $1(支持 --dry-run / --verify / 空=推送)"; exit 2;;
esac

# 临时暂存目录(平铺三子目录,排除 __pycache__)
STAGE="$(mktemp -d)"
trap 'rm -rf "$STAGE"' EXIT
mkdir -p "$STAGE/app" "$STAGE/scripts" "$STAGE/libs"

echo "[sync] staging hermes 逻辑真源 → $STAGE ..."
# rsync 式复制排除 __pycache__;无 rsync 时降级 cp -r 后清
stage_dir() {
  local src="$1" dst="$2"
  if command -v rsync >/dev/null 2>&1; then
    rsync -a --delete --exclude='__pycache__' "$src/" "$dst/"
  else
    cp -r "$src/." "$dst/"
    find "$dst" -type d -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
  fi
}
stage_dir "$HERMES/app"     "$STAGE/app"
stage_dir "$HERMES/scripts" "$STAGE/scripts"
stage_dir "$LIBS"           "$STAGE/libs"

echo "[sync] staged: app + scripts + libs (排除 __pycache__)"
find "$STAGE" -type f | wc -l | xargs echo "[sync] 文件数:"

if [ "$MODE" = "push" ]; then
  echo "[sync] → $DEST (flags 含 --token <脱敏>,不回显)"
  hf buckets sync "$STAGE/." "$DEST" "${FLAGS[@]}"
  echo "[sync] 完成。Space Settings → Restart 即生效(不触发 rebuild,不触发付费墙)。"
  exit 0
fi

# ── verify 模式:拉 Bucket 到 tmp,对比本地真源 ──────────────
VERIFY_DIR="$(mktemp -d)"
trap 'rm -rf "$STAGE" "$VERIFY_DIR"' EXIT
echo "[verify] 拉取 $DEST → $VERIFY_DIR 对比本地真源 ..."
hf buckets sync "$DEST" "$VERIFY_DIR/" --token "$HF_TOKEN" --no-delete
if diff -rq "$STAGE" "$VERIFY_DIR" --exclude="__pycache__" >/dev/null 2>&1; then
  echo "[verify] ✓ Bucket 与本地真源一致"
  exit 0
else
  echo "[verify] ✗ Bucket 与本地真源不一致(diff 见下):"
  diff -rq "$STAGE" "$VERIFY_DIR" --exclude="__pycache__" 2>&1 | head -30 || true
  exit 1
fi
