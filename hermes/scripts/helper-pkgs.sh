#!/usr/bin/env bash
# ─────────────────────────────────────────────────────────────
# helper-pkgs.sh — 容器运行时"镜像外"补装包清单(单一事实源)
# ─ 为什么独立成文件(2026-09-04 用户拍板):
#   real-start.sh 是生产核心逻辑, 不该为几条 pip install 长肉。补装集中到此,
#   多条积累后, 未来一次性并进镜像重建(build 复用同一清单, 双路同源不漂移)。
# ─ 双路复用:
#   ▸ 运行时: real-start.sh 在 launching boot 前 source 本文件并调 install_extra_pkgs
#     (幂等: 已装则跳过 → 镜像若已预装, 启动零开销)。
#   ▸ 镜像构建: Dockerfile 在 pip 安装段后加
#         COPY helper-pkgs.sh /tmp/helper-pkgs.sh && RUN . /tmp/helper-pkgs.sh && install_extra_pkgs
#     同一清单在 build 预装, 启动检测已装即跳过(不重复不冲突)。build 后此文件可从
#     README 抄进 runtime; 清单永远此一处为准。
# ─ 改法: 追加一行到 EXTRA_PKGS, 每行三段 "已装判定python表达式|pip包名|说明"。
#     sync(Bucket) → Restart 生效(运行时时); 或攒够一并进下一次镜像重建。
#     ★ 已装判定用 import 表达式(python -c "import <expr>"), 判定不对会误重复装,
#       但幂等 + 失败非阻断, 最多多装一次, 安全。
# ─ 安全: 全程不 echo 任何 key; 失败(网络/索引)仅 WARN, 绝不阻断 boot。
# ─────────────────────────────────────────────────────────────

# 清单: "python判定|pip包|中文说明"。已装(python -c "import X" 成功)则跳过。
EXTRA_PKGS=(
  "snowballstemmer|snowballstemmer|model_tools 工具全文检索词干化,消 'No module named' WARNING"
)

# 遍历清单, 幂等安装。任一失败返回非零(real-start 可据此 WARN, 不阻断 boot)。
install_extra_pkgs() {
  # set -u 由调用方(real-start.sh)继承, 本函数内所有变量显式初始化
  local entry="" pycheck="" spec="" desc="" ret=0
  for entry in "${EXTRA_PKGS[@]}"; do
    IFS='|' read -r pycheck spec desc <<<"$entry"
    [ -z "${pycheck:-}" ] && continue
    if python -c "import ${pycheck}" 2>/dev/null; then
      echo "[helper-pkgs] present: ${spec} (${desc})"
      continue
    fi
    echo "[helper-pkgs] installing ${spec} (${desc})..."
    if python -m pip install --break-system-packages --quiet "${spec}"; then
      echo "[helper-pkgs] installed OK: ${spec}"
    else
      echo "[helper-pkgs] WARN: install failed (non-fatal): ${spec}"
      ret=1
    fi
  done
  return "$ret"
}