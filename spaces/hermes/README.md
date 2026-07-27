---
title: Hermes (Nexus 主控)
emoji: 🧠
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: true
short_description: Nexus 混合 Agent 主控大脑与路由
tags:
  - agents
  - llm
  - router
  - nexus
---

# Hermes — Nexus 主控大脑

Nexus 唯一入口。负责：

1. 接收任务 `/run`
2. 路由决策（发往 langgraph / claude / codex）
3. 调下游 Space，聚合结果
4. 写 Supabase `task_logs` + `agent_states`

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 存活探测（保活/唤醒用） |
| POST | `/run`   | 提交任务，返回 task_id |
| GET  | `/state/{thread_id}` | 查任务状态 |

## 路由规则（初版，可改）

| 任务特征 | 目标 Space |
|---------|-----------|
| 含"规划/多步/工作流/依赖" | langgraph |
| 含"实现/重构/调试 + 复杂" | claude |
| 含"补全/快速/片段" | codex |
| 默认 | langgraph |

实际按 prompt 关键词启发式 + 可后续接模型分类。

## Secrets

见 `docs/CREDENTIALS.md`。Hermes 需全套 R2/Supabase + 下游 URL + `NEXUS_API_KEY` + `GATEWAY_URL`。
