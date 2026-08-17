FROM python:3.12-slim

WORKDIR /app

# 系统依赖: git (clone mem0), libpq (psycopg), curl (healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git libpq-dev curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# 拉 mem0 server 代码 (只拉 server/ 目录, depth=1 最小体积)
RUN git clone --depth 1 --filter=blob:none --sparse https://github.com/mem0ai/mem0.git /tmp/mem0 && \
    cd /tmp/mem0 && git sparse-checkout set server && \
    cp -r /tmp/mem0/server/* /app/ && \
    cp -r /tmp/mem0/server/.env.example /app/.env.example && \
    rm -rf /tmp/mem0

# 安装 mem0 server 依赖 + langgraph + huggingface_hub
RUN pip install --no-cache-dir -r requirements.txt && \
    pip install --no-cache-dir langgraph huggingface_hub

# 复制启动脚本
COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 7860

ENV PYTHONUNBUFFERED=1

# 探活契约: /health 由 patches/40_health_worker.py 注入 (SELECT 1 + worker status)
# HF Space 自带 healthcheck 读 7860 端口; 此处补充应用层探活
HEALTHCHECK --interval=30s --timeout=10s --start-period=120s --retries=3 \
  CMD curl -sf http://127.0.0.1:7860/health || exit 1

CMD ["/app/start.sh"]
