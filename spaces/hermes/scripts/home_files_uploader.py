"""hermes home 关键文件周期上传 HF Storage Bucket(与 restore_home_files.py 配对)。

治"重启丢 dashboard 设置 + .env channel credentials + hermes 个体 memories"。
A 方案把 HERMES_HOME 移 /opt/data 本地盘治 state.db malformed,代价=重启清盘丢个体
人设/记忆/dashboard 写的 .env/config.yaml。本脚本周期推 Bucket home-backups/ 离线
快照,restore_home_files.py boot 拉回。

推送文件清单(相对 HERMES_HOME,与 restore_home_files.py _FILES 同源):
  - .env:dashboard "Credentials" 页 hermes 写的 channel token
  - SOUL.md:个体人设
  - memories/MEMORY.md + memories/USER.md:个体记忆
  - config.yaml:dashboard 设置项
state.db 不在此(state_db_uploader.py 独立管,走 SQLite backup API 取一致快照)。

增量推送(省 HF rate limit):逐文件比本地 mtime+size vs 上次推送记录,未改跳。
首次跑无记录则全推。修改窗口内 hermes 正写 .env/config.yaml 时,先读取拷 staging
(读时一致快照,文件若被 hermes 重写,closed fd 仍是旧快照),不放 /tmp tmpfs(同
state_db_uploader 治 issue #35376 雷):staging 落 HERMES_HOME 父盘(/opt/data,大非 tmpfs)。

环境变量:
  HF_TOKEN(写 bucket 权;hf CLI 自动读)
  HF_OWNER(默认空 → 降级 no-op + WARN;HF namespace)
  NEXUS_LOGIC_BUCKET(默认空 → 降级;bucket 名)
  HOME_FILES_UPLOAD_INTERVAL(默认 600 秒;文件改不频繁,比 state.db 300s 低频)
  HERMES_HOME(默认 /opt/data/.hermes)
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/data/libs")  # Bucket 挂载点;本地调试兜底

_HERMES_HOME = os.getenv("HERMES_HOME", "/opt/data/.hermes")
_INTERVAL = int(os.getenv("HOME_FILES_UPLOAD_INTERVAL", "600"))
_OWNER = os.getenv("HF_OWNER", "")
_BUCKET_NAME = os.getenv("NEXUS_LOGIC_BUCKET", "")
_BACKUP_SUBDIR = "home-backups"
# staging 落 HERMES_HOME 父盘(非 /tmp tmpfs,同 state_db_uploader 治 issue #35376)
_STAGING_DIR = os.path.dirname(_HERMES_HOME)

# 上次推送记录文件(本地 mtime+size per file),判断增量跳。无需持久化跨重启:
# 首次跑(无记录)全推,无浪费(小文件 HF rate limit 充裕)。
_STATE_FILE = os.path.join(_STAGING_DIR, ".home-upload-state.json")

_FILES = [
    "config.yaml",
    ".env",
    "SOUL.md",
    "memories/MEMORY.md",
    "memories/USER.md",
]


def _dest(rel: str) -> str:
    """hf://buckets/<owner>/<bucket>/home-backups/<rel>"""
    return f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{rel}"


def _have_creds() -> bool:
    return bool(os.getenv("HF_TOKEN") and _OWNER and _BUCKET_NAME)


def _have_hf_cli() -> bool:
    return shutil.which("hf") is not None


def _path(rel: str) -> str:
    return os.path.join(_HERMES_HOME, rel)


def _local_sig(rel: str) -> tuple[int, int] | None:
    """返 (mtime, size) 或 None(文件缺)。"""
    p = _path(rel)
    try:
        st = os.stat(p)
        return (int(st.st_mtime), int(st.st_size))
    except OSError:
        return None


def _load_state() -> dict:
    try:
        with open(_STATE_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def _save_state(state: dict) -> None:
    try:
        with open(_STATE_FILE, "w", encoding="utf-8") as f:
            json.dump(state, f)
    except OSError as e:  # noqa: BLE001
        print(f"[home-upload] WARN: save state failed: {e}", flush=True)


def _upload_file(rel: str) -> bool:
    """单文件 → Bucket(经 staging 拷一致快照,放 HERMES_HOME 父盘非 /tmp)。返成功?"""
    src = _path(rel)
    os.makedirs(_STAGING_DIR, exist_ok=True)
    try:
        os.makedirs(os.path.dirname(os.path.join(_STAGING_DIR, "staged-" + rel)), exist_ok=True)
    except OSError:
        pass
    tmp = tempfile.NamedTemporaryFile(
        suffix="-" + os.path.basename(rel),
        prefix="home-bak-",
        delete=False,
        dir=_STAGING_DIR,
    ).name
    try:
        # 拷源到 staging(读时快照;hermes 正写也读旧 inode 不撕)。shutil.copy2 保留元。
        shutil.copy2(src, tmp)
        result = subprocess.run(
            ["hf", "buckets", "cp", tmp, _dest(rel)],
            capture_output=True,
            text=True,
            timeout=120,
            env=os.environ.copy(),
        )
        if result.returncode != 0:
            print(
                f"[home-upload] {rel} hf buckets cp failed code={result.returncode} "
                f"stderr={result.stderr.strip()[:200]}",
                flush=True,
            )
            return False
        size = os.path.getsize(tmp)
        print(f"[home-upload] {rel} ok bytes={size} dest={_dest(rel)}", flush=True)
        return True
    except subprocess.TimeoutExpired:
        print(f"[home-upload] {rel} hf buckets cp timeout (120s)", flush=True)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[home-upload] {rel} failed: {e}", flush=True)
        return False
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def sync_once() -> None:
    if not (_have_hf_cli() and _have_creds()):
        return
    state = _load_state()
    next_state = dict(state)
    for rel in _FILES:
        src = _path(rel)
        if not os.path.exists(src):
            # hermes 未写过该文件(如用户没用 dashboard 写 .env/SOUL.md)→ 跳,不推空
            continue
        sig = _local_sig(rel)
        if sig is None:
            continue
        prev = state.get(rel)
        # 增量跳:mtime+size 未变则跳(省 HF rate limit)
        if prev == list(sig):
            continue
        if _upload_file(rel):
            next_state[rel] = list(sig)
        # 失败保留旧 state 下轮重试(不更 next_state)
    if next_state != state:
        _save_state(next_state)


def main() -> None:
    print(
        f"[home-upload] start, interval={_INTERVAL}s, "
        f"home={_HERMES_HOME}, bucket=hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}",
        flush=True,
    )
    if not (_have_hf_cli() and _have_creds()):
        print(
            "[home-upload] WARN: missing hf CLI / HF_TOKEN / HF_OWNER / "
            "NEXUS_LOGIC_BUCKET — uploader daemon no-op "
            "(dashboard 设置/.env/memories 重启后丢,需在 HF Secrets 补齐)",
            flush=True,
        )
    while True:
        try:
            sync_once()
        except Exception as e:  # noqa: BLE001
            print(f"[home-upload] fatal {e}", flush=True)
        time.sleep(_INTERVAL)


if __name__ == "__main__":
    main()
