# Nexus GHCR base 镜像 —— 依赖层(绝对静态化的"镜像层")
# 把四 Space 共用 Python 依赖 + hermes-agent 内核(NousResearch Hermes Agent 核心子集)打包进此镜像
# 推 GHCR ghcr.io/<owner>/nexus-base:stable
# 各 Space Dockerfile ARG BASE_IMAGE=ghcr.io/<owner>/nexus-base:stable + FROM ${BASE_IMAGE} 引用
# 升级依赖或 hermes-agent tag: 改本文件 → 本地 build 覆盖 :stable → HF repo 改 README 一字符 git push
# 此镜像不含任何 Nexus 业务代码(代码进 HF Storage Bucket rw /data 挂载)
#
# 构建命令(本地):
#   docker build -t ghcr.io/i3t2y/nexus-base:stable -f docker/nexus-base.Dockerfile docker/
#   docker push ghcr.io/i3t2y/nexus-base:stable
#   docker tag  ghcr.io/i3t2y/nexus-base:stable ghcr.io/i3t2y/nexus-base:vN
#   docker push ghcr.io/i3t2y/nexus-base:vN

# ──────────────────────────────────────────────────────────────────────
# K-R6 闸门 stage:自源码编 SQLite 3.53.4(≥3.51.3)防 WikiLeaks-free → fresh DB 强 DELETE 致 litestream 静默 off
# ──────────────────────────────────────────────────────────────────────
# hermes_state.py:509 is_sqlite_wal_reset_vulnerable 把 SQLite 3.7.0–3.51.2 判 vulnerable,
# fresh state.db 强制 DELETE journal fallback 无视 config journal_mode:wal(:639),litestream 需 WAL 跟踪
# → fresh DB DELETE = litestream 静默死,V7 测假阳(restore 成功 ≠ WAL 增量真流)。
# Debian 13(trixie)ships 3.46.1 含 WAL-reset bug;python:3.11-slim(bookworm)更旧。
# 抄上游 Dockerfile:5-41 sqlite_build stage 自编 libsqlite3.so.3.53.4 + 全套 FTS3/4/5/RTREE/MATH 编译 flag,
# 运行期 COPY 进 /usr/local/lib + 软链 libsqlite3.so.0 + ld.so.conf.d + ldconfig 优先,python3 import sqlite3 即链此。
# 进度 3.53.4 = 上游 #70480 修复版(≥3.51.3 闸门 ✓)。
FROM debian:13.4 AS sqlite_build
ARG SQLITE_AUTOCONF_VERSION=3530400
ARG SQLITE_SHA256=0e9483900e92cd5de8fd48d16bf9200145a61f7fd5be542a5ac81d8a9516eb9c
RUN apt-get -o Acquire::Retries=3 update && \
    apt-get -o Acquire::Retries=3 install -y --no-install-recommends \
        build-essential ca-certificates curl && \
    rm -rf /var/lib/apt/lists/* && \
    (curl -fsSL --retry 1 --retry-all-errors --connect-timeout 15 --max-time 60 \
        -o /tmp/sqlite.tar.gz \
        "https://sqlite.org/2026/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz" || \
     curl -fsSL --retry 3 --retry-all-errors --connect-timeout 15 --max-time 120 \
        -o /tmp/sqlite.tar.gz \
        "https://sources.buildroot.net/sqlite/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}.tar.gz") && \
    printf '%s  %s\n' "${SQLITE_SHA256}" /tmp/sqlite.tar.gz > /tmp/sqlite.sha256 && \
    sha256sum -c /tmp/sqlite.sha256 && \
    tar -xzf /tmp/sqlite.tar.gz -C /tmp && \
    cd "/tmp/sqlite-autoconf-${SQLITE_AUTOCONF_VERSION}" && \
    CFLAGS="-O2 \
        -DSQLITE_ENABLE_FTS3 \
        -DSQLITE_ENABLE_FTS3_PARENTHESIS \
        -DSQLITE_ENABLE_FTS4 \
        -DSQLITE_ENABLE_FTS5 \
        -DSQLITE_ENABLE_RTREE \
        -DSQLITE_ENABLE_GEOPOLY \
        -DSQLITE_ENABLE_COLUMN_METADATA \
        -DSQLITE_ENABLE_UNLOCK_NOTIFY \
        -DSQLITE_ENABLE_DBSTAT_VTAB \
        -DSQLITE_ENABLE_DBPAGE_VTAB \
        -DSQLITE_ENABLE_MATH_FUNCTIONS \
        -DSQLITE_ENABLE_PREUPDATE_HOOK \
        -DSQLITE_ENABLE_SESSION \
        -DSQLITE_SECURE_DELETE \
        -DSQLITE_THREADSAFE=1 \
        -DSQLITE_MAX_VARIABLE_NUMBER=250000" \
        ./configure --prefix=/opt/sqlite-fixed --disable-static && \
    make -j"$(nproc)" && \
    make install

# ──────────────────────────────────────────────────────────────────────
# K-R4 闸门 stage:抽 node:22 LTS(bookworm-slim,glibc 2.36 兼 trixie 运行期)供 web build
# ──────────────────────────────────────────────────────────────────────
# hermes_cli/web_dist/ .gitignore(仓库无预建),cmd_dashboard 无 --skip-build 启动期 build 会 timeout(HF 无 npm/network)。
# base bake 期 cd web && npm run build 预建 hermes_cli/web_dist/(vite 输出,见 vite.config.ts outDir),
# 镜像层固化,start.sh 跑时设 HERMES_WEB_DIST 指向 → web_server.py:135 直读预建 dist。
# node 仅 build 期需(运行期 SPA 是静态 JS,无需 node)。COPY node/npm/corepack 三件入 builder stage。
FROM node:22-bookworm-slim AS node_source

# ──────────────────────────────────────────────────────────────────────
# 运行期主 stage:python:3.11-slim + 自编 libsqlite3 + base 依赖 + hermes-agent 内核 + web_dist 预建
# ──────────────────────────────────────────────────────────────────────
FROM python:3.11-slim

# ── 优先自编 SQLite 3.53.4(K-R6,在上游 python:3.11-slim 系统 libsqlite 之上覆盖)──
COPY --from=sqlite_build /opt/sqlite-fixed/lib/libsqlite3.so.3.53.4 /usr/local/lib/
RUN ln -sf libsqlite3.so.3.53.4 /usr/local/lib/libsqlite3.so.0 && \
    ln -sf libsqlite3.so.3.53.4 /usr/local/lib/libsqlite3.so && \
    printf '/usr/local/lib\n' > /etc/ld.so.conf.d/000-sqlite-fixed.conf && \
    ldconfig && \
    python3 -c "import sqlite3, sys; \
v = sqlite3.sqlite_version_info; \
sys.exit(f'linked SQLite {sqlite3.sqlite_version} still has the WAL-reset bug') if v < (3, 51, 3) else None; \
db = sqlite3.connect(':memory:'); \
db.execute(\"CREATE VIRTUAL TABLE docs USING fts5(content, tokenize='trigram')\"); \
db.execute(\"INSERT INTO docs VALUES ('hermes')\"); \
sys.exit('SQLite FTS5 trigram self-test failed') if db.execute(\"SELECT count(*) FROM docs WHERE docs MATCH 'erm'\").fetchone()[0] != 1 else None; \
db.close()"

# ── apt 段(必 root,在 USER user 前)─────────────────────────────────
# ca-certificates/curl: litestream 下载 + omniroute 实测;
# sqlite3: state.db 调试;git: clone hermes-agent;base: nemo-relay 等可能源码编译兜底;
# jq: 日志/JSON 解析;ripgrep: hermes-agent 搜索(无则降级 grep,可选保留)
# 注:node/npm 不入运行期(Web build 在 builder stage 跑完,产物 COPY 进运行期,运行期不需 node)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl jq sqlite3 git ripgrep build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── litestream v0.5.15(root 段,监 state.db WAL→R2 不改源)─────────────
# ★2026-08-05 A 方案后 litestream 全段弃(state.db 移 /opt/data 本地盘,无旁路进程干扰 WAL;
#   start.sh 已删 restore/replicate 全段 + 删 scripts/litestream.yml 孤儿)。此 RUN 留作
#   base 历史,下次 rebuild base 顺手删(当前不要求 rebuild;litestream 二进制在镜像内未引不跑,无害)。
# 资产名:litestream-0.5.15-linux-x86_64.tar.gz(无 v 前缀 + x86_64;勿用 vfs-amd64 那是 .so 扩展)
# 解出 litestream CLI 二进制放 /usr/local/bin
RUN curl -fsSL https://github.com/benbjohnson/litestream/releases/download/v0.5.15/litestream-0.5.15-linux-x86_64.tar.gz \
    | tar -xz -C /usr/local/bin litestream

# ── uv(root 段,装系统 uv 供 hermes-agent editable install)────────────
# astral.sh install.sh 默认装 /root/.local/bin,显式挪 /usr/local/bin 供所有 user 用
RUN curl -LsSf https://astral.sh/uv/install.sh | sh \
    && cp /root/.local/bin/uv /usr/local/bin/uv \
    && /usr/local/bin/uv --version

# ── UID 1000 与 HF 一致(HF 容器以 user ID 1000 跑)──────────────────
RUN useradd -m -u 1000 user
# ── /opt/data:state.db 本地盘落地(A 方案治 malformed,2026-08-05)──────
# start.sh 以 user(UID 1000)跑,/opt 默认 root 拥有 user 无写权 → mkdir /opt/data failed。
# 两实战项目(HermesFace/HuggingMes)稳跑 /opt/data 本地 SQLite→0 malformed,靠此预建+chown。
# A 方案 = HERMES_HOME 移出 /data bucket FUSE 进 /opt/data 本地盘消 litestream 旁路并发雷根因。
# 预建 + chown user → start.sh `mkdir -p /opt/data` 不报 failed + subprocess HOME=${HERMES_HOME}/home 可写。
RUN mkdir -p /opt/data && chown user:user /opt/data

# ── base Python 依赖(四 Space 超集,一并装进系统 site-packages)──────
# requirements-base.txt 含四 Space 共用 + langgraph 那套 + huggingface_hub
COPY requirements-base.txt /tmp/requirements-base.txt
RUN pip install --no-cache-dir -r /tmp/requirements-base.txt

# ── hermes-agent 内核(NousResearch Hermes Agent,核心子集)──────────
# pin tag 不 pin main(防 break;升级改 tag + rebuild)
# clone 到 /opt/hermes-agent(系统级只读供 import,root 拥有,user 只读 import 即可)
# editable --system 安装:egg-link 写进系统 site-packages 指向源码,任何 user 能 import run_agent
ARG HERMES_AGENT_TAG=v2026.7.30
RUN git clone --depth 1 --branch ${HERMES_AGENT_TAG} \
        https://github.com/NousResearch/hermes-agent.git /opt/hermes-agent \
    && uv pip install --system --no-cache-dir -e /opt/hermes-agent --no-deps

# ──────────────────────────────────────────────────────────────────────
# K-R5-2 闸门(2026-08-02 二轮修正,与原生 BasicAuthProvider 对齐):CORS 改源固化
# ──────────────────────────────────────────────────────────────────────
# 一轮(2026-08-02):沿用 HermesFace/HuggingMes 旧 `--insecure` 思路,改源三锚点
# 把 auth gate 全关(should_require_auth→False + auth_middleware bypass)。臆断
# "HF sandbox 已隔离" = 错。hermes 原生 BasicAuthProvider(plugins/dashboard_auth/basic/,
# kind: backend,bundled 自动加载)需 gate 开(`should_require_auth` 非 loopback 返 True
# → `auth_required=True`)才接管 /login 密码表单。关 gate = basic 永不接管 = dashboard
# 公网裸跑 = 与用户"后台自动加密码"需求向背。且 June 2026 hardening 后公网扫描者能直访,
# HF sandbox 非纯隔离,密码闸门必要。
#
# 二轮(本版,与原生对齐):只留 CORS 改源。auth gate + auth_middleware 回原生,basic provider 接管鉴权。
#   激活 = config.yaml + env:`HERMES_DASHBOARD_BASIC_AUTH_{USERNAME,PASSWORD,SECRET}`。
#     - 仅 username + password(password_hash)非空 → basic plugin requires_env 通过 → 注册
#     - secret 须设固定值(默认随机重启失效 session 隐患,设固定让 session 跨重启)
#   - 缺 env → list_providers() 空 → gate `SystemExit("Refusing to bind...")` fail-closed 拒起
#   - 配齐 → gate 通过 → /login 密码表单(scrypt 哈希 + HMAC stateless cookie,无 OAuth/IDP/DB)
#
# 仅改一处 CORS(web_server.py 行 345,v0.19.1 + main 845031a grep count=1 双证,行号一致零漂):
#   allow_origin_regex(限 localhost)→ allow_origins=["*"]。
#   解 HF iframe embed — sonoke-h.hf.space 在 huggingface.co iframe 内渲染,SPA fetch JS/CSS/WS
#   跨域回 sonoke-h.hf.space/api/*,默认 CORS regex 拒 → 换 allow_origins=["*"] 放行所有域
#   (HTTP fetch 层;credentials 默认 False)。 HF 无 X-Frame-Options/CSP frame-ancestors 头注入
#   (v0.19.1+main grep -in 0 命中双证),iframe embed 仅靠 CORS 够。
#   鉴权走 BasicAuthProvider cookie(HMAC-sig),非 CORS credential — allow_origins=["*"] 与 cookie
#   鉴权无冲突(CORS preflight 不挡 SameSite cookie 流)。
#
# 注:HERMES_AGENT_TAG 仍 pin v2026.7.30(v0.19.1,470cf66)。main 845031a(8/2)web_server.py 三锚点
#   行号全一致零漂移(已核),且 main 仅多 1 chore commit,无功能改动。保 tag 不动防 break,升级改 tag+rebuild。
#
# 施工:独立脚本 docker/patch_web_server.py(只 1 锚点;避 shell 行续转义 + Python 单行分号地雷;
#   脚本多行 + 函数,py_compile 可验;锚漂即 build 期 AssertionError 拦建,跨升级稳健)。
COPY patch_web_server.py /tmp/patch_web_server.py
RUN cd /opt/hermes-agent && python3 /tmp/patch_web_server.py && rm -f /tmp/patch_web_server.py

# ── 预装 anthropic SDK:消 hermes-agent 运行时 lazy_deps 懒装风控(决定1.6)──
# 用 [anthropic] extras pin 0.87.0(对齐 pyproject extras,CVE 修正);不裸装最新防漂
RUN pip install --no-cache-dir "anthropic==0.87.0"

# ── K-R4 闸门:base bake 期 prebuild hermes_cli/web_dist/ (dashboard SPA)──
# 用 builder stage 抽来的 node/npm/corepack 跑 `npm install --workspace web && npm run build -w web`。
# 抄上游 Dockerfile:176-179,196,272(减法:只 web build,不装 playwright/photon/matrix,不跑 ui-tui build)。
# web/package.json deps 含 @hermes/shared(file:../apps/shared)→ 须 COPY web/package.json + apps/shared/。
# build 产物 hermes_cli/web_dist/(vite outDir,见 vite.config.ts)固化入镜像层,运行期 start.sh --skip-build 直读。
# 注:此段在 clone 后(repo web/ 已在 /opt/hermes-agent/web),build 跑在 /opt/hermes-agent 内,
#      产物落 /opt/hermes-agent/hermes_cli/web_dist/(已 git clone 进来),不动运行期 user 改动。
COPY --from=node_source /usr/local/bin/node /usr/local/bin/node
COPY --from=node_source /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
COPY --from=node_source /usr/local/lib/node_modules/corepack /usr/local/lib/node_modules/corepack
RUN ln -sf /usr/local/lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm && \
    ln -sf /usr/local/lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx && \
    ln -sf /usr/local/lib/node_modules/corepack/dist/corepack.js /usr/local/bin/corepack
# 跑 web build:在 /opt/hermes-agent 内 npm install --workspace web + npm run build -w web
RUN cd /opt/hermes-agent && \
    npm install --workspace web --no-audit --fetch-retries=5 && \
    npm run build -w web && \
    npm cache clean --force && \
    test -f /opt/hermes-agent/hermes_cli/web_dist/index.html && \
    echo "[base] web_dist prebuild OK -> /opt/hermes-agent/hermes_cli/web_dist/"

# ── K-R8 闸门:base bake 期 prebuild ui-tui/dist/entry.js (dashboard embedded-chat TUI)──
# hermes-agent dashboard(pid 命 `hermes dashboard --port 7860`)除 web SPA(HERMES_WEB_DIST)外,
# 还内嵌一个 TUI聊天(/api/pty embedded chat,React+ink 终端 UI),经 `_make_tui_argv` (main.py:1932)
# 起 `node --expose-gc <dist>/entry.js` 运行。
#
# `_make_tui_argv` 两条 prebuilt fast path:
#   1.HERMES_TUI_DIR 设且该目录有 dist/entry.js → 直接 node 启(main.py:1978-1983)
#   2._find_bundled_tui() 内置 bundle → 同(main.py:1987-1990)
# 两路全废(我镜像未设 ENV + 未 prebuild)→ 落 main.py:1993 normal flow,_tui_need_npm_install()
# 返 True → runtime `npm install` 死循环(上游 Dockerfile:364-374 亲证:root package-lock 描述全
# monorepo workspace[apps/*,ui-tui,ui-tui/packages/*,web,tests-js],但镜像只装 root/web/ui-tui,
# apps/*desktop 不装 → 永不收敛 + 并发 ENOTEMPTY → TUI "[session ended]" 502/dash tab 死)。
#
# 解 = 抄上游 Dockerfile:273-276 + L377:build 期 prebuild ui-tui/dist/entry.js + 设
# HERMES_TUI_DIR 指向 → `_tui_need_npm_install` (main.py:1693) `entry.is_file() and not lock.is_file()`
# 即 prebuilt-bundle 模式返 False → 跳 runtime install。`@hermes/ink` 是 ui-tui/packages/hermes-ink 子
# workspace,`--workspace ui-tui` 含其 hoisted deps + devDeps(esbuild/typescript/babel build toolchain)。
#
# build.mjs 头注自证:"dist/entry.js,self-contained,no runtime node_modules needed" — esbuild 单文件
# 打包 src/entry.tsx,运行期 node 直跑不需 node_modules,安全固化入镜像层。
# 注:esbuild/typescript 等 build toolchain 在 devDependencies,故 install 不加 --omit=dev,
#      避 NODE_ENV/inherit 致 omit=dev 静默跳 build deps build 崩(见 main.py:2040 注)。
# 先 `npm install --workspace ui-tui`(装 hoist deps+devDeps)→ 再 `npm run build -w ui-tui`
# (build.mjs 出 dist/entry.js)→ 验产物 → clean。
RUN cd /opt/hermes-agent && \
    npm install --workspace ui-tui --include=dev --no-audit --fetch-retries=5 && \
    npm run build -w ui-tui && \
    npm cache clean --force && \
    test -f /opt/hermes-agent/ui-tui/dist/entry.js && \
    echo "[base] ui-tui/dist/entry.js prebuild OK -> /opt/hermes-agent/ui-tui/dist/"

# ── 切非 root ────────────────────────────────────────────────────────
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    # hermes-agent state.db 唯一重定向开关(逻辑层 start.sh 可覆盖,固化默认)
    # A 方案(2026-08-05):指 /opt/data 本地盘非 /data bucket FUSE,治 malformed 根因。
    # start.sh L92 `${HERMES_HOME:-/opt/data/.hermes}` 走 ${:-} 表达式仅在 VAR 空/未设时用 default,
    # 此 ENV 设非空 → start.sh 用此值而非 default。故 ENV 必须指 /opt/data/.hermes,
    # 否则 start.sh default 永不生效(VAR 恒非空)。HF Secrets 若设 HERMES_HOME 覆盖此 ENV,
    # 用户须删 Secrets 让此 ENV 兜底(见 memory nexus-hermes-statedb-malformed-fix)。
    HERMES_HOME=/opt/data/.hermes \
    # 内核源码路径(逻辑层 import run_agent 用,只读,无需 user 写)
    HERMES_AGENT_DIR=/opt/hermes-agent \
    # K-R4:指向 bake 期 prebuild 的 dashboard SPA dist(web_server.py:135 读此 env)
    HERMES_WEB_DIST=/opt/hermes-agent/hermes_cli/web_dist \
    # K-R8:指向 bake 期 prebuild 的 TUI embedded-chat bundle(main.py:1961 _make_tui_argv 读此 env,
    #       存 dist/entry.js → L1978 fast path 起 `node --expose-gc dist/entry.js` 跳 runtime npm install)
    HERMES_TUI_DIR=/opt/hermes-agent/ui-tui

WORKDIR $HOME/app

# 容器启动时逻辑层从 /data 挂载;此处 WORKDIR 路径对齐 HF,工作目录虽空但路径一致
# 各 Space 的 start.sh 自身 COPY 进各自 Dockerfile(此 base 仅提供依赖与内核)
