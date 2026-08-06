"""state.db 周期上传 HF Storage Bucket(离线快照仓库,治"重启丢 dashboard 会话历史")。

2026-08-06 anysearch 查证后改 Bucket 路(推翻初版 Dataset repo 方案):
  - HF Storage Bucket 2026-03-10 发布 / 03-31 Spaces Volume 挂载 GA,早于两参考项目
    (HermesFace 2026-04-13 / HuggingMes 2026-05-03 创建),故两项目用 Dataset 非
    历史限制是惰性选熟悉 git endpoint。我们已有 Bucket sonoke/<logic> 挂载 /data,
    直接用零新依赖零新凭证零新 repo。
  - 官方文档明文 Bucket 专列 Rolling backups + Agentic storage 用例 = 正是 state.db 场景。
  - 比 Dataset repo 优:Bucket 无 git history 累积(每次覆写只留最新,Dataset 300s 周期
    天 288 commit 无界膨胀需 squash);Bucket --delete 覆写干净。

双盘分离保无 FUSE 并发雷(治本方案 A 核心):
  - state.db 真值源在线写在 /opt/data/.hermes 本地盘(ext4/overlay 无 FUSE 无旁路进程,
    WAL 正常稳定)
  - Bucket 纯当离线快照仓库:本脚本周期 cp 推(覆写),restore_state.py boot 期 cp 拉一次
  - 两盘分开无并发读写,旧 malformed 雷根因(bucket FUSE+litestream 旁路并发改 WAL)消除

一致快照:上传前 `PRAGMA wal_checkpoint(TRUNCATE)` 把 WAL 落主库,再 sqlite3 backup API
读一致快照拷 tmp 再推(backup API 读一致快照,不畏 hermes 正写)。

推送:调 `hf buckets cp` CLI 子进程(与 start.sh bootstrap_from_bucket 同模式)。
  huggingface_hub 1.0.1 无 bucket Python 上层 API(文档实证),改 CLI 最稳。
  hf CLI 自动读 HF_TOKEN env。dest = hf://buckets/<HF_OWNER>/<NEXUS_LOGIC_BUCKET>/state-backups/state.db

环境变量:
  HF_TOKEN(写 bucket 权;hf CLI 自动读)
  HF_OWNER(默认空 → 降级 no-op + WARN;HF namespace)
  NEXUS_LOGIC_BUCKET(默认空 → 降级;bucket 名)
  STATE_UPLOAD_INTERVAL(默认 300 秒;平衡 HF rate limit + 丢窗)
  HERMES_HOME(默认 /opt/data/.hermes;state.db 所在)
"""
from __future__ import annotations

import os
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time

sys.path.insert(0, "/data/libs")  # Bucket 挂载点;本地调试兜底

# huggingface_hub 不再用(1.0.1 无 bucket Python API;改 hf CLI 子进程)。
# 仅需 hf CLI 在 PATH(base 镜像装,requirements-base 含 huggingface_hub[cli])。

_HERMES_HOME = os.getenv("HERMES_HOME", "/opt/data/.hermes")
_INTERVAL = int(os.getenv("STATE_UPLOAD_INTERVAL", "300"))
# Bucket id 由 HF_OWNER + NEXUS_LOGIC_BUCKET 组合(与 sync-logic-bucket.sh 同源),
# 不硬编码真值(脱敏 + 部署侧可改)。
_OWNER = os.getenv("HF_OWNER", "")
_BUCKET_NAME = os.getenv("NEXUS_LOGIC_BUCKET", "")
# state.db 子目录(避与逻辑层 app/scripts/libs 混在 bucket 根)。
_BACKUP_SUBDIR = "state-backups"
_DB_NAME = "state.db"
# staging 目录:与 HERMES_HOME 同盘(/opt/data 等),非默认 /tmp tmpfs。
# 根因 hermes 原生 bug issue #35376:backup 在 /tmp(tmpfs 小)staging,
# state.db 涨过 /tmp 余量 → SQLite safe-copy 失败 → backup 静默截半 → restore 回残缺库。
# 放 HERMES_HOME 父盘(ext4/overlay 大,非 tmpfs)消此雷。
_STAGING_DIR = os.path.dirname(_HERMES_HOME)


def _dest() -> str:
    """hf://buckets/<owner>/<bucket>/state-backups/state.db"""
    return f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{_DB_NAME}"


def _have_creds() -> bool:
    """三 env 齐(HF_TOKEN + owner + bucket)才推。缺一则自降级 no-op。"""
    return bool(os.getenv("HF_TOKEN") and _OWNER and _BUCKET_NAME)


def _have_hf_cli() -> bool:
    """hf CLI 在 PATH?base 镜像装 huggingface_hub[cli] 提供 hf。"""
    return shutil.which("hf") is not None


