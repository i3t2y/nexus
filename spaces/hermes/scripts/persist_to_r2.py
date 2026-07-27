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

完整性校验（#25）：每个 snapshot 上传后算 sha256 + 字节数，
写进 backup_snapshots 表与 R2 manifest 对象；restore 时复算比对。
"""
from __future__ import annotations

import hashlib
import json
import os
import sys
import time
from datetime import datetime, timezone
from typing import Any

sys.path.insert(0, "/home/user/app/libs")  # Space 内 PYTHONPATH 已设，本地调试兜底
import boto3  # noqa: E402
from botocore.config import Config  # noqa: E402
from supabase import create_client  # noqa: E402

_INTERVAL = int(os.getenv("SYNC_INTERVAL_SEC", "300"))
_BUCKET = os.getenv("R2_BACKUP_BUCKET", "nexus-backups")
_TABLES = ["agent_states", "task_logs", "long_memory", "skills_index"]
# manifest 索引对象 key（R2 内单文件，列各表最新快照 sha256/size，便于 restore 找最新）
_MANIFEST_KEY = "supabase-snapshot/_manifest.json"


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
    # R2 CopyObject 属 Class A 操作（计费），仅 Class B 出口免费。
    # 生产需注意额度：4 表 × 每次三步 ≈ 10 万 Class A/月仍属免费层 (100 万/月) 内。
    r2.copy_object(
        Bucket=_BUCKET,
        Key=key,
        CopySource={"Bucket": _BUCKET, "Key": tmp},
    )
    r2.delete_object(Bucket=_BUCKET, Key=tmp)


def _now_iso() -> str:
    """UTC ISO8601 时间戳（datetime 真实可用，与 errors.log_event 一致）。"""
    return datetime.now(timezone.utc).isoformat()


def sync_once() -> dict[str, Any]:
    r2 = _r2()
    supa = _supa()
    counts: dict[str, Any] = {}
    manifest: dict[str, Any] = {}
    ts = _now_iso()
    for t in _TABLES:
        try:
            rows = _snapshot_table(supa, t)
            body = json.dumps(rows, ensure_ascii=False, default=str).encode()
            key = f"supabase-snapshot/{t}.json"
            _atomic_upload(r2, key, body)
            # 完整性校验和：sha256 + 字节数；restore 时复算比对挡 R2 静默损坏/截断
            sha = hashlib.sha256(body).hexdigest()
            counts[t] = {"rows": len(rows), "sha256": sha, "bytes": len(body)}
            # 登记 backup_snapshots 元数据（恢复时按 table_name + created_at 定位快照）
            supa.table("backup_snapshots").insert({
                "table_name": t,
                "r2_key": key,
                "row_count": len(rows),
                "sha256": sha,
                "r2_size": len(body),
            }).execute()
            manifest[t] = {
                "r2_key": key,
                "sha256": sha,
                "bytes": len(body),
                "rows": len(rows),
                "updated_at": ts,
            }
        except Exception as e:  # noqa: BLE001
            counts[f"{t}_err"] = str(e)  # 记错误而非崩
    # 写 manifest 索引（各表最新 sha256/size），便于 restore 一步定位 + 完整性校对
    if manifest:
        try:
            _atomic_upload(r2, _MANIFEST_KEY, json.dumps(manifest, ensure_ascii=False).encode())
        except Exception as e:  # noqa: BLE001
            counts["_manifest_err"] = str(e)
    counts["_manifest_ts"] = ts
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
