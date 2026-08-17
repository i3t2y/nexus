# memlg Space — 三件套之一

## 定位
- **HF Space**: `nmem/memlg` (public, Docker SDK, port 7860)
- **职能**: Mem0 server (记忆层) + LangGraph worker (编排)
- **后端**: Neon Postgres (pgvector, AWS us-east-1)
- **保活**: cron-job.org 每 4min ping `/health`

## 三件套
1. Hermes (sonoke/h) — 入口/路由/调度 (云上大脑)
2. memlg (nmem/memlg) — 记忆+编排 (本目录)
3. Neon — 数据持久化

## 文件结构
- `Dockerfile` — 三文件之一 (冻结)
- `README.md` — HF Space frontmatter (冻结)
- `start.sh` — 启动薄引导 (冻结)
- `nworker/` — 逻辑层 (entrypoint.sh + run.py + graph/ + patches/)

## 持久化
- 三文件 → HF Space git repo (不频繁改)
- 逻辑层 → HF Bucket `nmem/logic` (rw 挂载 /data)
- 配置 → HF Secrets (零文件持久化)
- 版本化 → GitHub `i3t2y/nexus` 私库 (本目录)

## 部署链
```
GitHub i3t2y/nexus (版本化真源)
  → Actions (push 触发)
  → hf buckets sync nworker/ → nmem/logic Bucket
  → memlg Space start.sh: hf buckets sync 拉 → /app/worker
```
