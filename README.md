# Nexus

混合 Agent 系统：HF Spaces 计算 + Cloudflare R2 大文件 + Supabase Postgres 结构化状态。
Hermes 主控 + LangGraph 编排 + Claude/Codex 强推理。

> 当前状态：**模板阶段**。代码可直接部署，凭证就位后填 `.env` / Space Secrets 即跑。
> 凭证未真填，不含任何密钥。

## 结构

```
nexus/
├── docs/              # 架构、通信、部署、凭证四篇文档
│   ├── ARCHITECTURE.md
│   ├── COMMUNICATION.md
│   ├── DEPLOYMENT.md
│   └── CREDENTIALS.md
├── libs/              # 跨 Space 共享：storage(R2+Supabase) + gateway(HTTP) + checkpointer
│   ├── storage/
│   └── shared/
├── spaces/            # 4 个 HF Space 模板（各自独立 build context）
│   ├── hermes/        # 主控大脑
│   ├── langgraph/     # 编排（AsyncPostgresSaver）
│   ├── claude-code/   # 强推理
│   └── codex/         # 快速编码
├── workers/gateway/   # Cloudflare Worker 统一入口
├── sql/               # Supabase schema + pgvector
├── scripts/sync-spaces.sh  # 把 libs/ 复制进各 Space 目录
└── .env.example
```

## 快速开始

```bash
# 1. 凭证就位后同步共享库到各 Space
bash scripts/sync-spaces.sh

# 2. 起 Supabase：在 SQL Editor 跑 sql/00_schema.sql（需要时再跑 01_pgvector.sql）

# 3. 部署各 Space：把对应 spaces/<name>/ 内容推到各自的 HF Space 仓库
#    Space Secrets 按 docs/CREDENTIALS.md 配置

# 4. 部署 Worker：cd workers/gateway && npx wrangler deploy

# 5. 端到端：curl POST <hermes_url>/run -d '{"prompt":"测试规划"}'
```

## 关键文档

- **先读** [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — 总览 + 付费墙约束
- 通信方案 [`docs/COMMUNICATION.md`](docs/COMMUNICATION.md) — Worker 网关为主、直调为辅
- 部署顺序 [`docs/DEPLOYMENT.md`](docs/DEPLOYMENT.md)
- 凭证清单 [`docs/CREDENTIALS.md`](docs/CREDENTIALS.md)

## ⚠️ 必读约束

HF Docker Space 需付费套餐；免费 Space 会休眠（可外部保活解决）；Space 内持久存储已下线、必须用 R2/Supabase。见 ARCHITECTURE.md「关键约束」。
