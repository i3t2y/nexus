"""Neon Postgres 持久化同步（替代 persist_to_r2.py, 2026-08-17）。

原 persist_to_r2.py: Supabase 读 → R2 快照 (双保险)
本脚本: 直接 psycopg2 连 Neon, 结构化四表直接写入 (Neon 本身就是持久化)

砍掉:
  - Supabase (supabase-py) → 直接 psycopg2
  - R2 (boto3) → 不需要, Neon 自己就是持久化
  - backup_snapshots 元数据 → R2 砍了就没用了

环境变量:
  POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
  SYNC_INTERVAL_SEC (默认 300 = 5分钟)

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

import psycopg2  # noqa: E402
from psycopg2.extras import RealDictCursor  # noqa: E402

_INTERVAL = int(os.getenv("SYNC_INTERVAL_SEC", "300"))

# 四表读写逻辑:
# - agent_states, long_memory, skills_index: UPSERT (主键冲突覆盖)
# - task_logs: 只追加 (bigserial id, 不覆盖)
_TABLES = ["agent_states", "task_logs", "long_memory", "skills_index"]


def _env_diag() -> dict[str, bool]:
    """诊断: HF Secrets 注入探测 (不回显真值, 仅 presence)。"""
    return {
        "POSTGRES_HOST": bool(os.getenv("POSTGRES_HOST")),
        "POSTGRES_PORT": bool(os.getenv("POSTGRES_PORT")),
        "POSTGRES_USER": bool(os.getenv("POSTGRES_USER")),
        "POSTGRES_PASSWORD": bool(os.getenv("POSTGRES_PASSWORD")),
        "POSTGRES_DB": bool(os.getenv("POSTGRES_DB")),
    }


def _conn():
    """Neon Postgres 直连 (psycopg2)。"""
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty (HF Secrets missing)")
    return psycopg2.connect(
        host=host,
        port=int(os.getenv("POSTGRES_PORT", "5432")),
        user=os.getenv("POSTGRES_USER", ""),
        password=os.getenv("POSTGRES_PASSWORD", ""),
        dbname=os.getenv("POSTGRES_DB", "neondb"),
        sslmode="require",
        connect_timeout=10,
    )


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _ensure_tables(cur):
    """幂等建表 (如果 neon-schema.sql 没跑过)。"""
    cur.execute("""
        CREATE TABLE IF NOT EXISTS agent_states (
            thread_id   text PRIMARY KEY,
            state       jsonb   NOT NULL DEFAULT '{}'::jsonb,
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS task_logs (
            id          bigserial PRIMARY KEY,
            thread_id   text NOT NULL,
            space_name  text NOT NULL,
            action      text NOT NULL,
            status      text NOT NULL,
            request_id  text,
            created_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS long_memory (
            key         text PRIMARY KEY,
            value       jsonb   NOT NULL DEFAULT '{}'::jsonb,
            updated_at  timestamptz NOT NULL DEFAULT now()
        );
        CREATE TABLE IF NOT EXISTS skills_index (
            skill_name   text PRIMARY KEY,
            description  text,
            source       text,
            r2_key       text,
            usage_count  integer   NOT NULL DEFAULT 0,
            last_used    timestamptz
        );
    """)


def sync_once() -> dict[str, Any]:
    """周期同步: 本地 state.db → Neon 四表。

    实际上 hermes 的四表数据来自 hermes 进程内部写入。
    本脚本的主要作用是确保表存在 + 周期健康检查。
    """
    conn = _conn()
    conn.autocommit = True
    cur = conn.cursor(cursor_factory=RealDictCursor)

    # 幂等建表
    _ensure_tables(cur)

    counts: dict[str, Any] = {}
    ts = _now_iso()

    # 读取每表行数 (健康检查)
    for t in _TABLES:
        try:
            cur.execute(f'SELECT COUNT(*) AS cnt FROM public."{t}"')
            row = cur.fetchone()
            counts[t] = row["cnt"] if row else 0
        except Exception as e:
            counts[f"{t}_err"] = f"[{type(e).__name__}] {e}"

    # 写入 space_health 记录
    try:
        cur.execute("""
            CREATE TABLE IF NOT EXISTS space_health (
                id          bigserial PRIMARY KEY,
                space       text NOT NULL,
                status      text NOT NULL,
                detail      text,
                created_at  timestamptz NOT NULL DEFAULT now()
            );
        """)
        cur.execute(
            "INSERT INTO space_health (space, status, detail, created_at) VALUES (%s, %s, %s, %s)",
            ("hermes", "ok", json.dumps(counts), ts),
        )
    except Exception as e:
        counts["space_health_err"] = f"[{type(e).__name__}] {e}"

    counts["_ts"] = ts
    cur.close()
    conn.close()
    return counts


def main() -> None:
    print(f"[persist-neon] start, interval={_INTERVAL}s", flush=True)
    print(f"[persist-neon] env diag={_env_diag()}", flush=True)

    # 首次连接测试
    try:
        conn = _conn()
        conn.close()
        print("[persist-neon] Neon connection OK", flush=True)
    except Exception as e:
        print(f"[persist-neon] Neon connection FAILED: {e}", flush=True)

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
