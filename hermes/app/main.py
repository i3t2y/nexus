"""Hermes Space 启动入口(K 形态,2026-08-02 双项目实证修正)。

K 阶段定局:弃自建 Gradio Dashboard + FastAPI 自路由 + agent_server.py 单例。
改 hermes 全原生三组件 **subprocess**(非 in-proc daemon thread — HermesFace
+ HuggingMes 两生产项目实证推翻 plan K1 决策-4 deep 推论):
  - 子进程 1 = `hermes gateway run`
    (含 api_server adapter HTTP /v1/runs,API_SERVER_KEY ≥16 触发)+ IM。
  - 子进程 2 = `hermes dashboard --host 0.0.0.0 --port 7860 --no-open --skip-build`
    (HF 公网域指 7860,直监听非反代;base 镜已预建 web_dist 故 --skip-build)。

为何弃 in-proc daemon thread:实证两项目均 subprocess 子进程跑通;in-proc
`web_server.start_server()` 在 daemon thread 跑 asyncio(uvicorn 0.49+ signal
handler main-thread check / loop 冲突)= HF 实跑 dashboard daemon thread
重复挂掉 40+ 次根因。subprocess 独进程绕此。`--insecure` flag 已 DEPRECATED
NO-OP(June 2026 hardening),不再用 — HF iframe 嵌入靠 CORS patch(改
web_server.py X-Frame-Options + CSP,见 start.sh _patch_web_server_cors)。

本文件职责 = 极薄 boot:
  1. 环境前置检查(API_SERVER_KEY 兜底自生成,但 HF 应经 Secret 注真值)。
  2. subprocess.Popen 起两子进程。
  3. 主线程监两 Popen.poll(),任一死则全 proc exit(让 HF/supervisor
     重启,非半死态)+ 带死因 stderr。

原 main.py 的 /run//enqueue//dequeue//state//task 自路由 + Gradio + R2 helper
全迁:HTTP 任务入口 → api_server /v1/runs;Dashboard → 原生 SPA;R2 CRUD →
nexus-r2 plugin;下游探活 + 业务表只读 → nexus-ops plugin。
不再跑 uvicorn app.main:app(K 路无 FastAPI 自路由壳)。HF 7860 由 dashboard 直监听。
"""
from __future__ import annotations

import os
import secrets
import subprocess
import sys
import time
from typing import Optional


def _ensure_api_server_key() -> None:
    """api_server adapter 真触发器 = env API_SERVER_KEY ≥16 字符(deep 修正,
    非 API_SERVER_ENABLED deprecated)。

    缺失本地 dev 兜底自生成随机串 export,让本地集成测起 api_server;
    生产 HF 必经 Secret 注真值(self-gen 仅 dev fallback,非生产鉴权手段)。
    """
    key = os.environ.get("API_SERVER_KEY", "").strip()
    if len(key) < 16:
        gen = secrets.token_urlsafe(24)
        os.environ["API_SERVER_KEY"] = gen
        print(f"[hermes-boot] API_SERVER_KEY 缺失或 <16,dev 兜底自生成: {gen[:8]}...",
              file=sys.stderr, flush=True)


def _spawn_gateway() -> "subprocess.Popen[bytes]":
    """子进程 1:`hermes gateway run` foreground。

    HermesFace run_hermes():subprocess.Popen 起 hermes gateway(含 api_server
    adapter 同 loop 地 8642 + telegram/discord adapter)。
    stdout/stderr pipe 主线程读 + 转印 boot stderr(HF Logs 可见)。
    """
    hermes_bin = _hermes_bin()
    # --replace:幂等覆盖残留 gateway 实例(HF Space reboot/start.sh while 重启场景下
    # 旧 gateway pid 可能残留(telegram polling 抗 SIGTERM 不及时退)→ pid 锁撞死循环。
    # --replace 让新 gateway 自动 stop+replace 旧实例(systemd-managed 场景同理)。
    cmd = [hermes_bin, "gateway", "run", "--replace", "--accept-hooks"]
    print(f"[hermes-boot] spawn gateway: {' '.join(cmd)}", file=sys.stderr, flush=True)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )


