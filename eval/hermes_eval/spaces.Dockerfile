# Hugging Face Docker Space —— hermes /eval 端点
# HF 免费 Docker Space 必须监听 7860（Space 已注入 PORT=7860）。
FROM python:3.11-slim

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# 把 hermes_eval 服务端 + eval 三件套（score.py / eval_cases.json / heldout.json）拷进来
COPY . /app

ENV HERMES_EVAL_PSK="__SET_VIA_SPACE_SECRET__"
ENV HERMES_EVAL_BACKEND="stub"          # 接真实 hermes 后改 real
ENV HERMES_EVAL_DIR="/app"
ENV PORT=7860

EXPOSE 7860
CMD ["python", "-m", "hermes_eval.server"]
