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
  - .no-bundled-skills:profile 级 marker(用户 opt-out bundled skill seeding),
    缺则 skip(未 opt-out,正常默认)→ opt-out 选择跨重启持久
state.db 不在此(state_db_uploader.py 独立管,走 SQLite backup API 取一致快照)。

★2026-08-08 plugins/ 目录持久(config.yaml 的 enabled 标记保但插件代码本体不保,
  ephemeral /opt/data 重启清盘丢 → dashboard 装的插件消失 + enabled 标记无对应代码
  静默失效)。plugins/ 走 `hf buckets sync` 整目录多文件 sync(非单文件 cp),exclude
  `.git/`(无用的 git 历史,省体积)+ `__pycache__`/`*.pyc`(重构时重建)。plugins/
  本体是 git clone 进来的纯代码(install 不 pip 依赖,hermes 启动 import 时按需)。

★2026-08-08 skills/ 目录持久(hermes skills install 装的第三方技能本体 +
  .hub/lock.json 追踪)。与 plugin 关键差异:skills/ 同目录混 bundled(镜像内
  /opt/hermes-agent/skills/ 每启动 bundle sync 注入)+ user-installed(dashboard 装),
  无固定目录名能排 bundled(nexus-r2/ops 那种两固定名排除不适用)。故走路线 B 精准:
  仅基于 .hub/lock.json list_installed() 的 install_path 数 user skills,staging 各
  user skill 子树 + .hub 徽志(lock.json/audit.log/taps.json),hf buckets sync 推。
  bundled + .bundled_manifest + .hub/quarantine + .hub/index-cache 不碰(bundle
  sync 每启动重建)。

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
    # ★2026-08-08 .no-bundled-skills(profile 级 marker,HERMES_HOME 根 非 skills/ 下):
    #   用户 `hermes setup --no-skills` 或 dashboard opt-out 写此 marker → 启动 skip
    #   bundle skill seeding(skills_sync.py:688 HERMES_HOME/.no-bundled-skills)。
    #   保此 marker 让 opt-out 选择跨重启持久。缺则 skip(用户未 opt-out,正常默认)。
    ".no-bundled-skills",
]

# ★2026-08-08 plugins/ 整目录持久(dashboard 装的插件代码本体)。
# 走 `hf buckets sync` 多文件 sync,排除 .git/(git 历史无用,省体积)+
# __pycache__/*.pyc(变时重建)。hermes 装插件 = git clone --depth 1 入
# HERMES_HOME/plugins/<name>/,payload 纯代码(install 不 pip 依赖)。
# 本地缺 plugins/ 目录(无装任何插件)→ skip 不推空(prefetch 出错同)。
# ★exclude 内置两 plugin(nexus-r2/nexus-ops):start.sh L103 每 boot 从 Bucket
#   逻辑层 $APP_DIR/scripts/plugins/ cp 注入 HERMES_HOME/plugins/,无需 Bucket
#   home-backups 轮转。若推 Bucket 会在 restore 期可能与 start.sh cp 竞态(覆盖
#   顺序定,但不必要 — 内置本就每次 boot 强注)。只保 user dashboard 装的 plugin。
_PLUGINS_DIR_REL = "plugins"
# sync 的 exclude pattern(hf buckets sync --exclude 可多次):
#  - .git / .git/ → 不保 git 历史(shutil.move 含 .git 但 restore 重装不需)
#  - __pycache__/ + *.pyc → bytecode 缓存,变时重建
#  - *.pyo → opt level 缓存(老 Py3 大多无)
#  - nexus-r2 / nexus-ops → 内置两 plugin,start.sh 每 boot cp 注入,不入 Bucket
_PLUGINS_EXCLUDE = [".git", "__pycache__/", "*.pyc", "*.pyo", "nexus-r2", "nexus-ops"]

