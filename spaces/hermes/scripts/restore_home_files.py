"""首启从 HF Storage Bucket 拉回 hermes home 关键文件(与 home_files_uploader.py 配对)。

治"重启丢 dashboard 设置 + .env channel credentials + hermes 个体 memories"。
A 方案把 HERMES_HOME 移 /opt/data 本地盘治 state.db malformed,代价=重启清盘丢:
  - .env(dashboard "Credentials" 页写的 channel token,hermes 写此非 HF Secrets)
  - SOUL.md(hermes 个体人设 prompt_builder.py:1326 装入 system prompt)
  - memories/MEMORY.md + memories/USER.md(hermes 个体记忆)
  - config.yaml(dashboard 设置项:provider/参数/plugins;★start.sh 改"缺才 cp"后
    template 不再覆盖,但旧盘清空仍丢,故仍在此脚本拉回)
本脚本 boot 期(hermes 起前)从 Bucket home-backups/ 拉回上述文件落 HERMES_HOME。

拉回策略(零臆断,容错过,与 restore_state.py 同模式):
  - 无凭证 / hf CLI 缺 → 跳过,hermes 自起默认空(不阻断 boot)
  - 本地已有该文件(非 FORCE)→ 保留不覆盖(理论上 /opt/data 重启已清,有则保守不强覆)
  - Bucket 无该文件 → skip 日志(uploader 未跑过/未写过)

环境变量:
  HF_TOKEN(写 bucket 权;hf CLI 自动读)
  HF_OWNER(默认空 → skip;HF namespace)
  NEXUS_LOGIC_BUCKET(默认空 → skip;bucket 名)
  FORCE_RESTORE(默认空;设非空强制覆盖本地已有文件)
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
_BACKUP_SUBDIR = "home-backups"
_HERMES_HOME = os.getenv("HERMES_HOME", "/opt/data/.hermes")
_FORCE = bool(os.getenv("FORCE_RESTORE", "").strip())

# hermes home 关键文件清单(相对 HERMES_HOME):
#  - .env:dashboard "Credentials" 页写的 channel token(非 HF Secrets 注入那批)
#  - SOUL.md:个体人设(prompt_builder.py:1326 装入 prompt;doctor.py 缺则建空)
#  - memories/MEMORY.md:个体记忆索引(profiles.py:63)
#  - memories/USER.md:用户档案(profiles.py:64)
#  - config.yaml:dashboard 设置项(provider/参数/plugins);★start.sh 改"缺才 cp"
#    后 template 不覆盖已有 config,但 /opt/data 重启清空仍丢,故仍在此拉回
# 注:state.db 不在此(restore_state.py 独立管,走 SQLite backup API)
_FILES = [
    ".env",
    "SOUL.md",
    "memories/MEMORY.md",
    "memories/USER.md",
    "config.yaml",
]


def _have_creds() -> bool:
    return bool(os.getenv("HF_TOKEN") and _OWNER and _BUCKET_NAME)


def _have_hf_cli() -> bool:
    return shutil.which("hf") is not None


def _ensure_dirs() -> None:
    """保 HERMES_HOME + memories/ 目录在(应当 start.sh 已 mkdir,兜底防 race)。"""
    os.makedirs(_HERMES_HOME, exist_ok=True)
    os.makedirs(os.path.join(_HERMES_HOME, "memories"), exist_ok=True)


def _restore_one(rel: str) -> str:
    """拉单文件从 Bucket → HERMES_HOME/<rel>。返状态摘要字符串。"""
    src = f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{rel}"
    dst = os.path.join(_HERMES_HOME, rel)
    if os.path.exists(dst) and not _FORCE:
        return f"skip: local {rel} exists (set FORCE_RESTORE=1 to overwrite)"
    os.makedirs(os.path.dirname(dst), exist_ok=True)
    try:
        result = subprocess.run(
            ["hf", "buckets", "cp", src, dst],
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ.copy(),
        )
        if result.returncode != 0:
            # Bucket 无该文件(uploader 未跑过/未写过)→ 首启预期,非错
            return f"skip: {rel} not in bucket (code={result.returncode})"
        if not os.path.exists(dst):
            return f"skip: {rel} bucket empty"
        size = os.path.getsize(dst)
        return f"ok: restored {rel} bytes={size}"
    except subprocess.TimeoutExpired:
        return f"skip: {rel} hf buckets cp timeout (120s)"
    except Exception as e:  # noqa: BLE001
        return f"skip: {rel} {e}"


def restore_once() -> str:
    """返多文件状态汇总字符串,供 start.sh 日志。无副作用崩。"""
    if not _have_hf_cli():
        return "skip: hf CLI not in PATH"
    if not _have_creds():
        return "skip: missing HF_TOKEN/HF_OWNER/NEXUS_LOGIC_BUCKET"
    _ensure_dirs()
    lines = [f"home-backups restore from hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}"]
    for rel in _FILES:
        lines.append(f"  {rel}: {_restore_one(rel)}")
    return "\n".join(lines)


def main() -> None:
    print(f"[restore-home-files] {restore_once()}", flush=True)


if __name__ == "__main__":
    main()
