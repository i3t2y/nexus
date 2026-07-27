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
# Codex / 兼容模型，部署时按实际改
_MODEL = os.getenv("CODEX_MODEL", "gpt-4o-mini")


def auth(authorization: str | None) -> None:
    if not _API_KEY:
        return
    if authorization != f"Bearer {_API_KEY}":
        raise HTTPException(401, "unauthorized")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "space": "codex"}


class CompleteBody(BaseModel):
    thread_id: str
    prompt: str


@app.post("/complete")
async def complete(body: CompleteBody, authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(authorization)
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
