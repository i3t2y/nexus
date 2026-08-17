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

为什么用 6543 transaction pooler 安全（与旧 asyncpg 假设不同）:
- langgraph-checkpoint-postgres 底层用 **psycopg3**（非 asyncpg），依赖 psycopg[binary]。
- AsyncPostgresSaver.from_conn_string 内部硬编码三参数调 AsyncConnection.connect:
      conn_string, autocommit=True, prepare_threshold=0, row_factory=dict_row
- prepare_threshold=0 = 永不启用 server-side prepared statement。
- Supabase 6543 transaction pooler 不支持 server-side prepared statement，
  正因 prepare_threshold=0 已禁用，所以 6543 在此库下不冲突。
- 旧存档里 asyncpg 默认开 prepared statement 才需 statement_cache_size=0 兜底，
  本模板未用 asyncpg，无需该兜底。
- 注意：若未来手写 AsyncConnection.connect 直连（不经 from_conn_string），
  必须自己带 autocommit=True + prepare_threshold=0 + row_factory=dict_row，
  否则 setup() 不提交、读到 TypeError、或 6543 报 prepared statement does not exist。
"""
from __future__ import annotations

import os
from contextlib import asynccontextmanager
from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

# langgraph-checkpoint-postgres 单独安装：pip install langgraph-checkpoint-postgres
from langgraph.checkpoint.postgres.aio import AsyncPostgresSaver


def db_uri() -> str:
    """Supabase 直连串（port 6543 transaction pooler，见模块 docstring 为何安全）。

    额外强制 `sslmode=require`：Supabase pooler 实际已强制 TLS（pgBouncer），
    这里显式补上是纵深防御 + fail-closed（万一 URI 指非加密端点直接报错而非裸传）。
    若 URI 已带 sslmode 参数则尊重原值，不覆盖。

    K2 修正(2026-08-02):改 urllib.parse 重构 query,非字符串拼接。
    旧 `sep = "&" if "?" in uri else "?"` 在密码含 `?`/`&` 时误判分隔符致解析破
    (理论风险,Supabase pass 安全字符集实操不触发,但深度防御):用 urlparse 拆,
    parse_qsl 解既有 query 为 list,补 sslmode(未有时),urlencode 重组,
    urlunparse 回拼——彻底避字符串探测歧义。
    """
    uri = os.getenv("SUPABASE_DB_URI")
    if not uri:
        raise RuntimeError("SUPABASE_DB_URI 未设置（需 Supabase connection string）")
    p = urlparse(uri)
    q = parse_qsl(p.query, keep_blank_values=True)
    if not any(k == "sslmode" for k, _ in q):  # 未显式指定才补
        q.append(("sslmode", "require"))
    return urlunparse(p._replace(query=urlencode(q, safe="")))


@asynccontextmanager
async def build_checkpointer():
    """构造一个会用即关的 AsyncPostgresSaver。调用方负责 .setup()。

    from_conn_string 内部已设 prepare_threshold=0 / autocommit=True / row_factory=dict_row，
    故 6543 transaction pooler 直连安全，无需额外配置。
    """
    async with AsyncPostgresSaver.from_conn_string(db_uri()) as cp:
        yield cp
