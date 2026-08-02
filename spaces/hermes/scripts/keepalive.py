"""Keep-alive 辅助（借鉴 HuggingMes cloudflare-keepalive-setup.py）。

两类用途：
1. 自身被外部监测网站定期 ping /health（主要保活手段，已确认稳定）。
2. 本脚本周期性调下游 Space 的 /health 唤醒它们（防下游休眠），
   间隔随机化避免固定周期特征明显。

第三用途（关键）：**Supabase 自身保活**。
  Supabase 免费档 1 周不活跃自动暂停（暂停后 DB 不可用，REST 503）。
  本脚本每轮往 space_health 表 insert 一行 = 周期性轻量写 DB，把"上次活动"刷新，
  使 Supabase 不被判为不活跃 → 防暂停。insert 失败（DB 已暂停）会打日志，
  此时需手动去 Supabase dashboard 恢复项目。

环境变量：
  KEEPALIVE_INTERVAL_BASE (默认 600 秒)
  KEEPALIVE_INTERVAL_JITTER (默认 180 秒)
  NEXUS_API_KEY / LANGGRAPH_URL / CLAUDE_URL / CODEX_URL
  SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY (写 space_health 留痕 + Supabase 自身保活)
"""
from __future__ import annotations

import os
import random
import sys
import time

sys.path.insert(0, "/data/libs")  # Bucket 挂载点;本地调试兜底

import httpx  # noqa: E402

_BASE = int(os.getenv("KEEPALIVE_INTERVAL_BASE", "600"))
_JITTER = int(os.getenv("KEEPALIVE_INTERVAL_JITTER", "180"))
_SPACES = {
    "langgraph": os.getenv("LANGGRAPH_URL", ""),
    "claude": os.getenv("CLAUDE_URL", ""),
    "codex": os.getenv("CODEX_URL", ""),
}
# omniroute(模型平面)非下游 nexus Space,无 /health;是 OpenAI /v1/chat/completions 兼容端点(Bearer 鉴权)。
# 保活用最小 POST /v1/chat/completions(max_tokens=1) ping,凭证 GLM_API_KEY(= zai api_key = omniroute Bearer)。
# base_url 走 GLM_BASE_URL(已带 /v1,与 config.yaml model.base_url + hermes zai provider 对齐);
#   此前误用 anthropic Messages /v1/messages + x-api-key(omniroute 协议错配 anthropic 路死,已证伪改 zai)。
# 缺 GLM_BASE_URL/GLM_API_KEY 则跳(hermes 主推理路也起不来,跳不增损)。
_OMNI_BASE = os.getenv("GLM_BASE_URL", "").rstrip("/")
_OMNI_KEY = os.getenv("GLM_API_KEY", "")
_OMNI_MODEL = os.getenv("HERMES_MODEL", "glm-5.2")


def _space_headers() -> dict[str, str]:
    """探下游 HF Space 的 header（与 libs/shared/gateway.py 一致，防 #9 header 冲突）。

    X-Nexus-Key 给 app auth()；Authorization 留给 HF 层（私有 Space 需 HF_TOKEN）。
    """
    h: dict[str, str] = {"X-Nexus-Key": f"Bearer {os.getenv('NEXUS_API_KEY', '')}"}
    if os.getenv("HF_TOKEN"):
        h["Authorization"] = f"Bearer {os.getenv('HF_TOKEN')}"
    return h


