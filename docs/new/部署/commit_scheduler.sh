#!/bin/bash
# ══════════════════════════════════════════════════════════════════
# [骨架留档·2026-07-29 标注] 本件为 omn 血统 CommitScheduler 归档脚本模板,
# 非 Nexus 现役代码。Nexus 现役日志备份走 R2(persist_to_r2.py,见 spaces/hermes/scripts/),
# 本 dataset 冷备路径属 omn 双轨备选(CommitScheduler 作 HF 私有 dataset 长期归档),
# 与 litestream 主路径同为 omn 设计;Nexus 用 Supabase Postgres + R2 备份,
# litestream/CommitScheduler 双轨对 Nexus 均需国产化适配或弃用(见日志存放.md / 最强模板 §6)。
# 保留本件作 omn 骨架速查,勿直接当作 Nexus 现役部署件执行。
# ══════════════════════════════════════════════════════════════════
# 注:COMPONENT 默认 omniroute 为 omn 上游值,Nexus 五组件无单名 omniroute;
#     若移植须改默认为 hermes 或靠 NEXUS_COMPONENT env 注入。
# Nexus CommitScheduler — 5分钟高吞吐归档、聚合指标脱敏与清理
# ══════════════════════════════════════════════════════════════════
set -eo pipefail

echo "[scheduler] CommitScheduler 启动 $(date '+%F %T')"

# 1. 检查环境变量
[ -n "$HF_TOKEN" ] || { echo "[scheduler] WARN: 未注入 HF_TOKEN，跳过归档同步"; exit 0; }
[ -n "$LOG_PUBLIC_DATASET_REPO" ] || { echo "[scheduler] WARN: 未注入 LOG_PUBLIC_DATASET_REPO，跳过归档同步"; exit 0; }

COMPONENT="${NEXUS_COMPONENT:-omniroute}"
DATA_DIR="${DATA_DIR:-/data}"
LOG_DIR="${DATA_DIR}/logs"
mkdir -p "$LOG_DIR"

# 2. 模拟日志收集（这里应由业务或网关写入 /data/logs/）
# 如果没有实际日志，生成基本的心跳指标
_status_log="${LOG_DIR}/gateway_status.log"
if [ ! -f "$_status_log" ]; then
  echo "{"ts": $(date +%s%3N), "level": "info", "component": "scheduler", "msg": "CommitScheduler heartbeat"}" > "$_status_log"
fi

# 3. 日志脱敏与清理 (A类与B类脱敏, 严禁泄露 C类/PSK/Bearer)
echo "[scheduler] 开始对日志进行脱敏过滤..."
_tmp_archive=$(mktemp "/tmp/clean_archive_XXXXXX.log")

# 强制过滤敏感规则：剔除 PSK、Authorization、Bearer、Bearer Token 样式
grep -v -E "api_key|token|auth|bearer|psk|secret|key|password|jwt" "$_status_log" > "$_tmp_archive" || true

# 4. 判断日志文件大小并进行截断限制 (不超过 5MB)
_log_size=$(wc -c < "$_tmp_archive" 2>/dev/null || echo 0)
if [ "$_log_size" -gt 5000000 ]; then
  echo "[scheduler] 日志超过 5MB 限额，触发安全截断..."
  tail -c 5000000 "$_tmp_archive" > "${_tmp_archive}.cut"
  mv "${_tmp_archive}.cut" "$_tmp_archive"
fi

# 5. 上传至 Hugging Face 公开 Dataset 仓库
echo "[scheduler] 正在同步脱敏日志至公开 Dataset: ${LOG_PUBLIC_DATASET_REPO}"

python3 -c "
import os
from huggingface_hub import HfApi
api = HfApi()
try:
    api.upload_file(
        path_or_fileobj='$_tmp_archive',
        path_in_repo='${COMPONENT}/$(date +%Y-%m-%d)/logs.log',
        repo_id=os.environ['LOG_PUBLIC_DATASET_REPO'],
        repo_type='dataset',
        token=os.environ['HF_TOKEN']
    )
    print('[scheduler] 同步成功！')
except Exception as e:
    print(f'[scheduler] ERROR: 同步失败: {e}')
" || echo "[scheduler] WARN: 上传过程出错，等待下个周期"

rm -f "$_tmp_archive"
echo "[scheduler] 周期性任务结束 $(date '+%F %T')"
