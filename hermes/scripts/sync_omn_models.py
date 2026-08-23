#!/usr/bin/env python3
"""动态同步 omn 网关模型到 hermes providers.omn.models 白名单。

★2026-08-23 三件套模型路由治理: 解决"静态白名单跟不上 omn 动态模型"矛盾。

问题根因(源码级实证):
  - omn 是唯一模型网关(https://nonoke-omn.hf.space/v1), /v1/models 返回 278 个
    模型, 按 provider 分组, 含 auto/* 通配占位项(owned_by=combo, 不可直接调用)
    和 nvidia/mistral/tllm/oc/... 真实 provider 块。
  - hermes 的 discover_models: true 会把 /v1/models 全量原样灌进 picker
    (fetch_api_models 不过滤, model_switch.py:1530/1709), 产生 200+ 项含大量
    auto/* 僵尸占位, 污染模型选择。
  - discover_models: false 则静态白名单, 不随 omn 上游新增模型自动更新,
    需每次手动加 providers.omn.models 白名单行。

本脚本的方案(受控动态白名单):
  - 保持 discover_models: false(浏览器只显白名单, 干净无僵尸)。
  - 本脚本 boot 期(hermes 起前, config.yaml 生成后)定时/单次拉 omn /v1/models。
  - 过滤: 丢弃 auto/* 占位 + 不在白名单前缀内的 provider, 只保留真实可用的
     provider 前缀模型(默认 nvidia, 可经 OMNI_MODEL_PREFIXES 环境变量配多个)。
  - 回写 providers.omn.models 白名单(merge 进 config.yaml), 只增不删已有项,
     omn 新增的真实模型自动加入, 动态跟随上游。

门控(同 persist 脚本模式, 零臆断容错不阻断 boot):
  - OPENAI_API_KEY(omn 鉴权) + OMNI_BASE_URL(默认 https://nonoke-omn.hf.space/v1)
  - 失败/超时/JSON 解析错 → 保留现 config 不动(不破坏现有模型配置), 记日志退出
  - 不依赖第三方库(urllib 标准库 + ruamel 已有; 无 ruamel 则 yaml 兜底)

过滤规则(白名单前缀, 可配置):
  - OMNI_MODEL_PREFIXES 环境变量, 逗号分隔, 默认 "nvidia"
  - 保留 id.startswith(prefix + "/") 的模型
  - auto/* 永远丢弃(占位不可调用)
  - 无前缀裸 ID(nim-pool/nim-codex)丢弃(非独立模型)
"""

import json
import os
import re
import sys
import urllib.request
from pathlib import Path

HERMES_HOME = Path(os.environ.get("HERMES_HOME", "/opt/data/.hermes"))
CONFIG_PATH = HERMES_HOME / "config.yaml"
TMP_SUFFIX = ".omn-models.tmp"


def log(msg: str) -> None:
    print(f"[sync-omn-models] {msg}", flush=True)


def load_config() -> dict:
    """读取 config.yaml(ruamel 保注释, 兜底 pyyaml)。"""
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ="rt")
        with open(CONFIG_PATH) as f:
            return yaml.load(f) or {}
    except Exception:
        import yaml
        with open(CONFIG_PATH) as f:
            return yaml.safe_load(f) or {}


def save_config(cfg: dict) -> None:
    """写回 config.yaml(用与加载相同的解析器保持格式)。"""
    try:
        from ruamel.yaml import YAML
        yaml = YAML(typ="rt")
        with open(CONFIG_PATH, "w") as f:
            yaml.dump(cfg, f)
    except Exception:
        import yaml
        with open(CONFIG_PATH, "w") as f:
            yaml.safe_dump(cfg, f, allow_unicode=True, sort_keys=False)


