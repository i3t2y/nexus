"""Codex 快速编码 Space。对接 OpenAI 兼容 Codex 接口。"""
from __future__ import annotations

import os
from typing import Any

import httpx
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from storage import log_task, save_state

app = FastAPI(title="Nexus Codex")

_API_KEY = os.getenv("NEXUS_API_KEY", "")
_OPENAI = os.getenv("OPENAI_API_KEY", "")
_OPENAI_BASE = os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1")
# Codex / 兼容小模型（gpt-4o-mini 为 OpenAI 现役小模型，2026-07 查证仍在服务）；
# 部署时按账号接入的模型覆盖
_MODEL = os.getenv("CODEX_MODEL", "gpt-4o-mini")


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
    return {"status": "ok", "space": "codex"}


class CompleteBody(BaseModel):
    thread_id: str
    prompt: str


@app.post("/complete")
async def complete(body: CompleteBody, x_nexus_key: str | None = Header(None), authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(x_nexus_key or authorization)
    if not _OPENAI:
        raise HTTPException(500, "OPENAI_API_KEY 未配置")

    log_task(body.thread_id, "codex", "complete", "running")
    save_state(body.thread_id, {"phase": "completing", "model": _MODEL})

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
        log_task(body.thread_id, "codex", "complete", "error")
        raise HTTPException(502, f"codex failed: {e}") from e

    log_task(body.thread_id, "codex", "complete", "done")
    return {"thread_id": body.thread_id, "result": data}
