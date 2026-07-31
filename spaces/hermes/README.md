---
title: Hermes (Nexus 主控)
emoji: 🧠
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: true
short_description: Hermes Agent (NousResearch) on omniroute — Nexus 主控内核
tags:
  - agents
  - llm
  - router
  - nexus
  - hermes-agent
---

# Hermes — Nexus 主控内核

Nexus 唯一入口。永续改造后换装 **NousResearch Hermes Agent** 作内核(github.com/NousResearch/hermes-agent):
- 不再是自建关键词 route 分发——agent loop 默认调 omniroute 推理,按 prompt 语义智能决策调下游
- 三个 custom tool(注册为 hermes plugin,toolset=`nexus`):
  - `nexus_call_claude` —— 调 claude-code Space(实现/重构/调试 lane)
  - `nexus_call_codex` —— 调 codex Space(补全/片段 lane)
  - `nexus_route_langgraph` —— 调 langgraph Space(规划/多步工作流 lane)
  - 三 tool 桥到现役 `libs/shared/gateway.call_space`,结果回写 agent 会话记忆
- `force_space=` 兜底路:显式指派时跳 agent 直调下游(向后兼容老 dashboard + 指派 lane)

主控位不变:仍收 `/run`、写 Supabase `task_logs`/`agent_states`、Gradio Dashboard 三 Tab(任务路由/文件管理 R2/系统状态)、保活/持久化后台守护。

## 永续架构(三条铁律)

1. **逻辑层进 HF Storage Bucket `/data` rw 挂载** —— 改逻辑只推 Bucket + Restart,不触 HF rebuild 付费墙
2. **Dockerfile 永续墓碑** —— `ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable` + 仅 `COPY start.sh`(逻辑层在镜像外)
3. **依赖进 GHCR base 镜像** —— hermes-agent + 四 Space Python 蔓延依赖 + litestream 全在 base,逻辑层零 `pip install`

state.db 经 litestream WAL→R2 复制(铁律 L8,sync 10s)续命;Supabase 四表经 `persist_to_r2.py` 快照(灾备,与 litestream 互补)。

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 存活探测(保活/唤醒) |
| POST | `/run`   | 提交任务(agent 智能决策;带 `force_space` 直指下游) |
| POST | `/enqueue` | 异步入队(带 `Idempotency-Key`) |
| POST | `/dequeue` | 认领队首任务 |
| GET  | `/state/{thread_id}` | 查任务 phase 摘要(Supabase 查询面) |
| GET  | `/task/{thread_id}` | 查 task_queue 一行状态 |

## 路由(`force_space` 兜底 lane,与 agent 主路径并列)

| force_space | 目标 Space | 触发 |
|------------|-----------|------|
| (空,默认) | —— | agent loop 自推理 + 按语义智能调三 nexus_* tool |
| `claude` | claude-code `/run` | 显式指派编码 lane |
| `codex` | codex `/complete` | 显式指派补全 lane |
| `langgraph` | langgraph `/execute` | 显式指派编排 lane |

## Secrets

见 `.env.example` + `docs/new/部署/hermes-agent-换装方案.md`。Hermes 需:
- 全套 R2/Supabase + 下游 URL + `NEXUS_API_KEY` + `GATEWAY_URL`(现役,路 B 调下游用)
- `ANTHROPIC_BASE_URL` + `ANTHROPIC_API_KEY` + `HERMES_MODEL`(omniroute 接入,agent 推理用)

真值经 HF Space Secrets 注入,不入 git。
