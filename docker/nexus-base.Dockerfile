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

FROM python:3.11-slim

# ── apt 段(必 root,在 USER user 前)─────────────────────────────────
# ca-certificates/curl: litestream 下载 + omniroute 实测;
# sqlite3: state.db 调试;git: clone hermes-agent;base: nemo-relay 等可能源码编译兜底;
# jq: 日志/JSON 解析;ripgrep: hermes-agent 搜索(无则降级 grep,可选保留)
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates curl jq sqlite3 git ripgrep build-essential \
    && rm -rf /var/lib/apt/lists/*

# ── litestream v0.5.15(root 段,监 state.db WAL→R2 不改源)─────────────
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

# ── 预装 anthropic SDK:消 hermes-agent 运行时 lazy_deps 懒装风控(决定1.6)──
# 用 [anthropic] extras pin 0.87.0(对齐 pyproject extras,CVE 修正);不裸装最新防漂
RUN pip install --no-cache-dir "anthropic==0.87.0"

# ── 切非 root ────────────────────────────────────────────────────────
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1 \
    # hermes-agent state.db 唯一重定向开关(逻辑层 start.sh 可覆盖,固化默认)
    HERMES_HOME=/data/.hermes \
    # 内核源码路径(逻辑层 import run_agent 用,只读,无需 user 写)
    HERMES_AGENT_DIR=/opt/hermes-agent

WORKDIR $HOME/app

# 容器启动时逻辑层从 /data 挂载;此处 WORKDIR 路径对齐 HF,工作目录虽空但路径一致
# 各 Space 的 start.sh 自身 COPY 进各自 Dockerfile(此 base 仅提供依赖与内核)
