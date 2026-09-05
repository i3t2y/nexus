---
title: Nexus Claude (强推理)
emoji: ✦
colorFrom: purple
colorTo: pink
sdk: docker
app_port: 7860
pinned: false
short_description: 复杂推理与代码生成执行单元
tags:
  - claude
  - reasoning
  - codegen
  - nexus
---

# Claude Code Space

接收 Hermes 转发的复杂推理/代码任务，对接 Claude API 执行。

## 端点

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/health` | 探测 |
| POST | `/run`    | body `{thread_id, prompt}` |

## Secrets

`ANTHROPIC_API_KEY` + R2/Supabase 套 + `NEXUS_API_KEY`。
模型默认 `claude-sonnet-5`（Claude 5 族），按 `CLAUDE_MODEL` 覆盖。
