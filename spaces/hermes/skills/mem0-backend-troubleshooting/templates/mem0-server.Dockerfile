# mem0 server HF Space Dockerfile — Three-File永続 pattern
# Copies mem0 official server/ via git sparse-checkout (zero source changes).
# All config via HF Secrets env vars. Port 7860 (HF only exposes 7860).
FROM python:3.12-slim

WORKDIR /app

# System deps: git (clone), libpq (psycopg), curl (healthcheck)
RUN apt-get update && \
    apt-get install -y --no-install-recommends git libpq-dev curl && \
    apt-get clean && rm -rf /var/lib/apt/lists/*

# Pull mem0 server/ only (sparse-checkout, depth=1 = minimal)
RUN git clone --depth 1 --filter=blob:none --sparse https://github.com/mem0ai/mem0.git /tmp/mem0 && \
    cd /tmp/mem0 && git sparse-checkout set server && \
    cp -r /tmp/mem0/server/* /app/ && \
    cp -r /tmp/mem0/server/.env.example /app/.env.example && \
    rm -rf /tmp/mem0

RUN pip install --no-cache-dir -r requirements.txt

COPY start.sh /app/start.sh
RUN chmod +x /app/start.sh

EXPOSE 7860
ENV PYTHONUNBUFFERED=1

CMD ["/app/start.sh"]
