#!/usr/bin/env python3
"""Nexus 本机桥 — 扫 Neon task_queue 调 CNB CodeBuddy NPC(2026-08-22)。

本机桥 = 在用户本地机器(或任意能出网 api.cnb.cool 的主机)运行，
绕开 HF 容器 SNI 出网封禁，消费 Neon task_queue 中 kind='npc' 的任务。

环境变量:
  Neon 连接(同 persist_to_r2.py):
    POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB

  CNB CodeBuddy 配置:
    CNB_ACCESS_TOKEN  (= Bearer token, 必填)
    CNB_API_HOST      (默认 https://api.cnb.cool)
    CNB_REPO          (仓库路径, 如 i3t2y/nexus, 必填)
    CNB_MODEL         (NPC 模型, 默认 deepseek-v4-flash)

  桥循环:
    POLL_INTERVAL_SEC       (Neon 轮询间隔, 默认 300s = 5min, 避 Neon Free scale-to-zero 烧 CU)
    CNB_BUILD_POLL_SEC      (构建状态轮询间隔, 默认 15s)
    CNB_BUILD_TIMEOUT_SEC   (构建超时, 默认 600s = 10min)

用法:
  python poll_worker_tasks.py                    # 循环 poll
  python poll_worker_tasks.py --once             # 单轮 poll 后 exit
  python poll_worker_tasks.py --dry-run          # 只列 pending 不消费

依赖: httpx (已安装)

架构参考:
  ┌─────────────────────────────────────────────────────┐
  │  本机桥 (本机/非 HF)                                 │
  │  Neon SKIP LOCKED poll → CNB OpenAPI → 回写 result  │
  └─────────────────────────────────────────────────────┘
"""
from __future__ import annotations

import json
import os
import signal
import sys
import time
import traceback
from typing import Any

import httpx

# ── 优雅关机 ──────────────────────────────────────────────────────────────
_SHUTDOWN = False
_ONCE = "--once" in sys.argv
_DRY_RUN = "--dry-run" in sys.argv


def _on_sigterm(signum, frame):  # noqa: ANN001
    global _SHUTDOWN
    _SHUTDOWN = True
    print(f"[bridge] recv signal {signum}, graceful shutdown...", flush=True)


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigterm)

# ── 配置 ──
_POLL_INTERVAL = int(os.getenv("POLL_INTERVAL_SEC", "300"))  # Neon Free 5min scale-to-zero
_CNB_BUILD_POLL = int(os.getenv("CNB_BUILD_POLL_SEC", "15"))
_CNB_BUILD_TIMEOUT = int(os.getenv("CNB_BUILD_TIMEOUT_SEC", "600"))

# ── Neon 连接(httpx /sql, 同 persist_to_r2.py) ──────────────────────────────
def _conn_str() -> str:
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty")
    if host.endswith("-pooler"):
        host = host[: -len("-pooler")]
    user = os.getenv("POSTGRES_USER", "")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db = os.getenv("POSTGRES_DB", "neondb")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}?sslmode=require"


def _sql_url() -> str:
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty")
    if host.endswith("-pooler"):
        host = host[: -len("-pooler")]
    return f"https://{host}/sql"