def fetch_omn_models(base_url: str, api_key: str, timeout: float = 10.0) -> list:
    """拉 omn /v1/models, 返回模型 ID 列表(标准库 urllib, 不依赖第三方)。"""
    url = base_url.rstrip("/") + "/models"
    req = urllib.request.Request(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "User-Agent": "hermes-sync-omn-models",
        },
    )
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    ids = [m.get("id") for m in data.get("data", []) if isinstance(m, dict)]
    return [i for i in ids if isinstance(i, str) and i]


def filter_models(ids: list, prefixes: list) -> list:
    """滤掉 auto/* 占位 + 非白名单前缀, 返回需写入白名单的模型。

    规则:
      - auto/* 永远丢弃(combo 通配占位, 不可直接调用)
      - 只保留 prefix + "/" 开头的模型(白名单前缀)
      - 无前缀裸 ID 丢弃(非独立模型)
    """
    kept = []
    for mid in ids:
        if mid.startswith("auto/"):
            continue  # 占位通配, 永滤
        hit = any(mid.startswith(p + "/") for p in prefixes)
        if hit:
            kept.append(mid)
    return sorted(set(kept))


def merge_into_providers(cfg: dict, model_ids: list) -> tuple:
    """merge 模型白名单进 providers.omn.models, 只增不删已有项。

    Returns (changed: bool, omn: dict, added: list, removed: list)
    """
    providers = cfg.setdefault("providers", {})
    omn = providers.setdefault("omn", {})
    existing = omn.get("models", {})

    if not isinstance(existing, dict):
        existing = {}

    # 保留已有 context_length 覆盖(用户手设的), 新增的用 omn 返回的 context
    new_models = dict(existing)
    added, removed = [], []
    for mid in model_ids:
        if mid not in existing:
            new_models[mid] = {}
            added.append(mid)

    # 检测已删除的模型(omn 不再返回的), 保留用户手设的不自动删(保守)
    for mid in list(existing.keys()):
        if mid not in model_ids and not mid.startswith("auto/"):
            # 白名单里 omn 已不返回的旧模型: 记日志但不自动删(防用户手设的
            # context_length 覆盖丢失; 用户可手动清)
            removed.append(mid)

    omn["models"] = new_models
    return (bool(added or removed), omn, added, removed)


def main() -> int:
    api_key = (os.environ.get("OPENAI_API_KEY") or "").strip()
    base_url = (os.environ.get("OMNI_BASE_URL") or "https://nonoke-omn.hf.space/v1").strip()
    prefixes = [p.strip() for p in os.environ.get("OMNI_MODEL_PREFIXES", "nvidia").split(",") if p.strip()]

    if not api_key:
        log("skip: no OPENAI_API_KEY (omn 鉴权), keeping existing config")
        return 0
    if not CONFIG_PATH.exists():
        log(f"skip: no config.yaml at {CONFIG_PATH}, run after config generation")
        return 0

    log(f"fetching models from {base_url} (prefixes={prefixes})...")
    try:
        ids = fetch_omn_models(base_url, api_key)
    except Exception as e:
        log(f"ERROR fetch failed: {e} (keeping existing config)")
        return 1

    if not ids:
        log("ERROR empty model list from omn (keeping existing config)")
        return 1

    kept = filter_models(ids, prefixes)
    log(f"fetched {len(ids)} models, kept {len(kept)} (prefix filter, auto/* dropped)")

    cfg = load_config()
    if cfg is None:
        log("ERROR could not load config.yaml (keeping existing)")
        return 1

    try:
        changed, omn, added, removed = merge_into_providers(cfg, kept)
    except Exception as e:
        log(f"ERROR merge failed: {e} (keeping existing config)")
        return 1

    if not changed:
        log("no new models to sync (whitelist already up to date)")
        return 0

    save_config(cfg)
    log(f"config.yaml updated: added={len(added)} kept_unchanged={len(kept)-len(added)} obsolete={len(removed)}")
    log(f"  added: {', '.join(added[:8])}{'...' if len(added) > 8 else ''}")
    log("  NOTE: restart hermes (not rebuild) for new whitelist to take effect")
    return 0


if __name__ == "__main__":
    sys.exit(main())