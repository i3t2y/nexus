#!/usr/bin/env python3
"""
run.py — 总控脚本，协调所有 runtime patch 的执行顺序。

由 entrypoint.sh 调用 (python3 /app/worker/run.py)。
此文件在 HF Dataset nmem/nworker 里，可随意改，不触发 Space rebuild。

执行顺序:
  pre-alembic  : 不依赖 DB 表的 patches (DEFAULT_CONFIG, pgvector.py)
  alembic      : 建表 (auth/api_key/settings)
  post-alembic : 依赖 DB 表的 patches (清 config_overrides, inject /health + worker)

以后新增 patch:
  1. 在 patches/ 目录创建 NN_name.py
  2. 在下方 PRE_ALEMBIC 或 POST_ALEMBIC 列表里加上文件名
"""
import subprocess
import sys
import os
from pathlib import Path

PATCHES_DIR = Path(__file__).parent / "patches"

# ─── 执行顺序定义 ───
# pre-alembic: 不依赖 DB 表
PRE_ALEMBIC = [
    "10_default_config.py",
    "20_pgvector_ext.py",
]

# post-alembic: 需要 alembic 建好的表
POST_ALEMBIC = [
    "30_clear_db_overrides.py",
    "40_health_worker.py",
]

def run_patch(name: str):
    """Execute a single patch file, capturing output."""
    path = PATCHES_DIR / name
    if not path.exists():
        print(f"  → {name}: NOT FOUND, skipping")
        return
    print(f"  → {name}:")
    result = subprocess.run(
        [sys.executable, str(path)],
        capture_output=True, text=True, timeout=60
    )
    # Print patch stdout (indented)
    for line in result.stdout.strip().split("\n"):
        if line:
            print(f"    {line}")
    if result.returncode != 0:
        print(f"    WARNING: exit code {result.returncode}")
        for line in result.stderr.strip().split("\n")[-5:]:
            print(f"    stderr: {line}")

def run_alembic():
    """Run alembic migration in /app."""
    print("  → alembic upgrade head:")
    result = subprocess.run(
        ["alembic", "upgrade", "head"],
        cwd="/app",
        capture_output=True, text=True, timeout=120
    )
    for line in result.stdout.strip().split("\n"):
        if line:
            print(f"    {line}")
    if result.returncode != 0:
        print(f"    WARNING: alembic failed (exit {result.returncode}), tables may exist")
        for line in result.stderr.strip().split("\n")[-3:]:
            print(f"    stderr: {line}")

if __name__ == "__main__":
    print("=== run_all.py: applying patches ===")

    print("[pre-alembic]")
    for name in PRE_ALEMBIC:
        run_patch(name)

    print("[alembic]")
    run_alembic()

    print("[post-alembic]")
    for name in POST_ALEMBIC:
        run_patch(name)

    print("=== run_all.py: done ===")