def probe_omniroute() -> tuple[str, str, str]:
    """omniroute(模型平面)保活:最小 OpenAI chat-completions ping。返 (摘要, status, detail)。

    omniroute 无 /health(非 nexus 下游 app),走 POST /v1/chat/completions max_tokens=1。
    OpenAI 兼容协议:Bearer 鉴权(非 anthropic x-api-key);base_url 走 GLM_BASE_URL(已带 /v1)。
    复 omniroute 路径 = 防 hermes 主推理(glm-5.2 经 zai→omniroute)首请冷启动超时(48h 不活动休眠)。
    """
    if not _OMNI_BASE or not _OMNI_KEY:
        return ("omniroute: skip (no GLM_BASE_URL/GLM_API_KEY)", "skip", "unconfigured")
    # GLM_BASE_URL 形如 https://nonoke-omn.hf.space/v1(已带 /v1),拼 /chat/completions。
    # 若历史值无 /v1,rstrip 后再补(双兜底,容错旧配置)。
    base = _OMNI_BASE if _OMNI_BASE.endswith("/v1") else f"{_OMNI_BASE}/v1"
    try:
        r = httpx.post(
            f"{base}/chat/completions",
            headers={
                "Authorization": f"Bearer {_OMNI_KEY}",
                "content-type": "application/json",
            },
            json={"model": _OMNI_MODEL, "max_tokens": 1, "messages": [{"role": "user", "content": "ping"}]},
            timeout=20.0,
        )
        status = "ok" if r.is_success else "down"
        return (f"omniroute: {r.status_code}", status, str(r.status_code))
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:200]
        return (f"omniroute: DOWN ({msg})", "down", msg)


_SUPA = None


def _supa():
    """惰性连 Supabase，无凭证则返回 None（保活照跑，仅不写库）。"""
    global _SUPA
    if _SUPA is not None:
        return _SUPA
    url = os.getenv("SUPABASE_URL")
    key = os.getenv("SUPABASE_SERVICE_ROLE_KEY") or os.getenv("SUPABASE_ANON_KEY")
    if not url or not key:
        return None
    try:
        from supabase import create_client  # noqa: E402

        _SUPA = create_client(url, key)
    except Exception:  # noqa: BLE001
        _SUPA = None
    return _SUPA


def _sleep_rand() -> int:
    # 随机抖动间隔，避免固定周期形成可观测规律
    return _BASE + random.randint(-_JITTER, _JITTER)


def probe(space: str, url: str) -> tuple[str, str, str]:
    """返回 (状态摘要, status, detail) 用于入库。"""
    try:
        r = httpx.get(f"{url}/health", headers=_space_headers(), timeout=15.0)
        status = "ok" if r.is_success else "down"
        return (f"{space}: {r.status_code}", status, str(r.status_code))
    except Exception as e:  # noqa: BLE001
        msg = str(e)[:200]
        return (f"{space}: DOWN ({msg})", "down", msg)


def _write_health(space: str, status: str, detail: str) -> None:
    """写 space_health 表：留痕 + 副作用是刷新 Supabase 活动时间防暂停。"""
    supa = _supa()
    if supa is None:
        return
    try:
        supa.table("space_health").insert({
            "space": space,
            "status": status,
            "detail": detail,
        }).execute()
    except Exception as e:  # noqa: BLE001
        # 若是 Supabase 已暂停，会在此暴露；需手动恢复项目。
        print(f"[keepalive] supabase insert failed (可能项目已暂停): {e}", flush=True)


def main() -> None:
    print("[keepalive] start", flush=True)
    while True:
        wrote_supabase = False
        for space, url in _SPACES.items():
            if url:
                summary, status, detail = probe(space, url)
                print(f"[keepalive] {summary}", flush=True)
                _write_health(space, status, detail)
                wrote_supabase = True
                time.sleep(random.uniform(2, 8))  # 空间调用也加随机延时

        # omniroute(模型平面)保活:ping /v1/messages 防 48h 休眠 → 防路B首请超时。
        summary, status, detail = probe_omniroute()
        print(f"[keepalive] {summary}", flush=True)
        _write_health("omniroute", status, detail)

        # 即使没配下游 Space URL，也至少写一次 Supabase 保活自身（防 1 周暂停）。
        if not wrote_supabase:
            _write_health("hermes", "ok", "no-downstream-configured")

        time.sleep(_sleep_rand())


if __name__ == "__main__":
    main()
