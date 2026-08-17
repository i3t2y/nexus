---
title: mem0-server
emoji: 🧠
colorFrom: blue
colorTo: purple
sdk: docker
app_port: 7860
pinned: false
---

# mem0 Self-Hosted Memory Server

统一云端记忆层, 供 Hermes / Claude Code / Codex / 任意 agent 通过 HTTP API 调用。

## 架构

```
agent (X-API-Key) → HF Space (7860) → Neon Postgres (pgvector)
                     ↑ /health
              cron-job.org (每4min保活)
```

## 文件

| 文件 | 说明 |
|---|---|
| `Dockerfile` | python:3.12-slim + git clone mem0 server/ + port 7860 |
| `start.sh` | alembic upgrade + 注入 /health + uvicorn 7860 |
| `docs/STATUS.md` | 部署状态和进度 (agent 衔接用) |
| `docs/SECRETS.md` | Secrets 清单 (不含值) |
| `docs/DEPLOY.md` | 完整 5 步部署指南 |
| `.github/workflows/deploy-hf.yml` | GitHub Actions → HF Space |

## 部署

1. Neon 建 project (AWS us-east-1) + CREATE EXTENSION vector
2. HF Space 创建 (Docker SDK, port 7860) + 配 12 个 Secrets
3. push 到 GitHub → Actions 自动部署
4. cron-job.org 每4min ping /health
5. 详见 `docs/DEPLOY.md`

## 环境变量

详见 `docs/SECRETS.md`