# ★2026-08-08 skills/ 持久(dashboard 装的第三方技能本体 + .hub/lock.json 追踪)。
# hermes skills install 落 HERMES_HOME/skills/<category>/<name>/,并写
# skills/.hub/lock.json(Skills Hub 追踪 source/install_path/content_hash)。
# ephemeral /opt/data 重启清盘 → 技能本体 + lockfile 全丢 → hermes 不认已装。
#
# 与 plugin 关键差异:skills/ 同目录混 bundled(镜像内 /opt/hermes-agent/skills/ 每
# 启动 sync_skills 注入)+ user-installed(dashboard 装),无固定目录名能排 bundled
# (nexus-r2/ops 那种两固定名排除不适用)。故**不能整目录 sync**(会把 bundled 飘移
# 推 Bucket,restore 期 stale 覆盖镜像新 bundled)。
#
# 路线 B(精准):仅推 lockfile 列的 user skills + .hub 徽志(lock.json/audit.log/
# taps.json),bundled 不碰。基于 .hub/lock.json list_installed() 的 install_path
# 构造 staging,排除 bundled-manifest/quarantine/index-cache(bundle sync 每启动重建)。
# .no-bundled-skills marker 在 HERMES_HOME 根(非 skills/ 下,走 _FILES 单文件路径)。
_SKILLS_DIR_REL = "skills"
_HUB_DIR_REL = "skills/.hub"
# .hub 下需保(lockfile 追踪 + 徽志,丢则 hermes 不认已装 / 丢审计 / 丢 tap 配):
_HUB_KEEP = ("lock.json", "audit.log", "taps.json")
# .hub 下不推(临时/可重建,bundle sync 每启动重建或装完移走):
_HUB_EXCLUDE_DIRS = ("quarantine", "index-cache")
# 路线 B 推送的 staging 内 skills 子树(构造 user-skills + .hub 徽志,无 bundled):
# 仅含 user skills 子目录 + .hub/{lock.json,audit.log,taps.json} 三文件。
# skills/ 根的 .bundled_manifest(SKILLS_DIR/.bundled_manifest)不推,因 bundle sync
# 每启动 _write_manifest 重建(L934/1077/1381),推旧 manifest 干扰 sync diff 逻辑。


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


def _plugins_local_sig() -> tuple[int, int] | None:
    """plugins/ 目录签名(总 mtime 最深改 + 总 size)或 None(目录缺/空)。

    巡 plugins/<name>/ 各文件累 size + max mtime 作目录聚合签名。增时较粗但够判
    "有任何变化"(uptime 内装/卸/改插件 → 签变 → 触 sync)。
    ★与 _PLUGINS_EXCLUDE 一致:跳 .git/__pycache__/nexus-r2/nexus-ops(nexus 两
    内置 start.sh 每 boot cp 注入,随 cp mtime 不定,纳入计数会致假签名变混 user plugins
    变化),只数 user dashboard 装的 plugins。
    """
    root = _path(_PLUGINS_DIR_REL)
    if not os.path.isdir(root):
        return None
    total_size = 0
    max_mtime = 0
    found = False
    try:
        for dirpath, dirnames, filenames in os.walk(root):
            # 跳 .git/__pycache__(与 exclude 一致)
            dirnames[:] = [
                d for d in dirnames
                if d not in {".git", "__pycache__"}
            ]
            # 跳 nexus-r2/nexus-ops 内置(与 _PLUGINS_EXCLUDE 一致;start.sh 每 boot
            # cp 注入,mtime 随 cp 不定,纳入会假签变混 user plugin 变化)
            dirnames[:] = [
                d for d in dirnames
                if d not in {"nexus-r2", "nexus-ops"}
            ]
            # 若当前 dirpath 自身就在内置目录下(不该达,前一行已剪 dirnames),
            # 兜底:跳过其文件
            parts = dirpath.split(os.sep)
            if "nexus-r2" in parts or "nexus-ops" in parts:
                continue
            for fn in filenames:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                    total_size += int(st.st_size)
                    if int(st.st_mtime) > max_mtime:
                        max_mtime = int(st.st_mtime)
                    found = True
                except OSError:
                    pass
    except OSError:
        return None
    if not found:
        return None
    return (max_mtime, total_size)


