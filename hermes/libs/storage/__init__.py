"""Nexus 共享存储包（R2 only，2026-08-22 收口精简版）。"""
from .storage import (
    dumps,
    load_checkpoint,
    presigned_get,
    r2_client,
    save_checkpoint,
)

__all__ = [
    "r2_client",
    "save_checkpoint",
    "load_checkpoint",
    "presigned_get",
    "dumps",
]