"""Codex 快速编码 Space。对接 OpenAI 兼容 Codex 接口。"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, Header
from pydantic import BaseModel

from storage import log_task, save_state
from shared.errors import new_request_id, raise_nexus_error, log_event

app = FastAPI(title="Nexus Codex")

_API_KEY = os.getenv("NEXUS_API_KEY", "")
_OPENAI = os.getenv("OPENAI_API_KEY", "")
_OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
_SPACE = "codex"
# Codex / 兼容小模型（gpt-4o-mini 为 OpenAI 现役小模型，2026-07 查证仍在服务）；
# 部署时按账号接入的模型覆盖
_MODEL = os.getenv("CODEX_MODEL", "gpt-4o-mini")


def auth(authorization: str | None, request_id: str) -> None:
    """fail-closed：缺 key = 配置错误，拒绝而非放行。本地免鉴权设 NEXUS_AUTH_MODE=dev。"""
    if os.getenv("NEXUS_AUTH_MODE") == "dev":
        return
    if not _API_KEY:
        log_event(request_id, _SPACE, "auth", "error", reason="NEXUS_API_KEY 未配置")
        raise_nexus_error("config_error", "NEXUS_API_KEY 未配置（生产必填；本地免鉴权设 NEXUS_AUTH_MODE=dev）", 500, request_id)
    if authorization != f"Bearer {_API_KEY}":
        log_event(request_id, _SPACE, "auth", "error", reason="bad credential")
        raise_nexus_error("unauthorized", "鉴权失败", 401, request_id)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "space": "codex"}


class CompleteBody(BaseModel):
    thread_id: str
    prompt: str


@app.post("/complete")
async def complete(
    body: CompleteBody,
    x_nexus_key: str | None = Header(None),
    authorization: str | None = Header(None),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    rid = new_request_id(x_request_id)
    auth(x_nexus_key or authorization, rid)
    if not _OPENAI:
        log_event(rid, _SPACE, "complete", "error", reason="OPENAI_API_KEY 未配置")
        raise_nexus_error("config_error", "OPENAI_API_KEY 未配置", 500, rid)

    log_task(body.thread_id, "codex", "complete", "running", rid)
    save_state(body.thread_id, {"phase": "completing", "model": _MODEL})
    log_event(rid, _SPACE, "complete", "running", thread_id=body.thread_id, model=_MODEL)

    headers = {"Authorization": f"Bearer {_OPENAI}", "Content-Type": "application/json"}
    payload = {
        "model": _MODEL,
        "messages": [{"role": "user", "content": body.prompt}],
        "stream": False,
    }
    try:
        async with httpx.AsyncClient(timeout=60.0) as c:
            r = await c.post(f"{_OPENAI_BASE}/chat/completions", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        log_task(body.thread_id, "codex", "complete", "error", rid)
        log_event(rid, _SPACE, "complete", "error", thread_id=body.thread_id, err=str(e))
        raise_nexus_error("downstream_unreachable", f"codex failed: {e}", 502, rid)

    log_task(body.thread_id, "codex", "complete", "done", rid)
    log_event(rid, _SPACE, "complete", "done", thread_id=body.thread_id)
    return {"thread_id": body.thread_id, "result": data, "request_id": rid}