def _upload_plugins() -> bool:
    """plugins/ 整目录 → Bucket home-backups/plugins/(走 hf buckets sync)。

    本地缺 plugins/(无装任何插件)→ 跳不推空。sync 用 --no-delete(默认,不删
    Bucket 不在 source 的文件,避误删)。
    """
    src = _path(_PLUGINS_DIR_REL)
    if not os.path.isdir(src):
        # 无装任何插件 → skip(不报错,首启前正常)
        return True
    if not os.listdir(src):
        return True
    dest = f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{_PLUGINS_DIR_REL}"
    cmd = ["hf", "buckets", "sync", src, dest]
    for pat in _PLUGINS_EXCLUDE:
        cmd.extend(["--exclude", pat])
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=300,  # 目录 sync 容多文件,超 300s
            env=os.environ.copy(),
        )
        if result.returncode != 0:
            print(
                f"[home-upload] plugins/ hf buckets sync failed code="
                f"{result.returncode} stderr={result.stderr.strip()[:300]}",
                flush=True,
            )
            return False
        # stdout 含 sync plan(files created/updated),quiet 不打每文件,只汇总:
        out = (result.stdout or "").strip()
        # 抽 created/updated 行计(quiet 或 agent format JSONL)
        n_created = out.count('"action": "create"') + out.count("created")
        n_updated = out.count('"action": "update"') + out.count("updated")
        sig = _plugins_local_sig()
        size = sig[1] if sig else 0
        print(
            f"[home-upload] plugins/ ok bytes={size} "
            f"(created~{n_created} updated~{n_updated}) dest={dest}",
            flush=True,
        )
        return True
    except subprocess.TimeoutExpired:
        print("[home-upload] plugins/ hf buckets sync timeout (300s)", flush=True)
        return False
    except Exception as e:  # noqa: BLE001
        print(f"[home-upload] plugins/ failed: {e}", flush=True)
        return False


