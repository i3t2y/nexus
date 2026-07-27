#!/usr/bin/env bash
# 把 libs/ 复制进每个 Space 目录，便于各 Space 独立 build context。
# 用法：
#   bash scripts/sync-spaces.sh           # 实际同步（rm -rf + cp）
#   bash scripts/sync-spaces.sh --check   # 仅校验：对比 root libs vs 各 Space libs，
#                                          #   不一致 → exit 1；用于 CI 闸门防 push 旧库
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIBS="$ROOT/libs"
SPACES=(hermes langgraph claude-code codex)
MODE="sync"

if [ "${1:-}" = "--check" ]; then
  MODE="check"
fi

# 同步时先清运行残留的 __pycache__，避免污染对比
clean_pycache() {
  find "$ROOT/spaces" "$LIBS" -path '*/__pycache__/*' -prune -exec rm -rf {} + 2>/dev/null || true
}

if [ "$MODE" = "sync" ]; then
  for s in "${SPACES[@]}"; do
    dst="$ROOT/spaces/$s/libs"
    rm -rf "$dst"
    cp -r "$LIBS" "$dst"
    echo "synced libs -> spaces/$s/libs"
  done
  # 删每个 Space libs 里可能携带的 __pycache__
  clean_pycache
  echo done
  exit 0
fi

# ── check 模式 ──────────────────────────────────────────────
clean_pycache
echo "[check] 比对 root libs 与各 Space libs（含内容）..."
fail=0
for s in "${SPACES[@]}"; do
  dst="$ROOT/spaces/$s/libs"
  if [ ! -d "$dst" ]; then
    echo "  ✗ spaces/$s/libs 不存在（从未同步；先跑 sync-spaces.sh）"
    fail=1
    continue
  fi
  # -r 递归 -q 仅在差异时报文件名（静默=一致）；
  # 对比排除 __pycache__（运行残留，与库内容无关）
  # 先跑 git 不可见文件过滤：用 diff --brief 逐文件
  diffout=$(diff -rq "$LIBS" "$dst" --exclude="__pycache__" 2>/dev/null || true)
  # diff 命中差异会同时回 exit 1 + 列文件；额外排除常见噪声
  diffout=$(echo "$diffout" | grep -v "__pycache__" | grep -v "^Only in.*__pycache__" || true)
  if [ -n "$diffout" ]; then
    echo "  ✗ spaces/$s/libs 与 root libs 不一致："
    echo "$diffout" | sed 's/^/      /'
    fail=1
  else
    echo "  ✓ spaces/$s/libs 与 root libs 一致"
  fi
done

if [ "$fail" -ne 0 ]; then
  echo "[check] 失败：存在不一致，请先跑：bash scripts/sync-spaces.sh"
  exit 1
fi
echo "[check] 通过：4 Space libs 与 root libs 完全一致"
exit 0
