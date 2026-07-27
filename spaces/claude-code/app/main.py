"""Claude 强推理 Space。对接 Anthropic Messages API。"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, Header
from pydantic import BaseModel

from storage import log_task, save_state
from shared.errors import new_request_id, raise_nexus_error, log_event

app = FastAPI(title="Nexus Claude")

_API_KEY = os.getenv("NEXUS_API_KEY", "")
_ANTHROPIC = os.getenv("ANTHROPIC_API_KEY", "")
_SPACE = "claude"
# 默认模型（Claude 5 族最新）；部署时按账号可用模型覆盖
_MODEL = os.getenv("CLAUDE_MODEL", "claude-sonnet-5")


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
    return {"status": "ok", "space": "claude"}


class RunBody(BaseModel):
    thread_id: str
    prompt: str


@app.post("/run")
async def run(
    body: RunBody,
    x_nexus_key: str | None = Header(None),
    authorization: str | None = Header(None),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    rid = new_request_id(x_request_id)
    auth(x_nexus_key or authorization, rid)
    if not _ANTHROPIC:
        log_event(rid, _SPACE, "run", "error", reason="ANTHROPIC_API_KEY 未配置")
        raise_nexus_error("config_error", "ANTHROPIC_API_KEY 未配置", 500, rid)

    log_task(body.thread_id, "claude", "run", "running", rid)
    save_state(body.thread_id, {"phase": "reasoning", "model": _MODEL})
    log_event(rid, _SPACE, "run", "running", thread_id=body.thread_id, model=_MODEL)

    headers = {
        "x-api-key": _ANTHROPIC,
        "anthropic-version": "2023-06-01",
        "content-type": "application/json",
    }
    payload = {
        "model": _MODEL,
        "max_tokens": 4096,
        "messages": [{"role": "user", "content": body.prompt}],
    }
    try:
        async with httpx.AsyncClient(timeout=120.0) as c:
            r = await c.post("https://api.anthropic.com/v1/messages", json=payload, headers=headers)
            r.raise_for_status()
            data = r.json()
    except httpx.HTTPError as e:
        log_task(body.thread_id, "claude", "run", "error", rid)
        log_event(rid, _SPACE, "run", "error", thread_id=body.thread_id, err=str(e))
        raise_nexus_error("downstream_unreachable", f"anthropic failed: {e}", 502, rid)

    log_task(body.thread_id, "claude", "run", "done", rid)
    log_event(rid, _SPACE, "run", "done", thread_id=body.thread_id)
    return {"thread_id": body.thread_id, "result": data, "request_id": rid}
