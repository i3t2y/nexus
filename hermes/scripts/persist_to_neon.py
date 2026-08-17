"""Neon Postgres 持久化同步（替代 persist_to_r2.py, 2026-08-17）。

原 persist_to_r2.py: Supabase 读 → R2 快照 (双保险)
本脚本: 通过 Neon HTTP /sql 端点写入结构化四表 (Neon 本身就是持久化)

=== 2026-08-17 重构: psycopg2 → httpx HTTP /sql ===
原 psycopg2 长连接 + while True sleep(300) 阻止 Neon scale-to-zero → 180 CU-h/月超额
新方案: 每次同步 = 独立 HTTP POST 到 Neon /sql 端点, 完即断
  - 不占 TCP 连接 → Neon 5min 闲置后自然 suspend → CU-h ~0.5-3/月
  - 不依赖 psycopg2 (免 libpq 编译) → 只需 httpx (Python 主力 HTTP 库)
  - 协议: POST https://{host}/sql, header Neon-Connection-String, body {query, params}
  - 虽未公开文档化 (Neon PR #9827: "not public API yet, just grandfathered in")
    但 Neon 官方 driver @neondatabase/serverless 走同一端点, 不会弃用

砍掉:
  - Supabase (supabase-py) → 直接 HTTP /sql
  - R2 (boto3) → 不需要, Neon 自己就是持久化
  - backup_snapshots 元数据 → R2 砍了就没用了
  - psycopg2 长连接 → httpx 短请求

环境变量:
  POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
  SYNC_INTERVAL_SEC (默认 600 = 10分钟, > Neon 5min auto-suspend 保证 suspend)

表: agent_states, task_logs, long_memory, skills_index
"""
from __future__ import annotations

import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any

import httpx  # noqa: E402

_INTERVAL = int(os.getenv("SYNC_INTERVAL_SEC", "600"))

# 四表读写逻辑:
# - agent_states, long_memory, skills_index: UPSERT (主键冲突覆盖)
# - task_logs: 只追加 (bigserial id, 不覆盖)
_TABLES = ["agent_states", "task_logs", "long_memory", "skills_index"]


def _env_diag() -> dict[str, bool]:
    """诊断: HF Secrets 注入探测 (不回显真值, 仅 presence)。"""
    return {
        "POSTGRES_HOST": bool(os.getenv("POSTGRES_HOST")),
        "POSTGRES_PORT": bool(os.getenv("POSTGRES_PORT", "5432")),
        "POSTGRES_USER": bool(os.getenv("POSTGRES_USER")),
        "POSTGRES_PASSWORD": bool(os.getenv("POSTGRES_PASSWORD")),
        "POSTGRES_DB": bool(os.getenv("POSTGRES_DB", "neondb")),
    }


def _conn_str() -> str:
    """构建 Neon 连接串 (用于 Neon-Connection-String header)。"""
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty (HF Secrets missing)")
    # Neon HTTP /sql 要求 non-pooler host (strip -pooler suffix)
    if host.endswith("-pooler"):
        host = host[: -len("-pooler")]
    user = os.getenv("POSTGRES_USER", "")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db = os.getenv("POSTGRES_DB", "neondb")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}?sslmode=require"


def _sql_url() -> str:
    """Neon HTTP /sql endpoint URL。"""
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty (HF Secrets missing)")
    if host.endswith("-pooler"):
        host = host[: -len("-pooler")]
    return f"https://{host}/sql"


