"""R2 快照备份层(Neon 读源,2026-08-18 重构)。

原链(2026-08 前):Supabase 读 → R2 快照(双保险)。
2026-08-17 Supabase→Neon 全量迁移后 R2 daemon 切走,本脚本变死 code。
2026-08-18 恢复 R2 作快照备份层:读源从 Supabase 改 Neon HTTP /sql 端点。
2026-08-22 收口版合同简化:supabase-snapshot/ 可变路径 → snapshots/<ts>/ 不可变 blob,
   移除 _atomic_upload CAS,MANIFEST.json 纯指针(gen/ts/objects.*.key)。

与 persist_to_neon.py(主路)正交:
  - persist_to_neon.py = hermes 内部写 Neon 四表(主路持久)
  - 本脚本 = Neon 四表 → R2 JSON 快照(副路备份,灾备/审计)
  - 两 daemon 互独立,POSTGRES_HOST 单有 → 只主路;加 R2_ENDPOINT → 双起

元数据(manifest-only):sha256/bytes/rows/updated_at 全放 R2 MANIFEST.json objects,
**不进 Neon backup_snapshots 表**(Neon schema 不倒退,schema 七表已砍 backup_snapshots)。

环境变量:
  R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
  R2_BUCKET        (默认 nexus-checkpoints; 统一桶名)
  POSTGRES_HOST / POSTGRES_PORT / POSTGRES_USER / POSTGRES_PASSWORD / POSTGRES_DB
  R2_SYNC_INTERVAL_SEC (默认 1800 = 30分钟,快照层低频 R2 Class A 写 9600/天 << 免费额)
  (兼容回退: 未设 R2_SYNC_INTERVAL_SEC 时读 SYNC_INTERVAL_SEC,默认 1800)
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
import traceback
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, "/data/libs")  # Bucket 挂载点;Space 内 PYTHONPATH 已设,本地调试兜底
import boto3  # noqa: E402
import httpx  # noqa: E402
from botocore.config import Config  # noqa: E402

# R2 副路独立 env 名,与 Neon 主路 SYNC_INTERVAL_SEC 分离(避免 real-start.sh 同名污染)
_INTERVAL = int(os.getenv("R2_SYNC_INTERVAL_SEC") or os.getenv("SYNC_INTERVAL_SEC", "1800"))

# ── 优雅关机钩子(2026-08-18 Gork 总裁第一步 SIGTERM 短链补全) ──
# real-start.sh 停容器发 SIGTERM → 本 handler 设 _SHUTDOWN flag → while 循环
# 当前周期结束跑最后一次 sync_once flush 后 sys.exit(0)(无半截快照丢)。
# --once:单跑一轮 sync_once 后 exit(on_shutdown 可直调 python X.py --once)。
import signal

_SHUTDOWN = False
_ONCE = "--once" in sys.argv


def _on_sigterm(signum, frame):  # noqa: ANN001
    global _SHUTDOWN
    _SHUTDOWN = True
    print(f"[persist-r2] recv signal {signum}, graceful shutdown after current cycle...", flush=True)


signal.signal(signal.SIGTERM, _on_sigterm)
signal.signal(signal.SIGINT, _on_sigterm)


def _sleep_check(seconds):
    """1s 粒度睡,检 _SHUTDOWN flag 快响应(避免 INTERVAL 内装死)。"""
    for _ in range(seconds):
        if _SHUTDOWN:
            return
        time.sleep(1)


_BUCKET = os.getenv("R2_BUCKET", "nexus-checkpoints")
_TABLES = ["agent_states", "task_logs", "long_memory", "skills_index"]
# MANIFEST 指针(gen/ts/objects.*.key), snapshots/<ts>/ 不可变 blob 的索引
# 恢复端: 读 MANIFEST → objects.*.key → 下载; 或 list snapshots/ → max(ts)
_MANIFEST_KEY = "MANIFEST.json"


def _env_diag() -> dict[str, bool]:
    """诊断:HF Secrets 注入探测(不回显真值,仅 presence)。"""
    return {
        "R2_ENDPOINT": bool(os.getenv("R2_ENDPOINT")),
        "R2_ACCESS_KEY_ID": bool(os.getenv("R2_ACCESS_KEY_ID")),
        "R2_SECRET_ACCESS_KEY": bool(os.getenv("R2_SECRET_ACCESS_KEY")),
        "POSTGRES_HOST": bool(os.getenv("POSTGRES_HOST")),
        "POSTGRES_PORT": bool(os.getenv("POSTGRES_PORT", "5432")),
        "POSTGRES_USER": bool(os.getenv("POSTGRES_USER")),
        "POSTGRES_PASSWORD": bool(os.getenv("POSTGRES_PASSWORD")),
        "POSTGRES_DB": bool(os.getenv("POSTGRES_DB", "neondb")),
    }


def _r2():
    if not os.getenv("R2_ACCESS_KEY_ID") or not os.getenv("R2_SECRET_ACCESS_KEY"):
        raise RuntimeError("[_r2] R2_ACCESS_KEY_ID or R2_SECRET_ACCESS_KEY empty (HF Secrets missing)")
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("R2_ENDPOINT"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
        region_name=os.getenv("R2_REGION", "auto"),
        config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
    )


# ── Neon HTTP /sql 读源(复制 persist_to_neon.py 同款) ──────────────────────────
def _conn_str() -> str:
    """构建 Neon 连接串(用于 Neon-Connection-String header)。"""
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty (HF Secrets missing)")
    # Neon HTTP /sql 要求 non-pooler host(strip -pooler suffix)
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
    """执行单条 SQL via Neon HTTP /sql 端点。每次 = 独立 HTTP POST,完即断,不占连接。"""
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


def _snapshot_table_neon(table: str) -> list[dict[str, Any]]:
    """读 Neon 单表全行 → dict 列表。

    注意:
      - task_logs 累积大超 30s timeout 风险 → ORDER BY id DESC LIMIT 10000 截断(已知风险)
      - row_to_json 对 jsonb 列序列化为字符串而非嵌套对象;persist 端 json.dumps 后整串存,
        restore 端需 json.loads 透传(jsonb 列以字符串形式存快照,读回 Neon 时 $1::jsonb 还原)
    """
    if table == "task_logs":
        q = f'SELECT row_to_json(t) AS row FROM public."{table}" t ORDER BY id DESC LIMIT 10000'
    else:
        q = f'SELECT row_to_json(t) AS row FROM public."{table}" t'
    rows = _neon_query(q)
    # row_to_json 返回 {"row": {...}},提取
    return [r["row"] if isinstance(r, dict) and "row" in r else r for r in rows]


def _now_iso() -> str:
    """UTC ISO8601 时间戳(datetime 真实可用,与 errors.log_event 一致)。"""
    return datetime.now(timezone.utc).isoformat()


def _read_manifest(r2) -> dict[str, Any]:
    """读当前 MANIFEST.json,返回 {gen, ts, objects} 或默认 gen=0。"""
    try:
        resp = r2.get_object(Bucket=_BUCKET, Key=_MANIFEST_KEY)
        return json.loads(resp["Body"].read())
    except r2.exceptions.NoSuchKey:
        return {"gen": 0, "ts": _now_iso(), "objects": {}}


def sync_once() -> dict[str, Any]:
    r2 = _r2()
    counts: dict[str, Any] = {}
    manifest = _read_manifest(r2)
    gen = manifest.get("gen", 0) + 1
    ts = _now_iso()
    snap_dir = f"snapshots/{ts}/"
    objects: dict[str, Any] = {}
    for t in _TABLES:
        try:
            rows = _snapshot_table_neon(t)
        except Exception as e:  # noqa: BLE001
            counts[f"{t}_neon_err"] = f"[{type(e).__name__}] {e}"
            continue
        try:
            body = json.dumps(rows, ensure_ascii=False, default=str).encode()
            key = f"{snap_dir}{t}.json"
            # 不可变 blob:直接 PUT,无 CAS 无 tmp→copy 模拟(收口合同:同时只开一台 Hermes)
            r2.put_object(Bucket=_BUCKET, Key=key, Body=body)
            sha = hashlib.sha256(body).hexdigest()
            counts[t] = {"rows": len(rows), "sha256": sha, "bytes": len(body)}
            objects[t] = {
                "key": key,
                "sha256": sha,
                "bytes": len(body),
                "rows": len(rows),
            }
        except Exception as e:  # noqa: BLE001
            counts[f"{t}_r2_err"] = f"[{type(e).__name__}] {e}"
            continue
    # 写 MANIFEST 指针:gen 递增,ts 当前,objects 指向各表不可变 blob
    if objects:
        manifest = {
            "gen": gen,
            "ts": ts,
            "objects": objects,
        }
        try:
            r2.put_object(Bucket=_BUCKET, Key=_MANIFEST_KEY, Body=json.dumps(manifest, ensure_ascii=False).encode())
        except Exception as e:  # noqa: BLE001
            counts["_manifest_err"] = str(e)
    counts["_gen"] = gen
    counts["_snapshots_ts"] = ts
    return counts


def main() -> None:
    # boot 门控:R2_* + POSTGRES_* 四 presence 缺则 raise(real-start.sh 门控 skip 不进 main)
    missing = [
        k for k, v in _env_diag().items()
        if not v and k in ("R2_ENDPOINT", "R2_ACCESS_KEY_ID", "R2_SECRET_ACCESS_KEY", "POSTGRES_HOST")
    ]
    if missing:
        raise RuntimeError(f"[persist-r2] boot gate fail, missing env: {missing}")
    print(f"[persist-r2] start, interval={_INTERVAL}s, bucket={_BUCKET}", flush=True)
    print(f"[persist-r2] env diag={_env_diag()}", flush=True)
    # 首次 Neon 连接测试
    try:
        rows = _neon_query("SELECT 1 AS ok")
        print(f"[persist-r2] Neon HTTP /sql connection OK: {rows}", flush=True)
    except Exception as e:
        print(f"[persist-r2] Neon HTTP /sql connection FAILED: {e}", flush=True)
    _did_once = False
    while not _SHUTDOWN:
        try:
            res = sync_once()
            print(f"[persist-r2] synced {res}", flush=True)
            _did_once = True
        except Exception as e:  # noqa: BLE001
            etype = type(e).__name__
            msg = str(e)
            src = "?"
            if msg.startswith("[_r2]"):
                src = "R2"
            elif msg.startswith("[neon]"):
                src = "NEON"
            tb = traceback.format_exc().splitlines()
            tb_short = " | ".join(tb[-3:]) if len(tb) >= 3 else " | ".join(tb)
            print(f"[persist-r2] fatal[{src}/{etype}] {msg} | tb={tb_short} | env={_env_diag()}", flush=True)
        if _ONCE and _did_once:
            print("[persist-r2] --once done, exit 0", flush=True)
            sys.exit(0)
        if _SHUTDOWN:
            break
        _sleep_check(_INTERVAL)
    # 关机 final flush(收 SIGTERM 后补跑一轮确保最后快照不丢)
    try:
        sync_once()
        print("[persist-r2] final flush ok on shutdown, exit 0", flush=True)
    except Exception as e:
        print(f"[persist-r2] final flush fail: {e}", flush=True)
    sys.exit(0)


if __name__ == "__main__":
    main()
