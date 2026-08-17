"""首启从 HF Storage Bucket 拉回 hermes home 关键文件(与 home_files_uploader.py 配对)。

治"重启丢 dashboard 设置 + .env channel credentials + hermes 个体 memories"。
A 方案把 HERMES_HOME 移 /opt/data 本地盘治 state.db malformed,代价=重启清盘丢:
  - .env(dashboard "Credentials" 页写的 channel token,hermes 写此非 HF Secrets)
  - SOUL.md(hermes 个体人设 prompt_builder.py:1326 装入 system prompt)
  - memories/MEMORY.md + memories/USER.md(hermes 个体记忆)
  - config.yaml(dashboard 设置项:provider/参数/plugins;★start.sh 改"缺才 cp"后
    template 不再覆盖,但旧盘清空仍丢,故仍在此脚本拉回)
  - .no-bundled-skills:profile 级 marker(用户 opt-out bundled skill seeding),
    缺则 skip(未 opt-out,正常默认)→ opt-out 选择跨重启持久
★2026-08-08 plugins/ 目录拉回:dash 装的插件代码本体持久(单文件 _FILES 只保
  config.yaml 的 enabled 标记,但插件代码本体重启清盘丢 → enabled 无对应代码静默
  失效)。走 `hf buckets sync` 整目录拉(非逐文件 cp),exclude 同 uploader(.git
  历史无用/__pycache__ 重构时重建)。
★2026-08-08 skills/ 目录拉回(hermes skills install 装的第三方技能本体 +
  .hub/lock.json 追踪)。与 plugin 差异:uploader 推时基于 .hub/lock.json 精准只推
  user skills + .hub 徽志(无 bundled),故 Bucket 中 skills/ 目录纯 user 内容,
  restore 整目录 sync 拉安全(无 bundled 污染)。boot 期 restore 在 hermes 起前跑,
  hermes 后续 bundle sync 会把 bundled 加回 skills/<cat>/<name>/ 与拉回的 user skills
  同目录混,但互不覆盖(bundle sync 跳 non-manifest;user-installed 非 manifest 不碰)。
  exclude .bundled_manifest(镜像内 bundle sync 每启动重建,拉旧覆盖会干扰 sync diff)
  + .hub/quarantine + .hub/index-cache(临时/可重建)+ .git/__pycache__/*.pyc。
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
    # ★2026-08-08 .no-bundled-skills(profile 级 marker,HERMES_HOME 根 非 skills/ 下):
    #   用户 opt-out bundled skill seeding → 启动 skip bundle sync seeding。
    #   保此 marker 让 opt-out 选择跨重启持久。缺则 skip(未 opt-out,正常默认)。
    ".no-bundled-skills",
]

# ★2026-08-08 plugins/ 整目录拉回(dash 装的插件代码本体持久)。
# 走 `hf buckets sync` 整目录 sync(非逐文件 cp),exclude 同 uploader:
#  .git(git 历史 uploader 已不保)/ __pycache__/*.pyc(重构时本地重建)。
#  nexus-r2/nexus-ops:内置两 plugin,start.sh L103 每 boot cp 注入,
#   无需 Bucket 路径(避与 start.sh cp 竞态,内置恒由 start.sh 源覆)。
_PLUGINS_DIR_REL = "plugins"
_PLUGINS_EXCLUDE = [".git", "__pycache__/", "*.pyc", "*.pyo", "nexus-r2", "nexus-ops"]

# ★2026-08-08 skills/ 整目录拉回(user-installed 技能本体 + .hub 徽志)。
# uploader 基于 .hub/lock.json 精准推,Bucket 中 skills/ 仅含 user skills + 徽志
# (lock.json/audit.log/taps.json),无 bundled — restore 整目录 sync 拉安全。
# exclude .bundled_manifest(镜像内 bundle sync 每启动重建,拉旧 ManIfest 干扰 diff)
#  + .hub/quarantine/ + .hub/index-cache/ (装期临时/搜索缓存,可重建不必拉)
#  + .git/__pycache__/*.pyc(同 plugin)。
_SKILLS_DIR_REL = "skills"
_SKILLS_EXCLUDE = [
    ".git", "__pycache__/", "*.pyc", "*.pyo",
    ".bundled_manifest",
    ".hub/quarantine", ".hub/quarantine/",
    ".hub/index-cache", ".hub/index-cache/",
]


def _have_creds() -> bool:
    return bool(os.getenv("HF_TOKEN") and _OWNER and _BUCKET_NAME)


def _have_hf_cli() -> bool:
    return shutil.which("hf") is not None


def _ensure_dirs() -> None:
    """保 HERMES_HOME + memories/ + plugins/ + skills/ 目录在(应当 start.sh 已 mkdir,兜底防 race)。"""
    os.makedirs(_HERMES_HOME, exist_ok=True)
    os.makedirs(os.path.join(_HERMES_HOME, "memories"), exist_ok=True)
    os.makedirs(os.path.join(_HERMES_HOME, _PLUGINS_DIR_REL), exist_ok=True)
    os.makedirs(os.path.join(_HERMES_HOME, _SKILLS_DIR_REL), exist_ok=True)


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


def _restore_plugins() -> str:
    """plugins/ 整目录从 Bucket home-backups/plugins/ → HERMES_HOME/plugins/(走 hf buckets sync)。

    拉回策略:ephemeral /opt/data 重启清盘 → 本地 plugins/ 空 → sync 直接入(无覆盖
    风险;boot 期 hermes 起前无竞态)。Bucket 无 plugins/(uploader 未跑过 / 无装插件)
    → 跳。注:sync --no-delete(默认,不删本地不在 Bucket 的文件,但本地本应空无影响)。
    """
    src = f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{_PLUGINS_DIR_REL}"
    dst = os.path.join(_HERMES_HOME, _PLUGINS_DIR_REL)
    os.makedirs(dst, exist_ok=True)  # _ensure_dirs 已建,兜底
    cmd = ["hf", "buckets", "sync", src, dst]
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
            # Bucket 无 plugins/(uploader 未跑过 / 无装过插件)→ 首启预期,非错
            return f"skip: plugins/ bucket empty or sync failed (code={result.returncode})"
        out = (result.stdout or "").strip()
        # 抽计数(quiet 或 agent format JSONL)
        n_created = out.count('"action": "create"') + out.count("created")
        n_updated = out.count('"action": "update"') + out.count("updated")
        if n_created == 0 and n_updated == 0:
            # 无变化也 ok(但通常 boot 时本地空 → 全 create)
            return f"ok: plugins/ synced (created~{n_created} updated~{n_updated})"
        return f"ok: restored plugins/ (created~{n_created} updated~{n_updated})"
    except subprocess.TimeoutExpired:
        return "skip: plugins/ hf buckets sync timeout (300s)"
    except Exception as e:  # noqa: BLE001
        return f"skip: plugins/ {e}"


def _restore_skills() -> str:
    """skills/ 整目录从 Bucket home-backups/skills/ → HERMES_HOME/skills/(走 hf buckets sync)。

    uploader 基于 .hub/lock.json 精准推,Bucket 中 skills/ 仅含 user skills + 徽志
    (lock.json/audit.log/taps.json),无 bundled — 整目录 sync 拉安全(无污染)。
    ephemeral /opt/data 重启清盘 → 本地 skills/ 空 + .hub/ 缺 → sync 直接入(无覆
    风险;boot 期 hermes 起前无竞态)。Bucket 无 skills/(uploader 未跑过 / 无装 user
    skill)→ 跳(首启预期,非错)。
    exclude .bundled_manifest(镜像内 bundle sync 每启动重建,拉旧覆盖会干扰 sync diff)
     + .hub/quarantine/index-cache(装期临时/搜索缓存,可重建)+ .git/__pycache__/*.pyc。
    """
    src = f"hf://buckets/{_OWNER}/{_BUCKET_NAME}/{_BACKUP_SUBDIR}/{_SKILLS_DIR_REL}"
    dst = os.path.join(_HERMES_HOME, _SKILLS_DIR_REL)
    os.makedirs(dst, exist_ok=True)  # _ensure_dirs 已建,兜底
    cmd = ["hf", "buckets", "sync", src, dst]
    for pat in _SKILLS_EXCLUDE:
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
            # Bucket 无 skills/(uploader 未跑过 / 无装过 user skill)→ 首启预期,非错
            return f"skip: skills/ bucket empty or sync failed (code={result.returncode})"
        out = (result.stdout or "").strip()
        n_created = out.count('"action": "create"') + out.count("created")
        n_updated = out.count('"action": "update"') + out.count("updated")
        if n_created == 0 and n_updated == 0:
            return f"ok: skills/ synced (created~{n_created} updated~{n_updated})"
        return f"ok: restored skills/ (created~{n_created} updated~{n_updated})"
    except subprocess.TimeoutExpired:
        return "skip: skills/ hf buckets sync timeout (300s)"
    except Exception as e:  # noqa: BLE001
        return f"skip: skills/ {e}"


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
    # ★2026-08-08 plugins/ 整目录拉回(在文件之后,dash 装的插件代码本体持久)
    lines.append(f"  {_PLUGINS_DIR_REL}/: {_restore_plugins()}")
    # ★2026-08-08 skills/ 整目录拉回(hermes skills install 装的 user 技能本体 +
    #   .hub/lock.json 追踪;uploader 精准推 Bucket 纯 user 内容,整 sync 拉安全无污染)
    lines.append(f"  {_SKILLS_DIR_REL}/: {_restore_skills()}")
    return "\n".join(lines)


def main() -> None:
    print(f"[restore-home-files] {restore_once()}", flush=True)


if __name__ == "__main__":
    main()
