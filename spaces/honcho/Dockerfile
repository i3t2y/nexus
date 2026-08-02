# Nexus Honcho 自建 HF Docker Space(memory provider 补层,2026-08-03)
# 用途:给 hermes 接管的用户记忆补 Honcho(user-centric memory + social cognition)。
# 叠加非替换,不动 nexus 现有 4 组件架构。
#
# 形态:minimal Python launcher shim,不 fork honcho 源码。
#   - build 期 git clone honcho v3.0.11(plastic-labs/honcho,AGPL-3.0-only)到 /app
#   - uv sync --frozen --no-group dev 装 honcho 依赖进 .venv
#   - launcher.py 运行时替换 app.router.lifespan_context(0 行 honcho 源码 diff)
#   - api + deriver 两逻辑压单 uvicorn 进程(HF Space 单进程硬约束)
#   - deriver(queue_manager.main)经 asyncio.create_task 同 loop polling
#
# 不采 GHCR-base+Bucket 永续路(honcho 外部仓低频变,逻辑层极薄,Bucket 过度工程)。
# 升级 honcho:改下方 ARG HONCHO_TAG 重 build。
#
# 实证源:/tmp/honcho_check/honcho v3.0.11(commit 14538cfc, 2026-08-03 核)。
# shim 对齐该版源码结构(main.py lifespan/queue_manager.main/init_db/config.py env 前缀)。

FROM python:3.11-slim-bookworm

# ── honcho 版本 pin(升级改此重 build)──
ARG HONCHO_TAG=v3.0.11
ARG HONCHO_REPO=https://github.com/plastic-labs/honcho.git

# uv 版本 pin(对齐 honcho 官方 Dockerfile v0.9.24)
ARG UV_VERSION=0.9.24

ENV PYTHONUNBUFFERED=1
ENV PYTHONDONTWRITEBYTECODE=1
# honcho .python-version=3.11 -> uv 装项目用它;FROM 3.11 直对齐省 uv 拉 python
ENV UV_COMPILE_BYTECODE=1
ENV UV_LINK_MODE=copy

WORKDIR /app

# ── 系统依赖(root)──
# apt:git(已用 clone 可省但保 build clone 兜底)+ca-certificates(curl/git TLS)+
#     build-essential(lancedb/pyarrow/scikit-learn 源编译 fallback;lock 有 prebuilt wheel 但保险)
# curl:留作 debug/probe 用
RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        ca-certificates \
        curl \
        build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── uv 装(对齐 honcho 官方 Dockerfile)──
# 注:COPY --from 不支持 ARG 插值(classic builder),故 --from 用字面版本号;
#     UV_VERSION ARG 仅作版本可读性标记,改 uv 须同步改下行 --from 字面值。
COPY --from=ghcr.io/astral-sh/uv:0.9.24 /uv /bin/uv

# ── clone honcho(浅,tag pin)──
RUN git clone --depth 1 --branch ${HONCHO_TAG} ${HONCHO_REPO} /app \
    && rm -rf /app/.git

# ── 装 honcho 依赖(lockfile frozen)──
# --frozen:用 uv.lock 锁版本不更新;--no-group dev:跳测试依赖
# 注:HF build 无 buildkit cache mount,用普通 RUN(牺牲缓存层效率换兼容)
RUN uv sync --frozen --no-group dev

# .venv 进 PATH
ENV PATH="/app/.venv/bin:$PATH"
ENV HOME=/app

# ── launcher.py shim(prog+deriver 压一进程)──
COPY --chown=root:root launcher.py /app/launcher.py

# ── 非 root user(对齐 honcho 官方)──
RUN addgroup --system app && adduser --system --group app \
    && chown -R app:app /app

USER app

# HF Space 公网端口(非官方 8000)
EXPOSE 7860

# launcher:provision_db(init_db migrate Neon)+ lifespan 嵌 deriver task + uvicorn 7860
CMD ["python", "launcher.py"]
