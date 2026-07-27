"""Hermes 主控：唯一入口，路由分发，写日志状态。"""
from __future__ import annotations

import os
import uuid
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# 共享库（构建前已同步到本 Space 目录 libs/）
from storage import load_state, log_task, save_state
from gateway import call_space

app = FastAPI(title="Hermes")

_API_KEY = os.getenv("NEXUS_API_KEY", "")


def auth(authorization: str | None) -> None:
    """统一鉴权。Worker 已校验过，这里是双保险。"""
    if not _API_KEY:
        return  # 模板阶段未配 key 则放行
    if authorization != f"Bearer {_API_KEY}":
        raise HTTPException(401, "unauthorized")


@app.get("/health")
async def health() -> dict[str, str]:
    """保活/唤醒探测入口。"""
    return {"status": "ok", "space": "hermes"}


class RunBody(BaseModel):
    prompt: str
    force_space: str | None = None  # 手动指定 langgraph/claude/codex


# ── 路由决策 ────────────────────────────────────────────────────────
_KEYWORDS = {
    "langgraph": ["规划", "多步", "工作流", "依赖", "分解", "plan", "workflow"],
    "claude": ["实现", "重构", "调试", "复杂", "implement", "refactor", "debug"],
    "codex": ["补全", "快速", "片段", "complete", "snippet", "fast"],
}


def route(prompt: str, force: str | None) -> str:
    if force and force in _KEYWORDS:
        return force
    for space, kws in _KEYWORDS.items():
        if any(k in prompt.lower() for k in kws):
            return space
    return "langgraph"  # 默认


def _target_path(space: str) -> str:
    return {"langgraph": "/execute", "claude": "/run", "codex": "/complete"}[space]


# ── 端点 ───────────────────────────────────────────────────────────
@app.post("/run")
async def run(body: RunBody, authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(authorization)
    thread_id = str(uuid.uuid4())
    space = route(body.prompt, body.force_space)

    log_task(thread_id, "hermes", f"route→{space}", "pending")
    save_state(thread_id, {"prompt": body.prompt, "space": space, "phase": "dispatched"})

    try:
        result = await call_space(space, _target_path(space), {"thread_id": thread_id, "prompt": body.prompt})
    except Exception as e:  # noqa: BLE001
        log_task(thread_id, space, "invoke", "error")
        save_state(thread_id, {"phase": "error", "err": str(e)})
        raise HTTPException(502, f"downstream {space} failed: {e}") from e

    log_task(thread_id, space, "invoke", "done")
    save_state(thread_id, {"phase": "done", "downstream": space, "result": result})
    return {"task_id": thread_id, "space": space, "result": result}


@app.get("/state/{thread_id}")
async def state(thread_id: str, authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(authorization)
    st = load_state(thread_id)
    if st is None:
        raise HTTPException(404, "not found")
    return {"thread_id": thread_id, "state": st}
