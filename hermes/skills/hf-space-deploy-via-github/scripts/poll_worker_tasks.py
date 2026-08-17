#!/usr/bin/env python3
"""
Nexus Worker 任务轮询器 — cronjob 脚本 (no_agent=True)

定期查询 HF Space worker 的 pending 任务, 如果有新任务则输出通知。
cronjob 调用此脚本, 非空 stdout → 发送给用户; 空 stdout → 静默。

用法: 注册为 hermes cronjob (no_agent=True, schedule='every 30m')
  脚本路径: ~/.hermes/scripts/poll_worker_tasks.py

前置条件:
  - .env 中设置 WORKER_URL (HF Space URL) + WORKER_API_KEY (ADMIN_API_KEY 值)
  - Space 上 /worker/tasks GET 端点已部署
"""

import os
import sys
import requests
from pathlib import Path

# 从 .env 读配置
env_path = Path(os.path.expandvars('${HERMES_HOME:-/opt/data/.hermes}/.env'))
if env_path.exists():
    for line in env_path.read_text().splitlines():
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
        sys.exit(0)  # 静默失败

    data = r.json()
    tasks = data.get("tasks", [])
    count = data.get("count", 0)

    if count == 0:
        sys.exit(0)  # 无 pending 任务 — 静默

    # 有 pending 任务 — 输出通知 (stdout 发送给用户)
    lines = [f"📋 Worker 有 {count} 个待处理任务:"]
    for t in tasks:
        task_id = t.get("task_id", "?")
        task_text = t.get("task", "")[:80]
        created = t.get("created_at", "?")
        lines.append(f"  • [{task_id}] {task_text}")
        lines.append(f"    创建: {created}")

    lines.append("")
    lines.append("执行后用 PATCH /worker/tasks/{task_id} 标记完成:")
    lines.append(f"  curl -X PATCH {WORKER_URL}/worker/tasks/TASK_ID \\")
    lines.append(f'    -H "X-API-Key: $KEY" -H "Content-Type: application/json" \\')
    lines.append(f'    -d \'{{"status":"completed","result":"..."}}\'')

    print("\n".join(lines))

except Exception:
    sys.exit(0)  # 静默失败 — 不打扰用户
