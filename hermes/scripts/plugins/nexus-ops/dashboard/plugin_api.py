"""nexus-ops dashboard plugin 后端 API。

Hermes dashboard 自动 mount /api/plugins/nexus-ops/ (web_server._mount_plugin_api_routes)。
两件 nexus 仪表需求(hermes 原生 dashboard 无对照):
  - 下游 Space 探活/调起: GET /ops/probe (复用 shared.gateway.ping 探 langgraph/claude/codex)
  - Supabase 业务表只读查: GET /ops/states / /ops/tasks / /ops/logs / /ops/memory
    (agent_states/task_queue/task_logs/long_memory 四表 limit 倒序只读)

只读面:不做任何写 Supabase 操作(写由 hermes 原生 gateway / call_space 走 service_role)。
supabase_client 经 libs/storage(PYTHONPATH=/data/libs 同进程 import),
service_role 全开(K2 模型 A,RLS 03_rls_policies.sql 兜底纵深)。
"""
from __future__ import annotations

import asyncio
from typing import Any

try:
    from fastapi import APIRouter
except Exception:  # 无 dashboard 依赖时仍可被 plugin loader import 验证
    class APIRouter:  # type: ignore
        def get(self, *_a, **_k): return lambda fn: fn
        def post(self, *_a, **_k): return lambda fn: fn

try:
    from storage import supabase_client
except Exception:  # plugin loader import 期 storage 尚未就绪时兜底
    supabase_client = None  # type: ignore
try:
    from shared.gateway import ping as _ping_space
except Exception:
    _ping_space = None  # type: ignore

router = APIRouter()

_DOWNSTREAM = ("langgraph", "claude", "codex")
_LIMIT = 50


def _supa():
    if supabase_client is None:
        raise RuntimeError("storage.supabase_client 不可用(PYTHONPATH 未含 /data/libs?)")
    return supabase_client()


@router.get("/probe")
async def probe_downstream() -> dict[str, Any]:
    """并行探活下游三 Space。返 {space: ok|down}。复用 shared.gateway.ping。"""
    if _ping_space is None:
        return {"error": "shared.gateway 不可用(PYTHONPATH 未含 /data/libs?)",
                "langgraph": "unknown", "claude": "unknown", "codex": "unknown"}
    res = await asyncio.gather(*[_ping_space(s) for s in _DOWNSTREAM], return_exceptions=True)
    return {s: ("ok" if (not isinstance(r, Exception) and r) else "down")
            for s, r in zip(_DOWNSTREAM, res)}


@router.get("/states")
def list_states() -> dict[str, Any]:
    """agent_states 表最新 N 行(thread_id + state)。只读。"""
    try:
        res = (_supa().table("agent_states")
               .select("thread_id,state")
               .order("thread_id", desc=True).limit(_LIMIT).execute())
        return {"rows": res.data or [], "count": len(res.data or [])}
    except Exception as e:  # noqa: BLE001
        return {"error": f"读 agent_states 失败: {e}"}


@router.get("/tasks")
def list_tasks() -> dict[str, Any]:
    """task_queue 表最新 N 行。只读。"""
    try:
        res = (_supa().table("task_queue")
               .select("thread_id,space,status,payload,result,created_at,claimed_at,idempotency_key")
               .order("created_at", desc=True).limit(_LIMIT).execute())
        return {"rows": res.data or [], "count": len(res.data or [])}
    except Exception as e:  # noqa: BLE001
        return {"error": f"读 task_queue 失败: {e}"}


@router.get("/logs")
def list_logs() -> dict[str, Any]:
    """task_logs 表最新 N 行。只读。"""
    try:
        res = (_supa().table("task_logs")
               .select("thread_id,space_name,action,status,request_id,created_at")
               .order("created_at", desc=True).limit(_LIMIT).execute())
        return {"rows": res.data or [], "count": len(res.data or [])}
    except Exception as e:  # noqa: BLE001
        return {"error": f"读 task_logs 失败: {e}"}


@router.get("/memory")
def list_memory() -> dict[str, Any]:
    """long_memory 表全量(键值长期记忆)。只读。"""
    try:
        res = (_supa().table("long_memory")
               .select("key,value,updated_at").order("key").limit(_LIMIT).execute())
        return {"rows": res.data or [], "count": len(res.data or [])}
    except Exception as e:  # noqa: BLE001
        return {"error": f"读 long_memory 失败: {e}"}
