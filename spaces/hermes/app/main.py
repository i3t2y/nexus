"""Hermes Space 启动入口(K 形态,2026-08-02 实证推翻重定向)。

K 阶段定局:弃自建 Gradio Dashboard + FastAPI 自路由 + agent_server.py 单例。
改 hermes 全原生三组件单进程双线程(HF 单进程硬约束):
  - daemon thread 1 (gateway): asyncio.run(start_gateway) 同 loop 起
    api_server adapter(HTTP /v1/runs,API_SERVER_KEY ≥16 触发)+ telegram/discord IM。
  - daemon thread 2 (dashboard): in-proc 直跑 web_server.start_server --port 7860
    (HF 公网域指 7860,直监听非反代;daemon thread 不产 subprocess cmdline 避扫杀)。
  - 两 plugin tab(nexus-r2 R2 / nexus-ops 下游+业务表)经 config.yaml
    plugins.enabled + dashboard/manifest.json 自动注 dashboard。

本文件职责 = 极薄 boot:
  1. 环境前置检查(API_SERVER_KEY 兜底自生成,但 HF 应经 Secret 注真值)。
  2. 起 2 daemon thread 各持独 asyncio loop。
  3. 主线程阻塞保活(while sleep;任一 daemon thread 死则全 proc exit
     让 HF/supervisor 重启,非半死态)。

原 main.py 的 /run//enqueue//dequeue//state//task 自路由 + Gradio + R2 helper 全迁:
  - HTTP 任务入口 → hermes 原生 api_server /v1/runs(K1 决策-1)。
  - Dashboard → hermes 原生 SPA(K1 决策-2)。
  - R2 CRaUD → nexus-r2 plugin dashboard/plugin_api.py。
  - 下游探活 + 业务表只读 → nexus-ops plugin dashboard/plugin_api.py。

不再跑 uvicorn app.main:app(K 路无 FastAPI 自路由壳)。HF 7860 由 dashboard 直监听。
"""
from __future__ import annotations

import os
import secrets
import sys
import threading
import time
from typing import Any


def _ensure_api_server_key() -> None:
    """api_server adapter 真触发器 = env API_SERVER_KEY ≥16 字符(deep 修正,
    非 API_SERVER_ENABLED deprecated)。

    缺失则本地 dev 兜底自生成随机串并 export,让本地集成测能起 api_server;
    生产 HF 必须经 Secret 注真值(此 self-gen 仅 dev fallback,非生产鉴权手段)。
    """
    key = os.environ.get("API_SERVER_KEY", "").strip()
    if len(key) < 16:
        gen = secrets.token_urlsafe(24)
        os.environ["API_SERVER_KEY"] = gen
        print(f"[hermes-boot] API_SERVER_KEY 缺失或 <16,dev 兜底自生成: {gen[:8]}...",
              file=sys.stderr, flush=True)


def _spawn_gateway() -> None:
    """daemon thread 1:起 gateway(含 api_server adapter 同 async loop)。

    start_gateway() 协程经 asyncio.run 自建独 loop 阻塞驻留(POSIX)。
    api_server adapter 随 gateway 起 aiohttp web server 8642(非独立 loop),
    telegram/discord adapter 亦同 loop。
    """
    import asyncio

    try:
        from gateway.run import start_gateway
    except Exception as e:  # noqa: BLE001
        print(f"[hermes-boot] FATAL: gateway.run import 失败: {e}", file=sys.stderr, flush=True)
        raise
    try:
        ok = asyncio.run(start_gateway())
        print(f"[hermes-boot] gateway exited ok={ok}", file=sys.stderr, flush=True)
    except SystemExit as e:
        print(f"[hermes-boot] gateway SystemExit code={e.code}", file=sys.stderr, flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[hermes-boot] gateway crashed: {e}", file=sys.stderr, flush=True)
        raise


def _spawn_dashboard() -> None:
    """daemon thread 2:in-proc 直跑 web_server.start_server --port 7860。

    直监听 HF 7860 公网域(K-R2 直监听非反代最简解)。
    非 subprocess hermes dashboard:in-proc 起 web_server.start_server,
    避 main.py:_find_stale_dashboard_pids 扫 cmdline 杀进程(deep 修正)。
    本地集成测绑 127.0.0.1 免 OAuth(K-R5 公网鉴权留 HF 部署期)。

    DASHBOARD_BIND_HOST 控制:host=0.0.0.0 公网(HF,需 auth provider),
    缺省 127.0.0.1 loopback 免 auth(本地测)。headless=True 禁自动开浏览器。
    """
    host = os.environ.get("DASHBOARD_BIND_HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "7860"))
    try:
        from hermes_cli.web_server import start_server
    except Exception as e:  # noqa: BLE001
        print(f"[hermes-boot] FATAL: web_server import 失败: {e}", file=sys.stderr, flush=True)
        raise
    try:
        start_server(
            host=host,
            port=port,
            open_browser=False,
            allow_public=(host not in ("127.0.0.1", "localhost")),
            headless=True,
        )
        print("[hermes-boot] dashboard start_server returned (本应阻塞驻留)", file=sys.stderr, flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"[hermes-boot] dashboard crashed: {e}", file=sys.stderr, flush=True)
        raise


def boot() -> None:
    """主线程 boot:env 前置 → 2 daemon thread → 阻塞保活监死。"""
    _ensure_api_server_key()

    tg = threading.Thread(target=_spawn_gateway, name="hermes-gateway", daemon=True)
    td = threading.Thread(target=_spawn_dashboard, name="hermes-dashboard", daemon=True)
    tg.start()
    td.start()
    print(f"[hermes-boot] spawned gateway(tid={tg.ident}) + dashboard(tid={td.ident})",
          file=sys.stderr, flush=True)

    # 主线程阻塞保活:daemon thread 死则全 proc exit(HF/supervisor 重启,非半死)。
    while True:
        time.sleep(5)
        if not tg.is_alive() or not td.is_alive():
            dead = []
            if not tg.is_alive():
                dead.append("gateway")
            if not td.is_alive():
                dead.append("dashboard")
            print(f"[hermes-boot] FATAL: daemon thread(s) died: {', '.join(dead)} → exit proc",
                  file=sys.stderr, flush=True)
            # 非零 exit 让 HF/supervisor 视崩溃重启
            raise SystemExit(1)


# ── 7860 暴露面(HF Space Settings 健康探测打此路径须 200) ──────────
# dashboard 直监听 7860,原生 SPA 自带 / 健康路由。若 HF 探测需独立 /health
# (与 dashboard 鉴权无关),dashboard 端原生有 /health 路由可探。
# 此处不再自起 FastAPI 壳(K 路全原生)。
def main() -> None:
    boot()


if __name__ == "__main__":
    main()
