# Nexus 架构总览

> 混合 Agent 系统：HF Spaces 计算 + Cloudflare R2 大文件 + Supabase Postgres 结构化状态。
> 目标：稳定、低成本、低风控。

## 设计原则

1. **计算与存储分离** — HF Space 只当计算单元，所有持久状态走 R2 / Supabase。Space 重启不丢数据。
2. **主控路由** — Hermes 唯一入口，下游 Space 不直接对外。降低风控面。
3. **凭证外置** — 所有密钥通过 Space Secrets / 环境变量注入，不入库不入代码。
4. **模板先行** — 凭证未到位前，全部文件可直接部署，填 `.env` 即跑。

## 组件分布

| Space | 角色 | SDK | 状态 |
|-------|------|-----|------|
| `hermes` | 主控大脑，常驻，路由分发 | Gradio (ZeroGPU) / Docker | 模板 |
| `langgraph` | 复杂工作流编排，含 Checkpointer | Docker | 模板 |
| `claude-code` | 复杂推理、代码生成 | Docker | 模板 |
| `codex` | 快速编码、补全 | Docker | 模板 |

Space 间通过 `https://{owner}-{space}.hf.space` 互调，鉴权用共享 `NEXUS_API_KEY`（见 [通信方案](./COMMUNICATION.md)）。

## 存储分层

```
        ┌──────────────────────────────────────┐
        │            HF Spaces (计算)            │
        │  hermes  langgraph  claude  codex     │
        └───────────────┬──────────────────────┘
                        │  (Secrets 注入凭证)
            ┌───────────┴───────────┐
            ▼                       ▼
   ┌─────────────────┐     ┌──────────────────────┐
   │  Cloudflare R2   │     │  Supabase Postgres    │
   │  大文件/Checkpoint │     │  结构化/状态/Memory   │
   │  /Skills/向量     │     │  + pgvector 向量搜索  │
   └─────────────────┘     └──────────────────────┘
```

| 层 | 用途 | 何时用 |
|----|------|--------|
| **R2** | Checkpoint blob、Skills 备份、向量文件、大对象 | >1MB 或二进制 |
| **Supabase** | agent_states、task_logs、long_memory、调度队列 | 结构化、需查询 |
| **HF Datasets** | 临时缓存、必须放 HF 的文件 | 仅兜底 |

## 数据流（典型任务）

1. 请求到 Hermes `/run`，写 `task_logs`（status=pending）。
2. Hermes 路由决策 → 调下游 Space（带 `thread_id`）。
3. 下游 Space 读 Supabase 取 state，执行，写 R2 存大产物，回传结果。
4. Hermes 收结果 → upsert `agent_states`、更新 `task_logs`（status=done）。
5. LangGraph 长流程用 `AsyncPostgresSaver` 做 Checkpoint（状态进 Postgres，blob 进 R2）。

## ⚠️ 关键约束（部署前必读）

### HF Spaces 付费墙

自 2024 年起，**Docker / Gradio Space 跑 compute 需付费套餐**：
- 个人免费账号可跑最多 **2 个 Gradio Space (ZeroGPU)**，CPU Basic 免费。
- **Docker SDK Space 原则上需 PRO / Team 付费套餐创建。**
- 本方案 4 个 Space 全用 Docker，**部署时需确认账号开通了对应套餐**，否则 langgraph/claude/codex 三个 Docker Space 无法创建。Hermes 可改 Gradio + ZeroGPU 跑在免费层。

### 免费 Space 会休眠

免费硬件（CPU Basic / ZeroGPU）闲置后会"睡"。首调冷启动慢（数十秒）。
- 常驻需求 → Hermes 用 keep-alive 或升级付费。
- 休眠具体时长随政策变，以 [官方文档](https://huggingface.co/docs/hub/spaces-overview) 为准，不硬编码进代码。

### 网络

HF Space 出站仅 80 / 443 / 8080。R2(S3 API)、Supabase(HTTPS) 均走 443，无碍。

### 持久化

Space 内 `/data` 持久存储**已下线**。跨重启持久必须用 R2 / Supabase，不依赖本地磁盘。

## 技术栈版本锚点

| 组件 | 库 | 安装 |
|------|----|----|
| LangGraph Checkpoint | `langgraph-checkpoint-postgres` | `pip install langgraph-checkpoint-postgres` |
| Supabase | `supabase` | `pip install supabase` |
| R2 (S3 兼容) | `boto3` | `pip install boto3` |
| HTTP 调用 | `httpx` | `pip install httpx` |

API 已对照官方文档（2026-07）：
- `AsyncPostgresSaver.from_conn_string(uri)` + `await checkpointer.setup()`
- `supabase.create_client(url, key, options=None)`，`upsert(json, on_conflict=, ignore_duplicates=)`

## 演进路径

```
阶段0 模板 (当前) → 阶段1 凭证就位单 Space 跑通 → 阶段2 全链路 → 阶段3 保活/监控
```

详见 [DEPLOYMENT.md](./DEPLOYMENT.md)、[CREDENTIALS.md](./CREDENTIALS.md)。
