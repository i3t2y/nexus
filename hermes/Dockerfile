# Hermes (Nexus 主控) Docker 镜像 —— 永续墓碑(绝对静态化)
# 设计:此文件首切后永不再动。依赖进 GHCR base 镜像 ghcr.io/i3t2y/nexus-base:stable;
#       业务逻辑(app/scripts/libs)进 HF Storage Bucket rw /data 挂载,不进镜像。
#       升级依赖 = 本地 build 新 base 推 GHCR 覆盖 :stable + 改 README 一字符 git push(用户手动)。
#       改逻辑    = sync-logic-bucket.sh + Space Restart(用缓存镜像,永不 git push)。
#
# ARG 占位符写法(k3 兼容方案):
#   - 默认值 ghcr.io/i3t2y/nexus-base:stable 兜底 HF build-arg 未注入情况(退默认值仍可构建并拉到真 base)
#   - 浮动标签 :stable 是实际切换机制(日常升级行为,不需动 ARG)
#   - 即便 HF 对 FROM 行不注入 build-arg,FROM 退默认值,镜像照常拉到 GHCR 已推的 :stable
#   - i3t2y = GitHub/GHCR 用户名(镜像名非敏感);若换 owner 改此处默认值
ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable
FROM ${BASE_IMAGE}

# 阶段作用域重声明(Docker 铁律:全局 ARG 被 FROM 划断,FROM 后引用须重声明。
# 不带值=继承全局同名 ARG 值。防御性 + 留调试接口,无副作用)。
ARG BASE_IMAGE

# 转存运行时 ENV:start.sh 可 echo $BASE_IMAGE 打印当前镜像版本(运维排查引用)
ENV BASE_IMAGE=${BASE_IMAGE}

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
