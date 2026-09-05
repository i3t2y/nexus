"""统一错误结构 + request_id 透传 + 结构化日志。

设计：
- 每个请求有唯一 `request_id`（入站 header `X-Request-ID` 缺则生成 uuid）。
- 鉴权失败 / 业务错误统一返回 `{error: {code, message, retryable, request_id}}`。
- 关键事件写结构化 JSON 日志（含 request_id/space/action/status），便于跨组件串联排障。
  日志仅打 stdout（HF Space 抓 stdout），不写库（task_logs 表已记录任务级状态）。
"""
from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from fastapi import HTTPException

# 可重试的错误码（下游暂时不可达/超时），客户端可重试
_RETRYABLE_CODES = {"downstream_timeout", "downstream_unreachable", "db_unavailable", "rate_limited"}


def new_request_id(inbound: str | None) -> str:
    """入站 X-Request-ID 缺则生成 uuid4。"""
    rid = (inbound or "").strip()
    return rid or uuid.uuid4().hex


def error_response(code: str, message: str, status: int, request_id: str, retryable: bool | None = None) -> dict[str, Any]:
    """构造统一错误响应体。retryable 缺省按 code 推断。"""
    if retryable is None:
        retryable = code in _RETRYABLE_CODES
    return {
        "error": {
            "code": code,
            "message": message,
            "retryable": retryable,
            "request_id": request_id,
        }
    }


def raise_nexus_error(code: str, message: str, status: int, request_id: str, retryable: bool | None = None) -> "Any":
    """抛 HTTPException，body 用统一结构。FastAPI HTTPException(detail=...) 会原样返 detail。"""
    raise HTTPException(status_code=status, detail=error_response(code, message, status, request_id, retryable))


def log_event(request_id: str, space: str, action: str, status: str, **extra: Any) -> None:
    """结构化 JSON 日志（stdout，flush=True 供 HF Space 抓）。

    字段：ts(UTC ISO8601)、request_id、space、action、status、extra...
    单调日志时间戳由本函数自行生成（Python datetime 可用，无禁用约束）。
    """
    record = {
        "ts": datetime.now(timezone.utc).isoformat(),
        "request_id": request_id,
        "space": space,
        "action": action,
        "status": status,
    }
    if extra:
        record["extra"] = extra
    print(json.dumps(record, ensure_ascii=False, default=str), flush=True)
