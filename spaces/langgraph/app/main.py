"""LangGraph 编排 Space。

Checkpoint 走 Supabase Postgres (AsyncPostgresSaver)。
示例图为最小三步：理解 → 规划 → 输出。真实场景替换节点逻辑。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from langgraph.graph import END, START, StateGraph
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver
from pydantic import BaseModel
from typing_extensions import TypedDict

from storage import log_task, save_checkpoint, dumps

app = FastAPI(title="Nexus LangGraph")

_API_KEY = os.getenv("NEXUS_API_KEY", "")
_DB_URI = os.getenv("SUPABASE_DB_URI", "")


def auth(authorization: str | None) -> None:
    if not _API_KEY:
        return
    if authorization != f"Bearer {_API_KEY}":
        raise HTTPException(401, "unauthorized")


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "space": "langgraph"}


# ── 最小示意 StateGraph ────────────────────────────────────────────
class S(TypedDict, total=False):
    prompt: str
    steps: list[str]
    output: str


async def node_understand(state: S) -> dict[str, Any]:
    # 真实接入 LLM；此处桩返回
    return {"steps": [f"understand: {state.get('prompt', '')[:40]}"]}


async def node_plan(state: S) -> dict[str, Any]:
    steps = state.get("steps", [])
    return {"steps": steps + ["plan: split into subtasks"]}


async def node_output(state: S) -> dict[str, Any]:
    return {"output": "PLAN\n" + "\n".join(state.get("steps", []))}


def build_graph():
    b = StateGraph(S)
    b.add_node("understand", node_understand)
    b.add_node("plan", node_plan)
    b.add_node("output", node_output)
    b.add_edge(START, "understand")
    b.add_edge("understand", "plan")
    b.add_edge("plan", "output")
    b.add_edge("output", END)
    return b


class ExecBody(BaseModel):
    thread_id: str
    prompt: str


@app.post("/execute")
async def execute(body: ExecBody, authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(authorization)
    if not _DB_URI:
        raise HTTPException(500, "SUPABASE_DB_URI 未配置，无法用 PostgresSaver")
    log_task(body.thread_id, "langgraph", "execute", "running")

    # AsyncPostgresSaver 用完即关；生产可改长连池
    try:
        async with AsyncPostgresSaver.from_conn_string(_DB_URI) as cp:
            await cp.setup()
            graph = build_graph().compile(checkpointer=cp)
            final = await graph.ainvoke(
                {"prompt": body.prompt},
                config={"configurable": {"thread_id": body.thread_id}},
            )
        # 把最终状态 blob 落 R2（小状态也可不落，仅演示）
        save_checkpoint(body.thread_id, dumps(final))
    except Exception as e:  # noqa: BLE001
        log_task(body.thread_id, "langgraph", "execute", "error")
        raise HTTPException(502, f"graph failed: {e}") from e

    log_task(body.thread_id, "langgraph", "execute", "done")
    return {"thread_id": body.thread_id, "result": final}