def _lock_list_installed() -> list[dict]:
    """从 .hub/lock.json 枚举 user-installed skills(返 [{name, install_path, ...}])。

    hermes skills install 落库时 record_install 写 lock.json installed[<name>] = {...}
    (skills_hub.py HubLockFile.record_install L3519)。bundled skills 不进 lock(由
    bundle sync 的 .bundled_manifest 单独管)。故 lock.json 是 user skills 的唯一真源。
    lock 缺/坏 → 返 [](无装 user skill,首启预期,非错)。
    """
    lock_p = _path(os.path.join(_HUB_DIR_REL, "lock.json"))
    try:
        data = json.loads(lock_p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    installed = data.get("installed") if isinstance(data, dict) else None
    if not isinstance(installed, dict):
        return []
    result = []
    for name, entry in installed.items():
        if isinstance(entry, dict):
            result.append({"name": name, **entry})
    return result


def _skills_local_sig() -> tuple[int, int] | None:
    """user-installed skills 目录签名(总 mtime + 总 size)或 None(无 user skill)。

    路线 B 核心:仅基于 .hub/lock.json list_installed() 的 install_path 数 user skills,
    bundled skills 不数(无固定目录名能排,且 bundle sync 每启动 cp 其 mtime 随 cp 飘)。
    install_path 是相对 SKILLS_DIR 的路径(如 'github/my-skill' 或 'flat-skill')。
    数每个 user skill 子树全文件(*.pyc/__pycache__/.git 排,同 plugin)累 size + max mtime。
    """
    installed = _lock_list_installed()
    if not installed:
        return None
    root = _path(_SKILLS_DIR_REL)
    if not os.path.isdir(root):
        return None
    total_size = 0
    max_mtime = 0
    found = False
    for entry in installed:
        install_path = entry.get("install_path") or ""
        if not install_path:
            continue
        skill_dir = os.path.join(root, install_path)
        if not os.path.isdir(skill_dir):
            continue
        for dirpath, dirnames, filenames in os.walk(skill_dir):
            dirnames[:] = [d for d in dirnames if d not in {".git", "__pycache__"}]
            for fn in filenames:
                if fn.endswith((".pyc", ".pyo")):
                    continue
                fp = os.path.join(dirpath, fn)
                try:
                    st = os.stat(fp)
                    total_size += int(st.st_size)
                    if int(st.st_mtime) > max_mtime:
                        max_mtime = int(st.st_mtime)
                    found = True
                except OSError:
                    pass
    if not found:
        return None
    return (max_mtime, total_size)


def _skills_dest_dir() -> str:
    """Bucket home-backups/skills/ URI(整 user-skills 子树 dest)。"""
    return f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{_SKILLS_DIR_REL}"


def _upload_skills() -> bool:
    """user-installed skills 本体 + .hub 徽志 → Bucket home-backups/skills/。

    路线 B:构造 staging 仅含 user skills(从 lock.json install_path 列)+ .hub 徽志
    (lock.json/audit.log/taps.json),不含 bundled(canvas 区分不了故必须基于 lockfile 精准)。
    走 `hf buckets sync staging/. dest/skills/ --no-delete`(整 staging sync,精准增量)。
    本地无 user skill(lock 空)→ 跳不推空(首启正常)。
    """
    installed = _lock_list_installed()
    if not installed:
        return True  # 无 user skill → skip(不报错,首启前正常)
    skills_root = _path(_SKILLS_DIR_REL)
    if not os.path.isdir(skills_root):
        return True
    # 构造 staging:复 user skills 子树 + .hub 徽志三文件
    staging = tempfile.mkdtemp(prefix="home-skills-", dir=_STAGING_DIR)
    try:
        st_skills = os.path.join(staging, "skills")
        os.makedirs(st_skills, exist_ok=True)
        n_skills = 0
        for entry in installed:
            install_path = entry.get("install_path") or ""
            if not install_path:
                continue
            src = os.path.join(skills_root, install_path)
            if not os.path.isdir(src):
                continue
            dst = os.path.join(st_skills, install_path)
            shutil.copytree(src, dst, ignore=shutil.ignore_patterns(
                ".git", "__pycache__", "*.pyc", "*.pyo"))
            n_skills += 1
        if n_skills == 0:
            return True  # lock 列了但本地全缺(不该达,兜底 skip)
        # .hub 徽志三文件(lock.json 追踪 + audit.log 审计 + taps.json github tap 配)
        hub_src = os.path.join(skills_root, ".hub")
        if os.path.isdir(hub_src):
            st_hub = os.path.join(st_skills, ".hub")
            os.makedirs(st_hub, exist_ok=True)
            for fname in _HUB_KEEP:
                fsrc = os.path.join(hub_src, fname)
                if os.path.isfile(fsrc):
                    shutil.copy2(fsrc, os.path.join(st_hub, fname))
        dest = _skills_dest_dir()
        cmd = ["hf", "buckets", "sync", st_skills + os.sep, dest]
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=300,
                env=os.environ.copy(),
            )
            if result.returncode != 0:
                print(
                    f"[home-upload] skills/ hf buckets sync failed code="
                    f"{result.returncode} stderr={result.stderr.strip()[:300]}",
                    flush=True,
                )
                return False
            out = (result.stdout or "").strip()
            n_created = out.count('"action": "create"') + out.count("created")
            n_updated = out.count('"action": "update"') + out.count("updated")
            print(
                f"[home-upload] skills/ ok user_skills={n_skills} "
                f"(created~{n_created} updated~{n_updated}) dest={dest}",
                flush=True,
            )
            return True
        except subprocess.TimeoutExpired:
            print("[home-upload] skills/ hf buckets sync timeout (300s)", flush=True)
            return False
        except Exception as e:  # noqa: BLE001
            print(f"[home-upload] skills/ failed: {e}", flush=True)
            return False
    finally:
        try:
            shutil.rmtree(staging, ignore_errors=True)
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
    # ★2026-08-08 plugins/ 目录 sync(独立增量 sig 路径,不混单文件 state)
    psig = _plugins_local_sig()
    if psig is not None:
        pkey = "_plugins_dir_sig"
        if list(psig) != state.get(pkey):
            if _upload_plugins():
                next_state[pkey] = list(psig)
        # 失败保旧 state 下轮重试
    # ★2026-08-08 skills/ user-installed 目录 sync(路线 B 精准,基于 .hub/lock.json)
    ssig = _skills_local_sig()
    if ssig is not None:
        skey = "_skills_dir_sig"
        if list(ssig) != state.get(skey):
            if _upload_skills():
                next_state[skey] = list(ssig)
        # 失败保旧 state 下轮重试
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
