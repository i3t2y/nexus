---
title: Nexus LangGraph (编排)
emoji: 🔀
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
short_description: 复杂工作流编排，AsyncPostgresSaver Checkpoint + R2 blob
tags:
  - langgraph
  - workflow
  - nexus
---

# LangGraph Space

接收 Hermes 转发的编排类任务，跑 LangGraph 状态机：

- Checkpoint → Supabase Postgres（`AsyncPostgresSaver`）
- 大 blob → R2

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 探测 |
| POST | `/execute` | 执行编排，body `{thread_id, prompt}` |

## Secrets

R2 / Supabase 全套 + `SUPABASE_DB_URI`（PostgresSaver 直连）+ `NEXUS_API_KEY`。
