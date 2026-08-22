"""统一存储工具：R2 大文件（2026-08-22 收口精简版）。

各 Space 通过 sys.path 引入本目录，或打包成包安装。
对外暴露：r2_client、save_checkpoint、load_checkpoint、presigned_get、dumps。
所有凭证从环境变量读，不硬编码。

2026-08-22 清理：移除全部 Supabase 路径（supabase_client / save_state / load_state /
log_task / enqueue_task / claim_task / complete_task / load_task / remember / recall）。
单 Space 收口后 Supabase 废弃，真相源 = Mem0 向量 + MEMORY.md + skills + task_queue；
agent_states/task_logs/long_memory 四表在 Neon（persist_to_r2 快照层管），不再经 Supabase。
遗留调用方：nexus-ops plugin（探活已取消下游 + 查废弃 Supabase）一并归 old/。
"""
from __future__ import annotations

import json
import os

import boto3
from botocore.config import Config

# ── R2 ──────────────────────────────────────────────────────────────
_R2: boto3.client | None = None  # type: ignore[type-arg]


def r2_client():
    """惰性初始化 R2 (S3 兼容) 客户端。"""
    global _R2
    if _R2 is None:
        endpoint = os.getenv("R2_ENDPOINT")
        if not endpoint:
            raise RuntimeError("R2_ENDPOINT 未设置")
        _R2 = boto3.client(
            "s3",
            endpoint_url=endpoint,
            aws_access_key_id=os.getenv("R2_ACCESS_KEY_ID", ""),
            aws_secret_access_key=os.getenv("R2_SECRET_ACCESS_KEY", ""),
            region_name=os.getenv("R2_REGION", "auto"),
            config=Config(
                # R2 不支持 accelerate，连接超时兜底
                connect_timeout=5,
                read_timeout=30,
                retries={"max_attempts": 3},
            ),
        )
    return _R2


def save_checkpoint(thread_id: str, data: bytes | str, bucket: str | None = None) -> str:
    """保存 Checkpoint blob 到 R2。返回对象 key。"""
    bucket = bucket or os.getenv("R2_BUCKET", "nexus-checkpoints")
    key = f"{thread_id}.json"
    body = data.encode() if isinstance(data, str) else data
    r2_client().put_object(Bucket=bucket, Key=key, Body=body)
    return key


def load_checkpoint(thread_id: str, bucket: str | None = None) -> bytes | None:
    """读取 Checkpoint。不存在返回 None。"""
    import botocore.exceptions

    bucket = bucket or os.getenv("R2_BUCKET", "nexus-checkpoints")
    try:
        resp = r2_client().get_object(Bucket=bucket, Key=f"{thread_id}.json")
        return resp["Body"].read()
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return None
        raise


def presigned_get(thread_id: str, bucket: str | None = None, expires: int = 3600) -> str:
    """生成 Presigned GET URL，供外部临时读大产物。"""
    bucket = bucket or os.getenv("R2_BUCKET", "nexus-checkpoints")
    return r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": f"{thread_id}.json"},
        ExpiresIn=expires,
    )


# ── 便捷 ────────────────────────────────────────────────────────────
def dumps(state: dict) -> bytes:
    """统一序列化供 R2 存放。"""
    return json.dumps(state, ensure_ascii=False, default=str).encode()