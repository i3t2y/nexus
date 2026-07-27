"""LangGraph 编排 Space。

Checkpoint 走 Supabase Postgres (AsyncPostgresSaver)。
示例图为最小三步：理解 → 规划 → 输出。真实场景替换节点逻辑。

资源管理（#7/#17 修正）：
- 应用启动时通过 lifespan 创建一次 AsyncPostgresSaver、跑一次 setup()、编译图，
  存进 `app.state`；请求复用全局 checkpointer + graph，不再每请求 setup
  （违背文档的"setup() 仅启动一次"约定 + 每次新连接池开销）。
- lifespan 退出时关闭连接。
- 若 SUPABASE_DB_URI 未配置：lifespan 跳过 checkpointer，/execute 仍返 500（fail-closed）。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from typing import Any

from fastapi import FastAPI, Header, HTTPException
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel
from typing_extensions import TypedDict

from storage import log_task, save_checkpoint, dumps
from shared.checkpointer import build_checkpointer

_API_KEY = os.getenv("NEXUS_API_KEY", "")
_DB_URI = os.getenv("SUPABASE_DB_URI", "")


def auth(authorization: str | None) -> None:
    """fail-closed：缺 key = 配置错误，拒绝而非放行。本地免鉴权设 NEXUS_AUTH_MODE=dev。"""
    if os.getenv("NEXUS_AUTH_MODE") == "dev":
        return
    if not _API_KEY:
        raise HTTPException(500, "NEXUS_API_KEY 未配置（生产必填；本地免鉴权设 NEXUS_AUTH_MODE=dev）")
    if authorization != f"Bearer {_API_KEY}":
        raise HTTPException(401, "unauthorized")


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


# ── lifespan：启动建 checkpointer + 编译图，请求复用 ──────────────────
@asynccontextmanager
async def lifespan(app: FastAPI):
    if not _DB_URI:
        # 无 DB_URI：不建 checkpointer，/execute 会显式 500（见下方防护）
        app.state.cp = None
        app.state.graph = None
        print("[langgraph] SUPABASE_DB_URI 未配置，lifespan 跳过 checkpointer（/execute 将失败）", flush=True)
        yield
        return
    # build_checkpointer() 是 async context manager；进入即建立连接，退出即关
    async with build_checkpointer() as cp:
        await cp.setup()  # 必须等连接就绪后调一次；之后请求复用
        app.state.cp = cp
        app.state.graph = build_graph().compile(checkpointer=cp)
        print("[langgraph] checkpointer ready, graph compiled", flush=True)
        yield
    # 退出 async with 自动关连接
    app.state.cp = None
    app.state.graph = None


app = FastAPI(title="Nexus LangGraph", lifespan=lifespan)


@app.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "space": "langgraph"}


@app.post("/execute")
async def execute(body: ExecBody, x_nexus_key: str | None = Header(None), authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(x_nexus_key or authorization)
    if not _DB_URI:
        raise HTTPException(500, "SUPABASE_DB_URI 未配置，无法用 PostgresSaver")
    cp = getattr(app.state, "cp", None)
    graph = getattr(app.state, "graph", None)
    if cp is None or graph is None:
        # lifespan 未就绪（DB 不可达或仍在启动）—— fail-closed 而非裸连
        raise HTTPException(503, "checkpointer 未就绪（DB 不可达或服务仍在启动）")

    log_task(body.thread_id, "langgraph", "execute", "running")
    try:
        final = await graph.ainvoke(
            {"prompt": body.prompt},
            config={"configurable": {"thread_id": body.thread_id}},
        )
        # 最终状态 blob 落 R2（小状态也可不落，仅演示）
        save_checkpoint(body.thread_id, dumps(final))
    except Exception as e:  # noqa: BLE001
        log_task(body.thread_id, "langgraph", "execute", "error")
        raise HTTPException(502, f"graph failed: {e}") from e

    log_task(body.thread_id, "langgraph", "execute", "done")
    return {"thread_id": body.thread_id, "result": final}
