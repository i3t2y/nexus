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
# 私有 HF Space 的 HF 层访问 token；公开 Space 可不配。
_HF_TOKEN = os.getenv("HF_TOKEN", "")


def _gateway_headers(request_id: str | None = None) -> dict[str, str]:
    """调 Worker 网关的 header（网关自身 requireAuth 读 Authorization）。"""
    if not _API_KEY:
        raise RuntimeError("NEXUS_API_KEY 未设置")
    h: dict[str, str] = {"Authorization": f"Bearer {_API_KEY}", "Content-Type": "application/json"}
    if request_id:
        h["X-Request-ID"] = request_id
    return h


def _space_headers(request_id: str | None = None) -> dict[str, str]:
    """直调下游 HF Space 的 header。

    - X-Nexus-Key：下游 app auth() 读（NEXUS_API_KEY）。
    - Authorization：留给 HF 层（私有 Space 需 Bearer HF_TOKEN；公开 Space 可不带）。
      不用 Authorization 传 NEXUS_API_KEY，否则和私有 Space 的 HF 层 token 冲突。
    """
    if not _API_KEY:
        raise RuntimeError("NEXUS_API_KEY 未设置")
    h: dict[str, str] = {
        "X-Nexus-Key": f"Bearer {_API_KEY}",
        "Content-Type": "application/json",
    }
    if _HF_TOKEN:
        h["Authorization"] = f"Bearer {_HF_TOKEN}"
    if request_id:
        h["X-Request-ID"] = request_id
    return h


async def call_space(space: str, path: str, payload: dict[str, Any], request_id: str | None = None) -> dict[str, Any]:
    """调下游 Space。优先 Worker 网关，失败回退直调。

    Args:
        space: langgraph / claude / codex
        path: 下游 Space 内部路径，如 /execute
        payload: JSON body
        request_id: 透传 X-Request-ID，缺省不发（下游自生成）
    """
    # ── 主：经 Worker 网关（网关入站鉴权读 Authorization）──
    if _GATEWAY:
        try:
            async with httpx.AsyncClient(timeout=_GATEWAY_TIMEOUT) as c:
                r = await c.post(
                    f"{_GATEWAY}/route",
                    json={"space": space, "path": path, "body": payload},
                    headers=_gateway_headers(request_id),
                )
                if 200 <= r.status_code < 300:
                    return r.json()
                if r.status_code != 404:  # 404 视为网关未配置该路由 → 回退
                    r.raise_for_status()
        except (httpx.TimeoutException, httpx.RequestError):
            pass  # 网关不可达，走回退

    # ── 回退：直调 hf.space（私有 Space 需 HF_TOKEN + X-Nexus-Key）──
    base = _SPACE_URLS.get(space)
    if not base:
        raise RuntimeError(f"{space} 的 URL 未配置（既无 GATEWAY_URL 也无 {space.upper()}_URL）")
    async with httpx.AsyncClient(timeout=_TIMEOUT) as c:
        r = await c.post(f"{base}{path}", json=payload, headers=_space_headers(request_id))
        r.raise_for_status()
        return r.json()


async def ping(space: str) -> bool:
    """健康探测，用于保活/唤醒（直调，走 _space_headers）。"""
    base = _SPACE_URLS.get(space, "")
    if not base:
        return False
    try:
        h = _space_headers()
        # GET 不带 Content-Type
        h.pop("Content-Type", None)
        async with httpx.AsyncClient(timeout=10.0) as c:
            r = await c.get(f"{base}/health", headers=h)
            return r.status_code == 200
    except httpx.RequestError:
        return False
