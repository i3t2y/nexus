# Hermes (Nexus 主控) Docker 镜像 —— 永续墓碑(绝对静态化)
# 设计:此文件首切后永不再动。依赖进 GHCR base 镜像 ghcr.io/<owner>/nexus-base:stable;
#       业务逻辑(app/scripts/libs)进 HF Storage Bucket rw /data 挂载,不进镜像。
#       升级依赖 = 本地 build 新 base 推 GHCR 覆盖 :stable + 改 README 一字符 git push(用户手动)。
#       改逻辑    = sync-logic-bucket.sh + Space Restart(用缓存镜像,永不 git push)。
#
# ARG 占位符写法(k3 兼容方案):
#   - 默认值 ghcr.io/<owner>/nexus-base:stable 兜底 HF build-arg 未注入情况(退默认值仍可构建)
#   - 浮动标签 :stable 是实际切换机制(日常升级行为,不需动 ARG)
#   - 即便 HF 对 FROM 行不注入 build-arg,FROM 退默认值,镜像照常拉
#   <owner> 部署时替换为 GHCR 用户名(可与 HF owner 不同)
ARG BASE_IMAGE=ghcr.io/<owner>/nexus-base:stable
FROM ${BASE_IMAGE}

# 运行时:逻辑层从 /data 挂载点解析(PYTHONPATH 顶层包 + uvicorn --app-dir 双重定位,
# 由 start.sh 设定;此处 ENV 作默认,与 start.sh 一致)
ENV PYTHONPATH=/data/libs \
    HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

# 镜像仅含 start.sh 引导(逻辑在 Bucket,依赖在 base 镜像)
COPY --chown=user start.sh ./start.sh
RUN chmod +x start.sh

EXPOSE 7860
CMD ["bash", "start.sh"]