def _spawn_dashboard() -> "subprocess.Popen[bytes]":
    """子进程 2:`hermes dashboard --host 0.0.0.0 --port 7860 --no-open --skip-build`。

    直监听 HF 7860 公网域(K-R2 直监听非反代最简解)。
    --skip-build:base 镜已预建 web_dist(K-R4 过),免 HF 期 npm build。
    --insecure 不传:June 2026 hardening 后已 DEPRECATED NO-OP,公网绑始终须
      auth provider;HF iframe 嵌入靠 CORS patch(非 --insecure)。
    DASHBOARD_BIND_HOST 控制 host(缺省 0.0.0.0 HF 公网;本地测可设 127.0.0.1
      免 auth gate,因 127.0.0.1 是 loopback → should_require_auth 返 False →
      gate 关 → 免 auth provider)。
    K-R5 闸门终局(2026-08-02 实证,与原生 BasicAuthProvider 对齐):
      鉴权 = hermes 原生 BasicAuthProvider(plugins/dashboard_auth/basic/,
      kind: backend bundled 自动加载,非 OAuth/NousPortal/register app)。
      带 env HERMES_DASHBOARD_BASIC_AUTH_{USERNAME,PASSWORD,SECRET} 触发:
        0.0.0.0 非 loopback → should_require_auth=True → auth_required=True
        → gate 开 → list_providers() 检 basic plugin requires_env=USERNAME 通过
        + 内检 password 非空 → 注册 → /login 密码表单(scrypt 哈希 + HMAC
        stateless cookie)。缺任一 → list_providers() 空 → gate SystemExit
        fail-closed 拒起。secret 须固定(默认随机重启失效 session)。
    """
    hermes_bin = _hermes_bin()
    host = os.environ.get("DASHBOARD_BIND_HOST", "0.0.0.0")
    port = os.environ.get("PORT", "7860")
    cmd = [hermes_bin, "dashboard",
          "--host", host, "--port", port,
          "--no-open", "--skip-build"]
    print(f"[hermes-boot] spawn dashboard: {' '.join(cmd)}", file=sys.stderr, flush=True)
    return subprocess.Popen(
        cmd,
        stdout=subprocess.PIPE,
        stderr=subprocess.STDOUT,
        env=os.environ.copy(),
    )


def _hermes_bin() -> str:
    """优先 PATH 内 hermes(镜像内 /usr/local/bin/hermes);fallback ~/.venv/bin/hermes
    (HermesFace 风格,uv 装形态)。"""
    import shutil
    found = shutil.which("hermes")
    if found:
        return found
    venv_bin = os.path.expanduser("~/.venv/bin/hermes")
    if os.path.exists(venv_bin):
        return venv_bin
    return "hermes"  # 让 PATH 解析,失败则 Popen 抛 FileNotFoundError


def _drain(proc: "subprocess.Popen[bytes]", tag: str, stop: "object") -> None:
    """后台线程:把子进程 stdout/stderr 行转印到 boot stderr(HF Logs 可见)。"""
    assert proc.stdout is not None
    try:
        for line in iter(proc.stdout.readline, b""):
            if stop.is_set():
                break
            sys.stderr.write(f"[{tag}] {line.decode('utf-8', 'replace')}")
            sys.stderr.flush()
    except Exception as e:  # noqa: BLE001
        print(f"[hermes-boot] {tag} drain 结束: {e}", file=sys.stderr, flush=True)


def boot() -> None:
    """主线程 boot:env 前置 → 两 subprocess → 阻塞监死。"""
    _ensure_api_server_key()

    gw = _spawn_gateway()
    db = _spawn_dashboard()
    import threading
    stop = threading.Event()

    # 两子进程日志转印到 boot stderr(HF Logs 可见)
    for proc, tag in ((gw, "gateway"), (db, "dashboard")):
        t = threading.Thread(target=_drain, args=(proc, tag, stop),
                             name=f"drain-{tag}", daemon=True)
        t.start()

    print(f"[hermes-boot] spawned gateway(pid={gw.pid}) + dashboard(pid={db.pid})",
          file=sys.stderr, flush=True)

    # 主线程阻塞监死:子进程死则全 proc exit(HF/supervisor 重启,非半死)。
    while True:
        time.sleep(3)
        gw_rc = gw.poll()
        db_rc = db.poll()
        if gw_rc is not None or db_rc is not None:
            stop.set()
            dead = []
            if gw_rc is not None:
                dead.append(f"gateway (rc={gw_rc})")
            if db_rc is not None:
                dead.append(f"dashboard (rc={db_rc})")
            print(f"[hermes-boot] FATAL: subprocess died: {', '.join(dead)} → exit proc",
                  file=sys.stderr, flush=True)
            # 杀残存子进程防僵尸
            for proc in (gw, db):
                if proc.poll() is None:
                    proc.terminate()
            raise SystemExit(1)


# ── 7860 暴露面(HF Space Settings 健康探测打此路径须 200) ──────────
# dashboard 直监听 7860,原生 SPA 自带 / 路由 + /health。K 路全原生,不再自起 FastAPI 壳。
def main() -> None:
    boot()


if __name__ == "__main__":
    main()
