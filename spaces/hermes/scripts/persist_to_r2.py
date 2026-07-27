"""R2 + Supabase 双写持久化同步。

借鉴 HermesFace 的 hermes_persist.py / HuggingMes 的 hermes-sync.py，
改为：结构化状态读自 Supabase → 周期性快照写 R2（原子覆盖）。
解决 HF Space 重启 / 休眠导致本地数据丢失（本方案核心数据已在 R2/Supabase，
此脚本再做一层 Supabase→R2 的备份快照，双保险）。

环境变量：
  R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
  R2_BACKUP_BUCKET  (默认 nexus-backups)
  SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
  SYNC_INTERVAL_SEC (默认 300 = 5分钟)
"""
from __future__ import annotations

import json
import os
import sys
import time
from typing import Any

sys.path.insert(0, "/home/user/app/libs")  # Space 内 PYTHONPATH 已设，本地调试兜底
import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402
from supabase import create_client  # noqa: E402

_INTERVAL = int(os.getenv("SYNC_INTERVAL_SEC", "300"))
_BUCKET = os.getenv("R2_BACKUP_BUCKET", "nexus-backups")
_TABLES = ["agent_states", "task_logs", "long_memory", "skills_index"]


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


def _snapshot_table(supa, table: str) -> list[dict[str, Any]]:
    res = supa.table(table).select("*").execute()
    return res.data or []


def _atomic_upload(r2, key: str, body: bytes) -> None:
    """原子上传：先写 tmp key 再 copy 到目标 key。
    借鉴 HermesFace save_to_dataset_atomic 思路（HF Dataset 无原子写，R2 用 copy 模拟）。
    避免读到写一半的文件。
    """
    tmp = f"_tmp/{key}.partial"
    r2.put_object(Bucket=_BUCKET, Key=tmp, Body=body)
    # R2 不收费 copy，原子替换
    r2.copy_object(
        Bucket=_BUCKET,
        Key=key,
        CopySource={"Bucket": _BUCKET, "Key": tmp},
    )
    r2.delete_object(Bucket=_BUCKET, Key=tmp)


def sync_once() -> dict[str, int]:
    r2 = _r2()
    supa = _supa()
    counts = {}
    for t in _TABLES:
        try:
            rows = _snapshot_table(supa, t)
            _atomic_upload(
                r2,
                f"supabase-snapshot/{t}.json",
                json.dumps(rows, ensure_ascii=False, default=str).encode(),
            )
            counts[t] = len(rows)
            # 登记 backup_snapshots 元数据（恢复时按 table_name + created_at 定位快照）
            supa.table("backup_snapshots").insert({
                "table_name": t,
                "r2_key": f"supabase-snapshot/{t}.json",
                "row_count": len(rows),
            }).execute()
        except Exception as e:  # noqa: BLE001
            counts[f"{t}_err"] = str(e)  # 记错误而非崩
    return counts


def main() -> None:
    print(f"[persist] start, interval={_INTERVAL}s, bucket={_BUCKET}", flush=True)
    while True:
        try:
            res = sync_once()
            print(f"[persist] synced {res}", flush=True)
        except Exception as e:  # noqa: BLE001
            print(f"[persist] fatal {e}", flush=True)
        time.sleep(_INTERVAL)


if __name__ == "__main__":
    main()
