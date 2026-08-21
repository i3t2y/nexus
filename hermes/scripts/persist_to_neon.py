"""Neon DDL 初始化 + 轻量健康检查（2026-08-22 去心跳版）。

原链(2026-08-17): 周期同步 daemon, 每 600s 写 space_health + 4 次 COUNT(*)。
2026-08-22 收口版合同: 移除所有定时 SQL 心跳, 让 Neon 自然休眠。
  本脚本职责收窄到:
  1. --init: boot 期幂等建表 (agent_states/task_logs/long_memory/skills_index)
  2. --once: 单次健康检查 (SELECT COUNT(*) 不写 space_health)
  3. daemon 模式: 仅保留 SIGTERM 钩子兼容 old real-start.sh, 主体只 sleep 不做 SQL

砍掉:
  - space_health 表 DDL + INSERT (Neon 心跳源)
  - 周期 COUNT(*) (除非显式 --once)
  - Supabase 残留

环境变量:
  POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
  SYNC_INTERVAL_SEC (仅 daemon 模式 sleep 间隔, 默认 600)

表: agent_states, task_logs, long_memory, skills_index (无 space_health)
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

# ── 优雅关机钩子(2026-08-18 Gork 总裁第一步 SIGTERM 短链补全) ──
# real-start.sh 停容器发 SIGTERM → 本 handler 设 _SHUTDOWN flag → while 循环
# 当前周期结束跑最后一次 sync_once flush 后 sys.exit(0)(无半截状态丢)。
# --once:单跑一轮 sync_once 后 exit(on_shutdown 可直调 python X.py --once)。
import signal

_SHUTDOWN = False
_ONCE = "--once" in sys.argv


def _on_sigterm(signum, frame):  # noqa: ANN001
    global _SHUTDOWN
    _SHUTDOWN = True
    print(f"[persist-neon] recv signal {signum}, graceful shutdown after current cycle...", flush=True)


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigterm)


def _sleep_check(seconds):
    """1s 粒度睡,检 _SHUTDOWN flag 快响应(避免 INTERVAL 内装死)。"""
    for _ in range(seconds):
        if _SHUTDOWN:
            return
        time.sleep(1)


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

    2026-08-22: 移除 space_health DDL (Neon 心跳源)。
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
    ]
    for ddl in ddls:
        try:
            _neon_query(ddl)
        except Exception as e:
            print(f"[persist-neon] DDL failed: {e}", flush=True)


def sync_once() -> dict[str, Any]:
    """单次健康检查: 仅 SELECT COUNT(*) 不写 space_health。

    2026-08-22: 移除 space_health INSERT + 仅 --once 显式调用时跑 SQL,
    daemon 模式不再执行任何 SQL (让 Neon 自然休眠)。
    """
    counts: dict[str, Any] = {}
    ts = _now_iso()
    for t in _TABLES:
        try:
            rows = _neon_query(f'SELECT COUNT(*) AS cnt FROM public."{t}"')
            counts[t] = rows[0]["cnt"] if rows else 0
        except Exception as e:
            counts[f"{t}_err"] = f"[{type(e).__name__}] {e}"
    counts["_ts"] = ts
    return counts


def main() -> None:
    _INIT = "--init" in sys.argv

    print(f"[persist-neon] start, interval={_INTERVAL}s (HTTP /sql mode)", flush=True)
    print(f"[persist-neon] env diag={_env_diag()}", flush=True)

    # 连接测试 + 幂等建表 (boot 期一次)
    try:
        rows = _neon_query("SELECT 1 AS ok")
        print(f"[persist-neon] Neon HTTP /sql connection OK: {rows}", flush=True)
        _ensure_tables()
        print("[persist-neon] tables ensured", flush=True)
    except Exception as e:
        print(f"[persist-neon] Neon connection/DDL FAILED: {e}", flush=True)
        if _INIT:
            sys.exit(1)

    if _INIT:
        print("[persist-neon] --init done, exit 0", flush=True)
        sys.exit(0)

    # daemon 模式: 不再执行定时 SQL, 仅保留 SIGTERM 兼容
    # 2026-08-22 收口版合同: 禁止 Neon 定时心跳, 让 Neon 自然休眠。
    # 此 daemon 仅保持进程存活供 real-start.sh trap 兼容, 不做任何 SQL。
    _did_once = False
    while not _SHUTDOWN:
        if _ONCE:
            try:
                res = sync_once()
                print(f"[persist-neon] synced {res}", flush=True)
                _did_once = True
            except Exception as e:
                etype = type(e).__name__
                msg = str(e)
                tb = traceback.format_exc().splitlines()
                tb_short = " | ".join(tb[-3:]) if len(tb) >= 3 else " | ".join(tb)
                print(f"[persist-neon] fatal[{etype}] {msg} | tb={tb_short}", flush=True)
            if _did_once:
                print("[persist-neon] --once done, exit 0", flush=True)
                sys.exit(0)
        # daemon 模式: 只 sleep, 不碰 Neon
        if _SHUTDOWN:
            break
        _sleep_check(_INTERVAL)
    # 关机 (不跑 SQL, 仅 exit)
    print("[persist-neon] shutdown, exit 0", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
