"""首启从 HF Storage Bucket 拉回 state.db(与 state_db_uploader.py 配对)。

2026-08-06 anysearch 查证后改 Bucket 路(推翻初版 Dataset repo 方案):
  - HF Storage Bucket GA 早于两参考项目,我们有 Bucket 已挂载,直接用。
  - 双盘分离:state.db 真值源在线写 /opt/data 本地盘(ephermal 重启清),
    Bucket 作离线快照仓库。本脚本 boot 期(hermes 起 state.db 写锁前)从
    bucket/state-backups/ 拉回最新快照到 HERMES_HOME,治"重启丢 dashboard 会话历史"。
  - huggingface_hub 1.0.1 无 bucket Python 上层 API(文档实证),改 hf buckets cp CLI 子进程。

拉回策略(零臆断,容错过):
  - 无凭证 / repo / hf CLI → 跳过,hermes 自起空库(不阻断 boot)
  - 本地已有 state.db(非 FORCE)→ 保留不覆盖(理论上 /opt/data 重启已清,有则保守不强覆)
  - repos 空 → ho buckets cp 报错 → skip 日志,hermes 起空库

环境变量:
  HF_TOKEN(写 bucket 权;hf CLI 自动读)
  HF_OWNER(默认空 → skip;HF namespace)
  NEXUS_LOGIC_BUCKET(默认空 → skip;bucket 名)
  FORCE_RESTORE(默认空;设非空强制覆盖本地 state.db)
  HERMES_HOME(默认 /opt/data/.hermes)
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

sys.path.insert(0, "/data/libs")  # Bucket 挂载点;本地调试兜底

_OWNER = os.getenv("HF_OWNER", "")
_BUCKET_NAME = os.getenv("NEXUS_LOGIC_BUCKET", "")
_BACKUP_SUBDIR = "state-backups"
_DB_NAME = "state.db"
_HERMES_HOME = os.getenv("HERMES_HOME", "/opt/data/.hermes")
_FORCE = bool(os.getenv("FORCE_RESTORE", "").strip())


def _src() -> str:
    """hf://buckets/<owner>/<bucket>/state-backups/state.db"""
    return f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{_DB_NAME}"


def _db_path() -> str:
    return os.path.join(_HERMES_HOME, _DB_NAME)


def _have_creds() -> bool:
    return bool(os.getenv("HF_TOKEN") and _OWNER and _BUCKET_NAME)


def _have_hf_cli() -> bool:
    return shutil.which("hf") is not None


def _ensure_home() -> None:
    """保 HERMES_HOME 目录在(应当 start.sh 已 mkdir,兜底防 race)。"""
    os.makedirs(_HERMES_HOME, exist_ok=True)


def restore_once() -> str:
    """返状态摘要字符串,供 start.sh 日志。无副作用崩,失败返 skip 原因。"""
    if not _have_hf_cli():
        return "skip: hf CLI not in PATH"
    if not _have_creds():
        return "skip: missing HF_TOKEN/HF_OWNER/NEXUS_LOGIC_BUCKET"
    dst = _db_path()
    if os.path.exists(dst) and not _FORCE:
        # 本地已有 state.db(理论上 /opt/data 重启清不该有;有则保留不覆盖)
        return f"skip: local state.db exists at {dst} (set FORCE_RESTORE=1 to overwrite)"
    _ensure_home()
    # hf buckets cp <hf://...> <local>:bucket 中无 state.db 时返非零,跳过 hermes 起空库
    try:
        result = subprocess.run(
            ["hf", "buckets", "cp", _src(), dst],
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ.copy(),
        )
        if result.returncode != 0:
            # repo 空 / 网络等:首启跳过,hermes 起空库(不阻断 boot)
            return (
                f"skip: hf buckets cp failed code={result.returncode} "
                f"stderr={result.stderr.strip()[:200]}"
            )
        if not os.path.exists(dst):
            return "skip: bucket has no state.db (uploader 未跑过)"
        size = os.path.getsize(dst)
        return f"ok: restored state.db from {_src()} bytes={size}"
    except subprocess.TimeoutExpired:
        return "skip: hf buckets cp timeout (120s)"
    except Exception as e:  # noqa: BLE001
        return f"skip: {e}"


def main() -> None:
    print(f"[restore-state] {restore_once()}", flush=True)


if __name__ == "__main__":
    main()
