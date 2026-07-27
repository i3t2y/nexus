---
title: Nexus Codex (快速编码)
emoji: ⚡
colorFrom: yellow
colorTo: orange
sdk: docker
app_port: 7860
pinned: false
short_description: 快速编码与补全执行单元
tags:
  - codex
  - codegen
  - fast
  - nexus
---

# Codex Space

接收 Hermes 转发的快速编码/补全任务，对接 OpenAI Codex 兼容接口执行。

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 探测 |
| POST | `/complete` | body `{thread_id, prompt}` |

## Secrets

`OPENAI_API_KEY`（或自建 Codex endpoint 的 base）+ R2/Supabase 套 + `NEXUS_API_KEY`。
