"""Space 间调用：经 Worker 网关为主，直调 hf.space 为回退。

契约见 docs/COMMUNICATION.md。
"""
from __future__ import annotations

import os
from typing import Any

import httpx

# 下游 Space 名 → 直调 URL（回退用）
_SPACE_URLS: dict[str, str] = {
    "langgraph": os.getenv("LANGGRAPH_URL", ""),
    "claude": os.getenv("CLAUDE_URL", ""),
    "codex": os.getenv("CODEX_URL", ""),
}

_TIMEOUT = 90.0  # 含可能冷启动
_GATEWAY_TIMEOUT = 60.0
_API_KEY = os.getenv("NEXUS_API_KEY", "")
_GATEWAY = os.getenv("GATEWAY_URL", "")


def _headers() -> dict[str, str]:
    if not _API_KEY:
        raise RuntimeError("NEXUS_API_KEY 未设置")
    return {"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"}


async def call_space(space: str, path: str, payload: dict[str, Any]) -> dict[str, Any]:
    """调下游 Space。优先 Worker 网关，失败回退直调。

    Args:
        space: langgraph / claude / codex
        path: 下游 Space 内部路径，如 /execute
        payload: JSON body
    """
    headers = _headers()

    # ── 主：经 Worker 网关 ──
    if _GATEWAY:
        try:
            async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT) as c:
                r = await c.post(
                    f"{_GATEWAY}/route",
                    json={"space": space, "path": path, "body": payload},
                    headers=headers,
                )
                if 200 <= r.status_code < 300:
                    return r.json()
                if r.status_code != 404:  # 404 视为网关未配置该路由 → 回退
                    r.raise_for_status()
        except (httpx.TimeoutException, httpx.RequestError):
            pass  # 网关不可达，走回退

    # ── 回退：直调 hf.space ──
    base = _SPACE_URLS.get(space)
    if not base:
        raise RuntimeError(f"{space} 的 URL 未配置（既无 GATEWAY_URL 也无 {space.upper()}_URL）")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(f"{base}{path}", json=payload, headers=headers)
        r.raise_for_status()
        return r.json()


async def ping(space: str) -> bool:
    """健康探测，用于保活/唤醒。"""
    base = _SPACE_URLS.get(space, "")
    if not base:
        return False
    try:
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{base}/health", headers=_headers())
            return r.status_code == 200
    except httpx.RequestError:
        return False