def _neon_query(query: str, params: list | None = None) -> list[dict]:
    """执行单条 SQL via Neon HTTP /sql 端点。

    每次 = 独立 HTTP POST, 完即断, 不占连接。
    返回行列表 (dict per row)。
    """
    headers = {
        "Neon-Connection-String": _conn_str(),
        "Content-Type": "application/json",
    }
    body: dict[str, Any] = {"query": query}
    if params:
        body["params"] = params

    with httpx.Client(timeout=30.0) as client:
        resp = client.post(_sql_url(), headers=headers, json=body)
        resp.raise_for_status()
        data = resp.json()
        # Neon /sql 返回 {"rows": [...]} 或 {"command": "...", "row_count": N}
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        return []


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_tables():
    """幂等建表 (如果 neon-schema.sql 没跑过)。

    Neon HTTP /sql 不支持事务, 每条 DDL 独立请求。
    """
    ddls = [
        """CREATE TABLE IF NOT EXISTS agent_states (
            thread_id   text PRIMARY KEY,
            state       jsonb   NOT NULL DEFAULT '{}'::jsonb,
            updated_at  timestamptz NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS task_logs (
            id          bigserial PRIMARY KEY,
            thread_id   text NOT NULL,
            space_name  text NOT NULL,
            action      text NOT NULL,
            status      text NOT NULL,
            request_id  text,
            created_at  timestamptz NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS long_memory (
            key         text PRIMARY KEY,
            value       jsonb   NOT NULL DEFAULT '{}'::jsonb,
            updated_at  timestamptz NOT NULL DEFAULT now()
        )""",
        """CREATE TABLE IF NOT EXISTS skills_index (
            skill_name   text PRIMARY KEY,
            description  text,
            source       text,
            r2_key       text,
            usage_count  integer   NOT NULL DEFAULT 0,
            last_used    timestamptz
        )""",
        """CREATE TABLE IF NOT EXISTS space_health (
            id          bigserial PRIMARY KEY,
            space       text NOT NULL,
            status      text NOT NULL,
            detail      text,
            created_at  timestamptz NOT NULL DEFAULT now()
        )""",
    ]
    for ddl in ddls:
        try:
            _neon_query(ddl)
        except Exception as e:
            print(f"[persist-neon] DDL failed: {e}", flush=True)


def sync_once() -> dict[str, Any]:
    """周期同步: 本地 state.db → Neon 四表。

    实际上 hermes 的四表数据来自 hermes 进程内部写入。
    本脚本的主要作用是确保表存在 + 周期健康检查。
    """
    # 幂等建表 (每条独立 HTTP 请求)
    _ensure_tables()

    counts: dict[str, Any] = {}
    ts = _now_iso()

    # 读取每表行数 (健康检查) — 每条独立 HTTP 请求
    for t in _TABLES:
        try:
            rows = _neon_query(f'SELECT COUNT(*) AS cnt FROM public."{t}"')
            counts[t] = rows[0]["cnt"] if rows else 0
        except Exception as e:
            counts[f"{t}_err"] = f"[{type(e).__name__}] {e}"

    # 写入 space_health 记录
    try:
        _neon_query(
            "INSERT INTO space_health (space, status, detail, created_at) VALUES ($1, $2, $3, $4)",
            ["hermes", "ok", json.dumps(counts), ts],
        )
    except Exception as e:
        counts["space_health_err"] = f"[{type(e).__name__}] {e}"

    counts["_ts"] = ts
    return counts


def main() -> None:
    print(f"[persist-neon] start, interval={_INTERVAL}s (HTTP /sql mode)", flush=True)
    print(f"[persist-neon] env diag={_env_diag()}", flush=True)

    # 首次连接测试
    try:
        rows = _neon_query("SELECT 1 AS ok")
        print(f"[persist-neon] Neon HTTP /sql connection OK: {rows}", flush=True)
    except Exception as e:
        print(f"[persist-neon] Neon HTTP /sql connection FAILED: {e}", flush=True)

    while True:
        try:
            res = sync_once()
            print(f"[persist-neon] synced {res}", flush=True)
        except Exception as e:
            etype = type(e).__name__
            msg = str(e)
            tb = traceback.format_exc().splitlines()
            tb_short = " | ".join(tb[-3:]) if len(tb) >= 3 else " | ".join(tb)
            print(f"[persist-neon] fatal[{etype}] {msg} | tb={tb_short} | env={_env_diag()}", flush=True)
        time.sleep(_INTERVAL)


if __name__ == "__main__":
    main()
