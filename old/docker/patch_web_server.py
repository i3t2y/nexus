#!/usr/bin/env python3
"""K-R5-2 闸门:HF iframe embed + dashboard 鉴权对齐原生 BasicAuthProvider。

base 镜像 build 期跑此脚本,对 hermes_cli/web_server.py 做一处源码补丁。
锚点 count 自检:锚漂即 AssertionError 拦 build,跨升级稳健。

历史(2026-08-02 二轮修正):
  一轮(patch 三锚点:should_require_auth→False + CORS + auth_middleware bypass):
    臆断"HF sandbox 已隔离等价旧 --insecure"把 auth gate 全关。但 hermes 原生
    BasicAuthProvider(plugins/dashboard_auth/basic/,kind: backend,bundled 自动加载)
    需 gate 开(`should_require_auth` 非 loopback 返 True → `auth_required=True`)才接管
    /login 密码表单。关 gate(锚1 return False)= basic provider 永不接管 = dashboard 公网裸跑
    = 与用户"后台自动加密码"需求向背 + 与 hermes June 2026 hardening 向背。
    锚1 错向,删。

  二轮(本版,与原生对齐):只留 CORS(锚2)。gate 回原生 `return host not in _LOOPBACK_HOST_VALUES`
  (0.0.0.0 非 loopback → True → gate 开),由 BasicAuthProvider 接管鉴权。
  auth_middleware(锚3 注入)原可删 — main 源码 web_server.py:629 原生已检查
  `if getattr(request.app.state, "auth_required", False): return await call_next(request)`
  即 `auth_required=False → 放行`,我锚3 注入是多余 + 错位(置 token_authenticated 检查前
  打乱 token-auth seam 语义)。锚3 删,原生 629 行管放行。

  激活 BasicAuthProvider 见 config.yaml + .env.example:
    HERMES_DASHBOARD_BASIC_AUTH_{USERNAME,PASSWORD,SECRET} env(三变量),
    basic plugin `requires_env: USERNAME` → 该 env 缺则不加载 → list_providers() 空
    → gate `SystemExit("Refusing to bind...")` fail-closed 拒起。配齐 → basic 注册
    → gate 通过 → /login 密码表单接管(scrypt 哈希 + HMAC stateless cookie,无 OAuth/IDP/DB)。
    仅 username + password(password_hash)非空即激活;secret 缺则随机生成(进程重启失效,
    须设固定 SECRET 让 session 跨重启)。

仅留一锚点(v0.19.1 + main 845031a grep count=1 双证,行号 345 一致零漂):

  1. CORS allow_origin_regex(限 localhost,行 345)→ allow_origins=["*"]
     解 HF iframe embed — sonoke-h.hf.space 在 huggingface.co iframe 内渲染,
     SPA fetch JS/CSS/WS 跨域回 sonoke-h.hf.space/api/*,默认 CORS regex 拒 →
     换 allow_origins=["*"] 放行所有域(HTTP fetch 层;credentials 默认 False 安全)。
     HF Space 无 X-Frame-Options/CSP frame-ancestors 头注入(v0.19.1+main grep -in 0 命中
     双证),iframe embed 仅靠 CORS 够;WebSocket 无 ws_origin 白名单不另拦。
     鉴权走 BasicAuthProvider cookie(HMAC-sig),非 CORS credential — allow_origins=["*"]
     与 cookie 鉴权无冲突(CORS preflight 不挡 SameSite cookie 流)。

注意:gate 开后 dashboard 鉴权完全由 BasicAuthProvider 接管,真正攻面从"公网裸跑"缩到
"密码表单"。HF sandbox 仍有公网扫描者(非纯隔离),密码闸门必要 — 与用户"后台加密码"吻合。
"""
from __future__ import annotations

import sys
from pathlib import Path

WORKDIR = Path("/opt/hermes-agent")
TARGET = WORKDIR / "hermes_cli" / "web_server.py"


def main() -> int:
    if not TARGET.exists():
        print(f"[base] FATAL: {TARGET} not found — clone/checkout failed", file=sys.stderr)
        return 2

    s = TARGET.read_text(encoding="utf-8")
    orig_len = len(s)

    # ── (1) 仅一处:CORS allow_origin_regex → allow_origins=["*"] ───────
    # should_require_auth + auth_middleware 不改(回原生,basic provider 接管鉴权)。
    old_cors = r'    allow_origin_regex=r"^https?://(localhost|127\.0\.0\.1)(:\d+)?$",'
    assert old_cors in s, "CORS allow_origin_regex line not found — anchor drifted, pin HERMES_AGENT_TAG"
    new_cors = (
        '    allow_origins=["*"],'
        "  # nexus K-R5-2: HF iframe embed 跨域放行"
        "(huggingface.co iframe 嵌 sonoke-h.hf.space);"
        "鉴权走 BasicAuthProvider cookie 非 CORS credential,无冲突"
    )
    s = s.replace(old_cors, new_cors, 1)

    TARGET.write_text(s, encoding="utf-8")
    print(
        "[base] web_server.py patched: CORS allow_origins=[*] only "
        "(gate + auth_middleware 原生,basic provider 接管鉴权) "
        f"(len {orig_len}->{len(s)})"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
