#!/usr/bin/env bash
# 把 libs/ 复制进每个 Space 目录，便于各 Space 独立 build context。
# 用法：bash scripts/sync-spaces.sh
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LIBS="$ROOT/libs"
SPACES=(hermes langgraph claude-code codex)

for s in "${SPACES[@]}"; do
  dst="$ROOT/spaces/$s/libs"
  rm -rf "$dst"
  cp -r "$LIBS" "$dst"
  echo "synced libs -> spaces/$s/libs"
done

# 删每个 Space libs 里可能携带的 __pycache__
find "$ROOT/spaces" -path '*/libs/*' -name '__pycache__' -prune -exec rm -rf {} + 2>/dev/null || true
echo done
