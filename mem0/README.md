# mem0 (hermes 进程内 OSS 模式)

nexus 的向量记忆层。**与已废弃的 memgraph Space (old/memgraph-20260905/) 无任何关联** —— 自 2026-08-23 起 mem0 以 OSS 模式直接运行在 hermes (sonoke/h) 进程内，直连 Neon pgvector。

## 架构

```
hermes (sonoke/h) 进程内, hermes_agent.plugins.memory.mem0
  └─ mode: oss (in-process mem0)
       ├─ vector_store: pgvector → Neon hermes_mem0 表
       │    (embedding_model_dims=2048, hnsw=false — 2048 维超出 HNSW 上限, 勿开)
       ├─ llm: glm-4.7-flash via z.ai (openai 兼容端点)
       └─ embedder: nvidia/nemotron-3-embed-1b via NIM
```

## 配置注入链（清单, 不含密钥）

- `mem0.json` 由 **hermes/scripts/real-start.sh 在启动时动态重建**（从 config + 环境变量）。手改 mem0.json 会在下一次 Space 重启被覆盖 —— 勿手改。
- hermes 环境变量（HF Space Secrets）：
  - `ZAI_API_KEY` — mem0 LLM 提取（glm-4.7-flash, z.ai）
  - `NVIDIA_API_KEY` — embedder（nemotron-3-embed-1b, NIM）
  - Neon 连接串走池化端点（`-pooler` host）
- 三个 key（omn 中转 key / ZAI / NVIDIA）任何一个失效，mem0 相应环节退化但不会拖垮主聊天链。

## 运维要点

- **Neon 冷启动**：mem0 调用若报 connection lost，通常是 Neon scale-to-zero 唤醒延迟，重试一次即可（非故障）。
- **重启后校验**：Space 重启后跑一次 `mem0_search`（随便一个关键词）确认 2048 维表存在且可写。
- **v0.21 起 cron agent 可用 mem0**：监控类 job 新建时开 `continuity=true` + monitor 门控 + mem0 记录运行结论 —— 见 docs/shared/cron-memory-evolution.md。
- **历史**（旧自托管 mem0 server 方案的 fix 链，仅存档用）：docs/memgraph/mem0-server-*.md（已标 ARCHIVED）。

## 文件物

本目录当前只有文档。物理配置真源在两处：
1. `hermes/scripts/real-start.sh`（注入逻辑）
2. HF Space sonoke/h 的 Secrets（密钥值）
