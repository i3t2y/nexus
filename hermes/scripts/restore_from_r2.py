"""R2 → Neon 反向恢复(2026-08-22 snapshots/<ts>/ 简化)。

persist_to_r2.py 把 Neon 四表周期快照到 R2 snapshots/<ts>/ 不可变 blob;
本脚本做反向闭环:读 MANIFEST.json → objects.*.key → 下载 → 复算 sha256 对比
→ upsert 回 Neon 表(via HTTP /sql INSERT ... ON CONFLICT)。

manifest-only(D2):元数据(sha256/bytes/rows)只放 R2 MANIFEST.json objects,
不查 Neon 表。校验对比从 manifest.objects 取该表登记,无登记则降级放行。

命令行:
  python restore_from_r2.py --all                 # 恢复全部 _TABLES
  python restore_from_r2.py --table agent_states  # 仅恢复某表
  python restore_from_r2.py --list                # 列 manifest 内容(只读)
  python restore_from_r2.py --table foo --verify-only  # 仅校验 sha256 不写

幂等:重跑安全(ON CONFLICT 主键覆盖)。--verify-only 可干跑校下沉链路。

环境变量同 persist_to_r2.py(R2_*、POSTGRES_*)。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any

sys.path.insert(0, "/data/libs")  # Bucket 挂载点;Space 内 PYTHONPATH 已设,本地调试兜底
import boto3  # noqa: E402
import botocore.exceptions  # noqa: E402
import httpx  # noqa: E402
from botocore.config import Config  # noqa: E402

_BUCKET = os.getenv("R2_BUCKET", "nexus-checkpoints")
_TABLES = ["agent_states", "task_logs", "long_memory", "skills_index"]
_MANIFEST_KEY = "MANIFEST.json"
# 主键列名(ON CONFLICT 需要)+ 空 pk 列用 insert 而非 upsert
_PK: dict[str, str | None] = {
    "agent_states": "thread_id",
    "long_memory": "key",
    "skills_index": "skill_name",
    "task_logs": None,  # bigserial id 代理键,恢复时不覆盖 → 用纯 insert
    "backup_snapshots": "id",
    "space_health": "id",
}

# 安全门:默认不恢复 task_logs/空间健康表的代理键表(id 重复会撞主键 → insert 报错,
# 不该靠备份覆盖自增历史)。仅 agent_states/long_memory/skills_index 走 upsert 全量覆盖。
_SAFE_RESTORE = {"agent_states", "long_memory", "skills_index"}

# 各表非 pk 列(jsonb 列回写需 $N::jsonb 强转),按 neon-schema.sql 列序
_COLS: dict[str, list[tuple[str, str]]] = {
    "agent_states": [("state", "jsonb"), ("updated_at", "timestamptz")],
    "long_memory": [("value", "jsonb"), ("updated_at", "timestamptz")],
    "skills_index": [
        ("description", "text"), ("source", "text"), ("r2_key", "text"),
        ("usage_count", "integer"), ("last_used", "timestamptz"),
    ],
}


def _r2():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("R2_ENDPOINT"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
        region_name=os.getenv("R2_REGION", "auto"),
        config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
    )


# ── Neon HTTP /sql 写回(复制 persist_to_r2.py 同款) ──────────────────────────
def _conn_str() -> str:
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty (HF Secrets missing)")
    if host.endswith("-pooler"):
        host = host[: -len("-pooler")]
    user = os.getenv("POSTGRES_USER", "")
    password = os.getenv("POSTGRES_PASSWORD", "")
    db = os.getenv("POSTGRES_DB", "neondb")
    port = os.getenv("POSTGRES_PORT", "5432")
    return f"postgresql://{user}:{password}@{host}:{port}/{db}?sslmode=require"


def _sql_url() -> str:
    host = os.getenv("POSTGRES_HOST", "")
    if not host:
        raise RuntimeError("[neon] POSTGRES_HOST empty (HF Secrets missing)")
    if host.endswith("-pooler"):
        host = host[: -len("-pooler")]
    return f"https://{host}/sql"


def _neon_query(query: str, params: list | None = None) -> list[dict]:
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
        if isinstance(data, dict) and "rows" in data:
            return data["rows"]
        return []


def _get_manifest(r2) -> dict[str, Any] | None:
    """读 R2 MANIFEST.json;返回 {gen, ts, objects} 或 None。"""
    try:
        resp = r2.get_object(Bucket=_BUCKET, Key=_MANIFEST_KEY)
        return json.loads(resp["Body"].read())
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return None
        raise


def _get_snapshot_bytes(r2, key: str) -> bytes | None:
    """读取 R2 快照 blob。不存在返回 None（json.loads 调用方自行兜底）。"""
    try:
        return r2.get_object(Bucket=_BUCKET, Key=key)["Body"].read()
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return None
        raise


def _verify(table: str, raw: bytes, manifest: dict[str, Any] | None) -> tuple[bool, str]:
    """复算 sha256 + 字节数,对比 MANIFEST.objects[table] 登记值。manifest=None/无该表降级放行。

    manifest-only:元数据不进 Neon,校对源 = MANIFEST.json objects。
    """
    sha = hashlib.sha256(raw).hexdigest()
    nsize = len(raw)
    if manifest is None:
        return True, f"无 manifest(降级放行)sha256={sha}"
    meta = (manifest.get("objects") or {}).get(table)
    if meta is None:
        return True, f"manifest.objects 无 {table} 登记(降级放行)sha256={sha}"
    reg_sha = (meta.get("sha256") or "").strip()
    reg_size = meta.get("bytes")
    reg_rows = meta.get("rows")
    if not reg_sha:
        return True, f"无登记 sha256(降级)实测 sha256={sha} bytes={nsize}"
    if sha != reg_sha:
        return False, f"sha256 不符 实测={sha} 登记={reg_sha}"
    if reg_size is not None and reg_size != nsize:
        return False, f"字节数不符 实测={nsize} 登记={reg_size}"
    return True, f"sha256 匹配 ({sha}) bytes={nsize} rows={reg_rows}"


def _restore_table(r2, manifest, table: str, verify_only: bool) -> dict[str, Any]:
    """单表恢复。返回 {table, rows, verify_ok, msg, restored}。

    从 manifest.objects[table].key 读取不可变 blob,不走 `supabase-snapshot/{table}.json` 硬编码。
    写回用 Neon HTTP /sql INSERT ... ON CONFLICT (pk) DO UPDATE SET。
    jsonb 列回写需 $N::jsonb 强转(row_to_json 序列化为字符串)。
    task_logs(bigserial 代理键)不写回(保持 _SAFE_RESTORE 语义)。
    """
    out: dict[str, Any] = {"table": table, "rows": 0, "verify_ok": False, "restored": False}
    # 从 manifest.objects 读该表 blob key;无 objects 段降级到 supabase-snapshot/ 兼容旧快照
    obj_meta = (manifest.get("objects") or {}).get(table) if manifest else None
    if obj_meta and obj_meta.get("key"):
        key = obj_meta["key"]
    else:
        key = f"supabase-snapshot/{table}.json"
    try:
        raw = _get_snapshot_bytes(r2, key)
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        out["msg"] = f"读取快照失败 ({code}): {e}"
        return out
    if raw is None:
        out["msg"] = f"快照对象不存在(key={key})"
        return out
    try:
        rows = json.loads(raw)
    except (json.JSONDecodeError, ValueError, TypeError) as e:
        out["msg"] = f"快照 JSON 解析失败: {e}"
        return out
    out["rows"] = len(rows)
    # 完整性校验(比对 R2 manifest 登记)
    ok, msg = _verify(table, raw, manifest)
    out["verify_ok"] = ok
    out["msg"] = msg
    if not ok:
        return out  # 校验失败拒绝写回,防坏数据覆盖好数据
    if verify_only:
        return out
    if not rows:
        out["msg"] = "空快照,跳过写回(防把表清空)"
        return out
    pk = _PK.get(table)
    if table not in _SAFE_RESTORE:
        out["msg"] = f"恢复≈写回暂未支持该表类型 (pk={pk});仅校验通过"
        return out
    if not pk:
        out["msg"] = "该表无可用主键列,跳过写回"
        return out
    cols = _COLS.get(table)
    if not cols:
        out["msg"] = f"无列定义({table}),跳过写回"
        return out
    try:
        # 逐行 upsert via Neon HTTP /sql。
        # 列序:pk + cols(jsonb 列 $N::jsonb 强转 row_to_json 字符串回 jsonb)。
        col_names = [pk] + [c[0] for c in cols]
        placeholders = ["$1"] + [f"${i + 2}::jsonb" if c[1] == "jsonb" else f"${i + 2}"
                                for i, c in enumerate(cols)]
        update_set = ", ".join(f"{c[0]} = EXCLUDED.{c[0]}" for c in cols)
        sql = (
            f'INSERT INTO public."{table}" ({", ".join(col_names)}) '
            f"VALUES ({', '.join(placeholders)}) "
            f"ON CONFLICT ({pk}) DO UPDATE SET {update_set}"
        )
        for row in rows:
            params = [row.get(pk)]
            for c, _ in cols:
                v = row.get(c[0])
                # jsonb 列:row_to_json 序列化为字符串,回写 $N::jsonb 还原;无值用 '{}' 兜底
                if c[1] == "jsonb":
                    if v is None:
                        v = "{}"
                    elif not isinstance(v, str):
                        v = json.dumps(v, ensure_ascii=False, default=str)
                params.append(v)
            _neon_query(sql, params)
        out["restored"] = True
        out["msg"] = f"写回 {len(rows)} 行 → Neon public.\"{table}\""
    except Exception as e:  # noqa: BLE001
        out["msg"] = f"写回失败: {e}"
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="R2 → Neon 反向恢复")
    p.add_argument("--all", action="store_true", help="恢复全部表")
    p.add_argument("--table", help="仅恢复指定表")
    p.add_argument("--list", action="store_true", help="列 manifest 内容(只读,不恢复)")
    p.add_argument("--verify-only", action="store_true", help="仅校验 sha256 不写回")
    args = p.parse_args()

    if not (args.all or args.table or args.list):
        p.error("需指定 --all / --table <name> / --list 之一")

    if not (os.getenv("R2_ENDPOINT") and os.getenv("POSTGRES_HOST")):
        print("R2_ENDPOINT / POSTGRES_HOST 未配置,无法恢复", flush=True)
        return 2

    r2 = _r2()

    if args.list:
        m = _get_manifest(r2)
        if m is None:
            print(f"(R2 无 manifest 对象 {_MANIFEST_KEY};可能首次快照未运行或 bucket 不一致)", flush=True)
            return 0
        print(json.dumps(m, ensure_ascii=False, indent=2), flush=True)
        return 0

    tables = _TABLES if args.all else [args.table]
    if args.table and args.table not in _TABLES and not _PK.get(args.table):
        print(f"未知表 {args.table}(已知表:{', '.join(_TABLES)})", flush=True)
        return 2

    # manifest 一次性取,校验复用(manifest-only D2)
    manifest = _get_manifest(r2)

    overall_ok = True
    for t in tables:
        if t is None:
            continue
        res = _restore_table(r2, manifest, t, verify_only=args.verify_only)
        tag = "OK" if (res["verify_ok"] and (res["restored"] or args.verify_only)) else "FAIL"
        if not res["verify_ok"]:
            overall_ok = False
        print(
            f"[restore] {tag} table={res['table']} rows={res['rows']} "
            f"verify_ok={res['verify_ok']} restored={res['restored']} msg={res['msg']}",
            flush=True,
        )
    return 0 if overall_ok else 1


if __name__ == "__main__":
    raise SystemExit(main())
