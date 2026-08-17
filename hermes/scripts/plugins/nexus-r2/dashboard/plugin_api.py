"""nexus-r2 dashboard plugin 后端 API。

Hermes dashboard 自动 mount `/api/plugins/nexus-r2/`(web_server._mount_plugin_api_routes)。
返 R2 文件 CRUD:list / read / save(text) / delete / upload(multipart)。
逻辑沿用 nexus main.py(旧 B 阶段)的 R2 helper,迁此成 plugin tab 后端。
R2 client 经 libs/storage.r2_client(PYTHONPATH=/data/libs 同进程 import),
bucket = R2_BUCKET env(默认 nexus-checkpoints; 统一桶名)。
"""
from __future__ import annotations

import os
from typing import Any

try:
    from fastapi import APIRouter, UploadFile
except Exception:  # 无 dashboard 依赖时仍可被 plugin loader import 验证
    class APIRouter:  # type: ignore
        def get(self, *_a, **_k): return lambda fn: fn
        def post(self, *_a, **_k): return lambda fn: fn
        def delete(self, *_a, **_k): return lambda fn: fn

try:
    from storage import r2_client
except Exception:  # plugin loader import 期 storage 尚未就绪时兜底
    r2_client = None  # type: ignore

try:
    import botocore.exceptions
except Exception:
    botocore = None  # type: ignore

router = APIRouter()

_R2_BUCKET = os.getenv("R2_BUCKET", "nexus-checkpoints")


def _r2():
    if r2_client is None:
        raise RuntimeError("storage.r2_client 不可用(PYTHONPATH 未含 /data/libs?)")
    return r2_client()


@router.get("/files")
def list_files() -> dict[str, Any]:
    """列 R2 bucket 全目录(浅层,不分前缀)。返 {files:[key,...]}。"""
    try:
        resp = _r2().list_objects_v2(Bucket=_R2_BUCKET)
        return {"files": [o["Key"] for o in resp.get("Contents", [])]}
    except Exception as e:  # noqa: BLE001
        return {"error": f"列文件失败: {e}"}


@router.get("/files/{filename:path}")
def read_file(filename: str) -> dict[str, Any]:
    """读文本文件内容。二进制返字节数(JSON 不可 text 预览,返 size+binary 标志)。"""
    try:
        obj = _r2().get_object(Bucket=_R2_BUCKET, Key=filename)
        body = obj["Body"].read()
        try:
            return {"filename": filename, "content": body.decode(), "size": len(body), "binary": False}
        except UnicodeDecodeError:
            return {"filename": filename, "content": None, "size": len(body), "binary": True}
    except Exception as e:  # noqa: BLE001
        code = getattr(getattr(e, "response", {}), "get", lambda *_a: "")("Error", {}).get("Code", "") \
            if hasattr(e, "response") else ""
        if botocore and isinstance(e, botocore.exceptions.ClientError) and code in ("NoSuchKey", "404"):
            return {"error": f"文件不存在: {filename}"}
        return {"error": f"读取失败: {e}"}


@router.post("/files/{filename:path}")
def save_file(filename: str, content: str) -> dict[str, Any]:
    """保存/覆盖文本文件。"""
    if not filename:
        return {"error": "请填文件名"}
    try:
        _r2().put_object(Bucket=_R2_BUCKET, Key=filename, Body=(content or "").encode())
        return {"ok": True, "filename": filename}
    except Exception as e:  # noqa: BLE001
        return {"error": f"保存失败: {e}"}


@router.delete("/files/{filename:path}")
def delete_file(filename: str) -> dict[str, Any]:
    """删文件。"""
    if not filename:
        return {"error": "请填文件名"}
    try:
        _r2().delete_object(Bucket=_R2_BUCKET, Key=filename)
        return {"ok": True, "filename": filename}
    except Exception as e:  # noqa: BLE001
        return {"error": f"删除失败: {e}"}


@router.post("/upload")
async def upload_file(file: UploadFile) -> dict[str, Any]:
    """multipart 上传(表单字段名 file)。文件名取 UploadFile.filename。"""
    if file is None or not file.filename:
        return {"error": "未选择文件"}
    key = os.path.basename(file.filename or "upload.bin")
    try:
        body = await file.read()
        _r2().put_object(Bucket=_R2_BUCKET, Key=key, Body=body)
        return {"ok": True, "filename": key, "size": len(body)}
    except Exception as e:  # noqa: BLE001
        return {"error": f"上传失败: {e}"}
