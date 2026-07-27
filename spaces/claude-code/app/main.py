"""Claude 强推理 Space。对接 Anthropic Messages API。"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from storage import log_task, save_state

app = FastAPI(title="Nexus Claude")

_API_KEY = os.getenv("NEXUS_API_KEY", "")
_ANTHROPIC = os.getenv("ANTHROPIC_API_KEY", "")
# 默认模型；最新 ID 见 docs/ARCHITECTURE.md，部署时覆盖
_MODEL = os.getenv("CLAUDE_MODEL", "claude-3-5-sonnet-20241022")


def auth(authorization: str | None) -> None:
    """fail-closed：缺 key = 配置错误，拒绝而非放行。本地免鉴权设 NEXUS_AUTH_MODE=dev。"""
    if os.getenv("NEXUS_AUTH_MODE") == "dev":
        return
    if not _API_KEY:
        raise HTTPException(500, "NEXUS_API_KEY 未配置（生产必填；本地免鉴权设 NEXUS_AUTH_MODE=dev）")
    if authorization != f"Bearer {_API_KEY}":
        raise HTTPException(401, "unauthorized")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "space": "claude"}


class RunBody(BaseModel):
    thread_id: str
    prompt: str


@app.post("/run")
async def run(body: RunBody, x_nexus_key: str | None = Header(None), authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(x_nexus_key or authorization)
    if not _ANTHROPIC:
        raise HTTPException(500, "ANTHROPIC_API_KEY 未配置")

    log_task(body.thread_id, "claude", "run", "running")
    save_state(body.thread_id, {"phase": "reasoning", "model": _MODEL})

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
        log_task(body.thread_id, "claude", "run", "error")
        raise HTTPException(502, f"anthropic failed: {e}") from e

    log_task(body.thread_id, "claude", "run", "done")
    return {"thread_id": body.thread_id, "result": data}
