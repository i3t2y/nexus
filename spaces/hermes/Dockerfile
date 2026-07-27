# Hermes (Nexus 主控) Docker 镜像
# 构建前提：libs/ 已通过 scripts/sync-spaces.sh 复制进本 Space 目录
FROM python:3.11-slim

RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH \
    PYTHONUNBUFFERED=1

WORKDIR $HOME/app

# 依赖单独拷利用缓存
COPY --chown=user requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 应用 + 共享库（构建前已同步到本目录）
COPY --chown=user app ./app
COPY --chown=user libs ./libs
ENV PYTHONPATH=/home/user/app/libs

EXPOSE 7860
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "7860"]
