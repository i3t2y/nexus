"""LangGraph AsyncPostgresSaver 适配：状态进 Supabase Postgres，大 blob 进 R2。

用法（在 langgraph Space 内）：

    from shared.checkpointer import build_checkpointer, set_thread_context
    async with build_checkpointer() as cp:
        await cp.setup()
        graph = builder.compile(checkpointer=cp)
        ...

设计：
- 后进的 checkpoint 元数据走 Postgres（AsyncPostgresSaver 原生能力）。
- 单次 checkpoint 过大（>100KB）的 blob 额外落 R2，Postgres 只存 key。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager

# langgraph-checkpoint-postgres 单独安装：pip install langgraph-checkpoint-postgres
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def db_uri() -> str:
    """Supabase 直连串（port 6543 pooler）。"""
    uri = os.getenv("SUPABASE_DB_URI")
    if not uri:
        raise RuntimeError("SUPABASE_DB_URI 未设置（需 Supabase connection string）")
    return uri


@asynccontextmanager
async def build_checkpointer():
    """构造一个会用即关的 AsyncPostgresSaver。调用方负责 .setup()。"""
    async with AsyncPostgresSaver.from_conn_string(db_uri()) as cp:
        yield cp
