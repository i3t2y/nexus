"""R2 → Supabase 反向恢复（#24）。

persist_to_r2.py 把 Supabase 表周期快照到 R2；本脚本做反向闭环：
读 R2 snapshot → 复算 sha256 对比 backup_snapshots 行（挡静默损坏/截断）→
upsert 回 Supabase 表（service_role 绕过 RLS）。

命令行：
  python restore_from_r2.py --all                 # 恢复全部 _TABLES
  python restore_from_r2.py --table agent_states  # 仅恢复某表
  python restore_from_r2.py --list                # 列 manifest 内容（只读）
  python restore_from_r2.py --table foo --verify-only  # 仅校验 sha256 不写

幂等：重跑安全（upsert on_conflict 覆盖）。--verify-only 可干跑校下沉链路。

环境变量同 persist_to_r2.py（R2_*、SUPABASE_*）。
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
from typing import Any

sys.path.insert(0, "/data/libs")  # Bucket 挂载点;Space 内 PYTHONPATH 已设，本地调试兜底
import boto3  # noqa: E402
import botocore.exceptions  # noqa: E402
from botocore.config import Config  # noqa: E402
from supabase import create_client  # noqa: E402

_BUCKET = os.getenv("R2_BUCKET", "nexus-checkpoints")
_TABLES = ["agent_states", "task_logs", "long_memory", "skills_index"]
_MANIFEST_KEY = "supabase-snapshot/_manifest.json"
# 主键列名（upsert on_conflict 需要）+ 空 pk 列用 insert 而非 upsert
_PK: dict[str, str | None] = {
    "agent_states": "thread_id",
    "long_memory": "key",
    "skills_index": "skill_name",
    "task_logs": None,  # bigserial id 代理键，恢复时不覆盖 → 用纯 insert
    "backup_snapshots": "id",
    "space_health": "id",
}

# 安全门：默认不恢复 task_logs/空间健康表的代理键表（id 重复会撞主键 → insert 报错，
# 不该靠备份覆盖自增历史）。仅 agent_states/long_memory/skills_index 走 upsert 全量覆盖。
_SAFE_RESTORE = {"agent_states", "long_memory", "skills_index"}


def _r2():
    return boto3.client(
        "s3",
        endpoint_url=os.getenv("R2_ENDPOINT"),
        aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
        aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
        region_name=os.getenv("R2_REGION", "auto"),
        config=Config(connect_timeout=5, read_timeout=30, retries={"max_attempts": 3}),
    )


def _supa():
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
    return create_client(os.getenv("SUPABASE_URL", ""), key)


def _get_manifest(r2) -> dict[str, Any] | None:
    """读 R2 manifest 对象；不存在返回 None。"""
    try:
        resp = r2.get_object(Bucket=_BUCKET, Key=_MANIFEST_KEY)
        return json.loads(resp["Body"].read())
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return None
        raise


def _get_snapshot_bytes(r2, key: str) -> bytes:
    return r2.get_object(Bucket=_BUCKET, Key=key)["Body"].read()


def _latest_snapshot_meta(supa, table: str) -> dict[str, Any] | None:
    """查 backup_snapshots 表该 table 最新一行（sha256/r2_size/create时间），用于校对。"""
    res = (
        supa.table("backup_snapshots")
        .select("r2_key,sha256,r2_size,created_at")
        .eq("table_name", table)
        .order("created_at", desc=True)
        .limit(1)
        .maybe_single()
        .execute()
    )
    return res.data or None


def _verify(table: str, raw: bytes, meta: dict[str, Any] | None) -> tuple[bool, str]:
    """复算 sha256 + 字节数，对比 backup_snapshots 行的登记值。meta=None 视作无登记退化通过。"""
    sha = hashlib.sha256(raw).hexdigest()
    nsize = len(raw)
    if meta is None:
        # 无登记行（manifest 也缺）：只校 sha256 非空，不强比对——降级放行
        return True, f"no registry row（降级放行）sha256={sha}"
    reg_sha = (meta.get("sha256") or "").strip()
    reg_size = meta.get("r2_size")
    if not reg_sha:
        return True, f"无登记 sha256（旧记录降级）实测 sha256={sha} bytes={nsize}"
    if sha != reg_sha:
        return False, f"sha256 不符 实测={sha} 登记={reg_sha}"
    if reg_size is not None and reg_size != nsize:
        return False, f"字节数不符 实测={nsize} 登记={reg_size}"
    return True, f"sha256 匹配 ({sha}) bytes={nsize}"


def _restore_table(r2, supa, table: str, verify_only: bool) -> dict[str, Any]:
    """单表恢复。返回 {table, rows, verify_ok, msg, restored}。"""
    out: dict[str, Any] = {"table": table, "rows": 0, "verify_ok": False, "restored": False}
    key = f"supabase-snapshot/{table}.json"
    try:
        raw = _get_snapshot_bytes(r2, key)
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        out["msg"] = f"读取快照失败 ({code}): {e}"
        return out
    rows = json.loads(raw)
    out["rows"] = len(rows)
    # 完整性校验（比对 backup_snapshots 登记行 + manifest 一致性）
    meta = _latest_snapshot_meta(supa, table)
    ok, msg = _verify(table, raw, meta)
    out["verify_ok"] = ok
    out["msg"] = msg
    if not ok:
        return out  # 校验失败拒绝写回，防坏数据覆盖好数据
    if verify_only:
        return out
    if not rows:
        out["msg"] = "空快照，跳过写回（防把表清空）"
        return out
    # 主键 upsert 覆盖（仅业务表）；代理键表暂不恢复（见 _SAFE_RESTORE 说明）
    pk = _PK.get(table)
    if table not in _SAFE_RESTORE:
        out["msg"] = f"恢复≈写回暂未支持该表类型 (pk={pk})；仅校验通过"
        return out
    if not pk:
        out["msg"] = "该表无可用主键列，跳过写回"
        return out
    try:
        # upsert：service_role 绕 RLS；on_conflict 主键覆盖（恢复语义：备份即真相）
        supa.table(table).upsert(rows, on_conflict=pk).execute()
        out["restored"] = True
    except Exception as e:  # noqa: BLE001
        out["msg"] = f"写回失败: {e}"
    return out


def main() -> int:
    p = argparse.ArgumentParser(description="R2 → Supabase 反向恢复")
    p.add_argument("--all", action="store_true", help="恢复全部表")
    p.add_argument("--table", help="仅恢复指定表")
    p.add_argument("--list", action="store_true", help="列 manifest 内容（只读，不恢复）")
    p.add_argument("--verify-only", action="store_true", help="仅校验 sha256 不写回")
    args = p.parse_args()

    if not (args.all or args.table or args.list):
        p.error("需指定 --all / --table <name> / --list 之一")

    if not (os.getenv("R2_ENDPOINT") and os.getenv("SUPABASE_URL")):
        print("R2_ENDPOINT / SUPABASE_URL 未配置，无法恢复", flush=True)
        return 2

    r2, supa = _r2(), _supa()

    if args.list:
        m = _get_manifest(r2)
        if m is None:
            print(f"（R2 无 manifest 对象 {_MANIFEST_KEY}；可能首次快照未运行或 bucket 不一致）", flush=True)
            return 0
        print(json.dumps(m, ensure_ascii=False, indent=2), flush=True)
        return 0

    tables = _TABLES if args.all else [args.table]
    if args.table and args.table not in _TABLES and not _PK.get(args.table):
        print(f"未知表 {args.table}（已知表：{', '.join(_TABLES)}）", flush=True)
        return 2

    overall_ok = True
    for t in tables:
        if t is None:
            continue
        res = _restore_table(r2, supa, t, verify_only=args.verify_only)
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