def _neon_query(query: str, params: list | None = None) -> list[dict]:
    """执行单条 SQL via Neon HTTP /sql。"""
    headers = {
        "Neon-Connection-String": _conn_str(),
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {"query": query}
    if params:
        body["params"] = params
    with httpx.Client(timeout=30.0) as client:
        resp = client.post(_sql_url(), headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        return []


# ── CNB CodeBuddy OpenAPI ──────────────────────────────────────────────────
_CNB_HOST = os.getenv("CNB_API_HOST", "https://api.cnb.cool").rstrip("/")
_CNB_TOKEN = os.getenv("CNB_ACCESS_TOKEN", "")
_CNB_REPO = os.getenv("CNB_REPO", "")
_CNB_MODEL = os.getenv("CNB_MODEL", "deepseek-v4-flash")


def _cnb_headers() -> dict[str, str]:
    return {
        "Authorization": f"Bearer {_CNB_TOKEN}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }


def _cnb_start_build(task: str, task_id: str, input_data: dict) -> dict[str, Any] | None:
    """调 CNB OpenAPI 触发 NPC 构建。

    端点: POST /{repo}/-/build/start
    事件: api_trigger_npc
    NPC:  CodeBuddy
    模型: deepseek-v4-flash (NPC 自带, 非调用方 key)

    返回: 构建响应 dict (含 buildId) 或 None(失败)
    """
    if not _CNB_TOKEN or not _CNB_REPO:
        print("[bridge] CNB_ACCESS_TOKEN 或 CNB_REPO 未配置, 跳过 NPC 调用", flush=True)
        return None

    endpoint = f"{_CNB_HOST}/{_CNB_REPO}/-/build/start"
    user_prompt = input_data.get("goal", task)
    body = {
        "event": "api_trigger_npc",
        "npc": {
            "name": "CodeBuddy",
            "model": _CNB_MODEL,
        },
        "env": {
            "userPrompt": user_prompt,
            "systemPrompt": "你是 Nexus CodeBuddy NPC, 负责执行编码/部署任务。",
        },
    }
    try:
        with httpx.Client(timeout=30.0) as client:
            resp = client.post(endpoint, headers=_cnb_headers(), json=body)
            resp.raise_for_status()
            result = resp.json()
            print(f"[bridge] CNB build started: {json.dumps(result, ensure_ascii=False)[:500]}",
                  flush=True)
            return result
    except httpx.HTTPStatusError as e:
        print(f"[bridge] CNB build start HTTP error: {e}", flush=True)
        return None
    except httpx.RequestError as e:
        print(f"[bridge] CNB build start request failed: {e}", flush=True)
        return None


def _cnb_poll_build(build_id: str) -> str | None:
    """轮询 CNB 构建状态直到完成/超时。

    端点: GET /{repo}/-/build/{buildId}/status
    返回: 构建结果文本(成功) 或 None(失败/超时)
    """
    if not _CNB_TOKEN or not _CNB_REPO:
        return None

    status_endpoint = f"{_CNB_HOST}/{_CNB_REPO}/-/build/{build_id}/status"
    deadline = time.time() + _CNB_BUILD_TIMEOUT

    while time.time() < deadline and not _SHUTDOWN:
        try:
            with httpx.Client(timeout=15.0) as client:
                resp = client.get(status_endpoint, headers=_cnb_headers())
                resp.raise_for_status()
                data = resp.json()
            status = (data.get("status") or "").lower()
            print(f"[bridge] build {build_id} status={status}", flush=True)

            if status in ("completed", "success", "succeeded"):
                return data.get("result", data.get("output", json.dumps(data, ensure_ascii=False)))
            if status in ("failed", "error", "cancelled", "timeout"):
                err = data.get("error", data.get("message", "unknown"))
                print(f"[bridge] build {build_id} failed: {err}", flush=True)
                return None
        except httpx.RequestError as e:
            print(f"[bridge] build status poll error: {e}", flush=True)

        for _ in range(_CNB_BUILD_POLL):
            if _SHUTDOWN:
                break
            time.sleep(1)

    print(f"[bridge] build {build_id} timeout after {_CNB_BUILD_TIMEOUT}s", flush=True)
    return None


# ── 任务消费 ──────────────────────────────────────────────────────────────
def _claim_task() -> dict[str, Any] | None:
    """FOR UPDATE SKIP LOCKED 抢一个 pending+kind=npc 任务。

    返回: {task_id, task, kind, input} 或 None(无任务)
    """
    claim_sql = """
        WITH cte AS (
            SELECT task_id FROM task_queue
            WHERE status = 'pending' AND kind = 'npc'
            ORDER BY created_at
            LIMIT 1
            FOR UPDATE SKIP LOCKED
        )
        UPDATE task_queue
        SET status = 'running', attempts = attempts + 1, updated_at = now()
        WHERE task_id IN (SELECT task_id FROM cte)
        RETURNING task_id, task, kind, input
    """
    rows = _neon_query(claim_sql)
    if not rows:
        return None
    row = rows[0]
    return {
        "task_id": row.get("task_id"),
        "task": row.get("task", ""),
        "kind": row.get("kind", "npc"),
        "input": row.get("input", {}),
    }


def _complete_task(task_id: str, output: dict | None, result: str, status: str) -> bool:
    """回写任务结果到 Neon。"""
    output_json = json.dumps(output or {}, ensure_ascii=False) if output else "{}"
    update_sql = """
        UPDATE task_queue
        SET status = %s, output = %s::jsonb, result = %s,
            updated_at = now(), completed_at = now()
        WHERE task_id = %s
    """
    try:
        _neon_query(update_sql, [status, output_json, result[:5000], task_id])
        print(f"[bridge] task {task_id} → {status}", flush=True)
        return True
    except Exception as e:
        print(f"[bridge] task completion writeback failed: {e}", flush=True)
        return False


def _poll_once() -> int:
    """单轮 poll: 抢任务 → 调 CNB → 回写。返回处理的任务数(0/1)。"""
    task = _claim_task()
    if task is None:
        return 0

    task_id = task["task_id"]
    task_text = task["task"]
    input_data = task.get("input", {})
    print(f"[bridge] claimed task {task_id}: {task_text[:120]}", flush=True)

    if _DRY_RUN:
        print(f"[bridge] dry-run, skip CNB for {task_id}", flush=True)
        _complete_task(task_id, {"dry_run": True}, "dry-run skipped", "completed")
        return 1

    # 调 CNB NPC
    build_resp = _cnb_start_build(task_text, task_id, input_data)
    if build_resp is None:
        _complete_task(task_id, {"error": "CNB build start failed"}, "CNB 构建触发失败", "failed")
        return 1

    build_id = build_resp.get("build_id") or build_resp.get("id") or str(build_resp)
    # 轮询构建结果
    npc_result = _cnb_poll_build(build_id)
    if npc_result is None:
        _complete_task(task_id, {"build_id": build_id}, "CNB 构建失败或超时", "failed")
    else:
        _complete_task(task_id, {"build_id": build_id, "npc_output": npc_result[:2000]},
                       npc_result[:5000], "completed")
    return 1


# ── 主循环 ────────────────────────────────────────────────────────────────
def _env_diag() -> dict[str, bool]:
    return {
        "POSTGRES_HOST": bool(os.getenv("POSTGRES_HOST")),
        "CNB_ACCESS_TOKEN": bool(os.getenv("CNB_ACCESS_TOKEN")),
        "CNB_REPO": bool(os.getenv("CNB_REPO")),
    }


def main() -> None:
    diag = _env_diag()
    if not diag["POSTGRES_HOST"]:
        print("[bridge] POSTGRES_HOST 未配置，无法连接 Neon", flush=True)
        sys.exit(2)
    if not diag["CNB_ACCESS_TOKEN"] or not diag["CNB_REPO"]:
        print("[bridge] 警告: CNB_ACCESS_TOKEN/CNB_REPO 未配全 → NPC 调用跳过(仅 dry-run)", flush=True)

    print(f"[bridge] start, poll_interval={_POLL_INTERVAL}s, cnb_host={_CNB_HOST}", flush=True)
    print(f"[bridge] env diag: {diag}", flush=True)

    # 测试 Neon 连接
    try:
        rows = _neon_query("SELECT 1 AS ok")
        print(f"[bridge] Neon connection OK: {rows}", flush=True)
    except Exception as e:
        print(f"[bridge] Neon connection FAILED: {e}", flush=True)
        if _ONCE:
            sys.exit(1)

    _did_once = False
    while not _SHUTDOWN:
        try:
            n = _poll_once()
            if n:
                print(f"[bridge] processed {n} task(s)", flush=True)
                _did_once = True
        except Exception as e:
            print(f"[bridge] poll error: {e}\n{traceback.format_exc()}", flush=True)

        if _ONCE and _did_once:
            print("[bridge] --once done, exit 0", flush=True)
            sys.exit(0)
        if _SHUTDOWN:
            break

        # 无任务时等 _POLL_INTERVAL
        for _ in range(_POLL_INTERVAL):
            if _SHUTDOWN:
                break
            time.sleep(1)

    print("[bridge] shutdown, exit 0", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()