def _db_path() -> str:
    return os.path.join(_HERMES_HOME, _DB_NAME)


def _consistent_backup() -> str | None:
    """一致快照:wal_checkpoint(TRUNCATE) 落 WAL 后 sqlite3 backup API 拷 tmp。

    backup API 读一致快照,不畏 hermes 正写(WAL 已 truncate 后主库是当前态)。
    返 tmp 路径(上传后 caller 删);失败返 None。
    """
    src = _db_path()
    if not os.path.exists(src):
        print(f"[state-upload] skip: state.db not found at {src}", flush=True)
        return None
    # staging 落 HERMES_HOME 父盘(非 /tmp tmpfs,治 issue #35376 雷):
    # dirs_exist_ok 兜底建盘;NamedTemporaryFile dir= 指该盘,staging 与源同盘拷贝快。
    try:
        os.makedirs(_STAGING_DIR, exist_ok=True)
    except OSError:
        print(f"[state-upload] WARN: mkdir staging {_STAGING_DIR} failed", flush=True)
    tmp = tempfile.NamedTemporaryFile(
        suffix=".db", prefix="state-bak-", delete=False, dir=_STAGING_DIR
    ).name
    try:
        # 先 wal_checkpoint TRUNCATE:把 WAL 落主库并清 WAL,backup 拿主库当前一致态
        # 用独立短连接做 checkpoint(需写连接,避开 hermes 主连接写锁竞争)
        try:
            chk = sqlite3.connect(src, timeout=5)
            chk.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            chk.close()
        except Exception as e:  # noqa: BLE001
            # checkpoint 失败(库正忙)不阻断:backup API 仍读主库一致快照(WAL 自动并入)
            print(f"[state-upload] wal_checkpoint warn: {e}", flush=True)
        src_conn = sqlite3.connect(f"file:{src}?mode=ro", uri=True, timeout=5)
        dst_conn = sqlite3.connect(tmp)
        src_conn.backup(dst_conn)
        dst_conn.close()
        src_conn.close()
        return tmp
    except Exception as e:  # noqa: BLE001
        print(f"[state-upload] backup failed: {e}", flush=True)
        try:
            os.unlink(tmp)
        except OSError:
            pass
        return None


def _upload(tmp: str) -> bool:
    """上传 tmp→Bucket state.db 单文件(hf buckets cp 覆写)。

    hf CLI 自动读 HF_TOKEN env。cp 单文件覆写(Bucket 非版本化,overwrite-in-place)。
    失败不崩,返 False 主循环下轮重试。
    """
    if not _have_hf_cli():
        print("[state-upload] skip: hf CLI not in PATH", flush=True)
        return False
    if not _have_creds():
        print(
            "[state-upload] skip: missing HF_TOKEN/HF_OWNER/NEXUS_LOGIC_BUCKET",
            flush=True,
        )
        return False
    try:
        # hf buckets cp <local> <hf://buckets/...>;覆写无 --delete(Bucket 单文件
        # overwrite-in-place,不会误删 bucket 其他文件 — 只操作该 path)
        result = subprocess.run(
            ["hf", "buckets", "cp", tmp, _dest()],
            capture_output=True,
            text=True,
            timeout=120,  # state.db 小(MB 级),120s 充裕防 hf CLI 挂死
            env=os.environ.copy(),
        )
        if result.returncode != 0:
            print(
                f"[state-upload] hf buckets cp failed code={result.returncode} "
                f"stderr={result.stderr.strip()[:300]}",
                flush=True,
            )
            return False
        size = os.path.getsize(tmp)
        print(f"[state-upload] ok bytes={size} dest={_dest()}", flush=True)
        return True
    except subprocess.TimeoutExpired:
        print("[state-upload] hf buckets cp timeout (120s)", flush=True)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[state-upload] upload failed: {e}", flush=True)
        return False


def sync_once() -> None:
    tmp = _consistent_backup()
    if tmp is None:
        return
    try:
        _upload(tmp)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


def main() -> None:
    print(
        f"[state-upload] start, interval={_INTERVAL}s, "
        f"db={_db_path()}, bucket=hf://buckets/{_OWNER}/{_BUCKET_NAME}",
        flush=True,
    )
    if not (_have_hf_cli() and _have_creds()):
        print(
            "[state-upload] WARN: missing hf CLI / HF_TOKEN / HF_OWNER / "
            "NEXUS_LOGIC_BUCKET — uploader daemon no-op "
            "(会话历史重启后丢,需在 HF Secrets 补齐)",
            flush=True,
        )
    while True:
        try:
            sync_once()
        except Exception as e:  # noqa: BLE001
            print(f"[state-upload] fatal {e}", flush=True)
        time.sleep(_INTERVAL)


if __name__ == "__main__":
    main()
