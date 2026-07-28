# Nexus GHCR base 镜像 —— 依赖层(绝对静态化的"镜像层")
# 把四 Space 共用 Python 依赖打包进此镜像,推 GHCR ghcr.io/<owner>/nexus-base:stable
# 各 Space Dockerfile ARG BASE_IMAGE=ghcr.io/<owner>/nexus-base:stable + FROM ${BASE_IMAGE} 引用
# 升级依赖: 改 docker/requirements-base.txt → 本地 build 覆盖 :stable → HF repo 改 README 一字符 git push
# 此镜像不含任何 Nexus 业务代码(代码进 HF Storage Bucket rw /data 挂载)
#
# 构建命令(本地):
#   docker build -t ghcr.io/<owner>/nexus-base:stable -f docker/nexus-base.Dockerfile docker/
#   docker push ghcr.io/<owner>/nexus-base:stable
# 建议同时打版本标签作回退锚点:
#   docker tag ghcr.io/<owner>/nexus-base:stable ghcr.io/<owner>/nexus-base:vN
#   docker push ghcr.io/<owner>/nexus-base:vN

FROM python:3.11-slim

# UID 1000 与 HF 一致(HF 容器以 user ID 1000 跑)
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

# 依赖单独拷利用缓存
COPY --chown=user requirements-base.txt .
RUN pip install --no-cache-dir -r requirements-base.txt

# 容器启动时逻辑层从 /data 挂载;此处 WORKDIR 路径对齐 HF,工作目录虽空但路径一致
# 各 Space 的 start.sh 自身 COPY 进各自 Dockerfile(此 base 仅提供依赖与用户环境)
