#!/usr/bin/env python3
"""
Nexus Worker 任务轮询器 — cronjob 脚本

定期查询 HF Space worker 的 pending 任务, 如果有新任务则输出通知。
cronjob 调用此脚本, 非空 stdout → 发送给用户; 空 stdout → 静默。

用法: python3 /opt/data/.hermes/scripts/poll_worker_tasks.py
"""

import os
import sys
import requests
from pathlib import Path

# 从 .env 读配置
env = Path('/opt/data/.hermes/.env').read_text()
for line in env.splitlines():
    if '=' in line and not line.startswith('#'):
        k, v = line.split('=', 1)
        os.environ.setdefault(k.strip(), v.strip())

WORKER_URL = os.environ.get("WORKER_URL", "https://nmem-memgraph.hf.space")
WORKER_API_KEY = os.environ.get("WORKER_API_KEY", os.environ.get("MEM0_API_KEY", ""))

try:
    r = requests.get(
        f"{WORKER_URL}/worker/tasks",
        headers={"X-API-Key": WORKER_API_KEY},
        params={"status": "pending", "limit": 10},
        timeout=30,
    )
    if r.status_code != 200:
        # 静默失败 — 不打扰用户
        sys.exit(0)

    data = r.json()
    tasks = data.get("tasks", [])
    count = data.get("count", 0)

    if count == 0:
        # 无 pending 任务 — 静默
        sys.exit(0)

    # 有 pending 任务 — 输出通知
    lines = [f"📋 Worker 有 {count} 个待处理任务:"]
    for t in tasks:
        task_id = t.get("task_id", "?")
        task_text = t.get("task", "")[:80]
        created = t.get("created_at", "?")
        lines.append(f"  • [{task_id}] {task_text}")
        lines.append(f"    创建: {created}")

    lines.append("")
    lines.append("执行后用 PATCH /worker/tasks/{task_id} 标记完成")
    lines.append(f"  curl -X PATCH {WORKER_URL}/worker/tasks/TASK_ID \\")
    lines.append(f'    -H "X-API-Key: $KEY" -H "Content-Type: application/json" \\')
    lines.append(f'    -d \'{{"status":"completed","result":"..."}}\''  )

    print("\n".join(lines))

except Exception:
    # 静默失败
    sys.exit(0)
