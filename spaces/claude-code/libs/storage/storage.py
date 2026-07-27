"""统一存储工具：R2 大文件 + Supabase 结构化。

各 Space 通过 sys.path 引入本目录，或打包成包安装。
对外暴露：r2_client、supabase_client、以及封装函数。
所有凭证从环境变量读，不硬编码。
"""
from __future__ import annotations

import json
import os
from typing import Any

import boto3
from botocore.config import Config
from supabase import Client, create_client

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
    bucket = bucket or os.getenv("R2_CHECKPOINT_BUCKET", "nexus-checkpoints")
    key = f"{thread_id}.json"
    body = data.encode() if isinstance(data, str) else data
    r2_client().put_object(Bucket=bucket, Key=key, Body=body)
    return key


def load_checkpoint(thread_id: str, bucket: str | None = None) -> bytes | None:
    """读取 Checkpoint。不存在返回 None。"""
    import botocore.exceptions

    bucket = bucket or os.getenv("R2_CHECKPOINT_BUCKET", "nexus-checkpoints")
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
    bucket = bucket or os.getenv("R2_CHECKPOINT_BUCKET", "nexus-checkpoints")
    return r2_client().generate_presigned_url(
        "get_object",
        Params={"Bucket": bucket, "Key": f"{thread_id}.json"},
        ExpiresIn=expires,
    )


# ── Supabase ────────────────────────────────────────────────────────
_SUPA: Client | None = None


def supabase_client() -> Client:
    """惰性初始化 Supabase 客户端（用 service_role）。"""
    global _SUPA
    if _SUPA is None:
        url = os.getenv("SUPABASE_URL")
        key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY", "")
        if not url:
            raise RuntimeError("SUPABASE_URL 未设置")
        _SUPA = create_client(url, key)
    return _SUPA


def save_state(thread_id: str, state: dict[str, Any]) -> None:
    """upsert Agent 状态到 agent_states 表。"""
    supabase_client().table("agent_states").upsert(
        {"thread_id": thread_id, "state": state},
        on_conflict="thread_id",
    ).execute()


def load_state(thread_id: str) -> dict[str, Any] | None:
    """读 Agent 状态。无记录返回 None。"""
    res = (
        supabase_client()
        .table("agent_states")
        .select("state")
        .eq("thread_id", thread_id)
        .maybe_single()
        .execute()
    )
    return res.data["state"] if res.data else None


def log_task(thread_id: str, space_name: str, action: str, status: str) -> None:
    """写一条 task_logs 记录。"""
    supabase_client().table("task_logs").insert(
        {
            "thread_id": thread_id,
            "space_name": space_name,
            "action": action,
            "status": status,
        }
    ).execute()


def remember(key: str, value: dict[str, Any]) -> None:
    """Long-term memory upsert。"""
    supabase_client().table("long_memory").upsert(
        {"key": key, "value": value},
        on_conflict="key",
    ).execute()


def recall(key: str) -> dict[str, Any] | None:
    res = (
        supabase_client()
        .table("long_memory")
        .select("value")
        .eq("key", key)
        .maybe_single()
        .execute()
    )
    return res.data["value"] if res.data else None


# ── 便捷 ────────────────────────────────────────────────────────────
def dumps(state: dict[str, Any]) -> bytes:
    """统一序列化供 R2 存放。"""
    return json.dumps(state, ensure_ascii=False, default=str).encode()
