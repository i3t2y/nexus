# Nexus

混合 Agent 系统（生产运行中）：

- **hermes (sonoke/h)** — 云上大脑：Telegram 入口 / 路由 / 调度，nonoke-omn 中转 LLM
- **mem0** — 向量记忆层（hermes 进程内 OSS 模式 → Neon pgvector hermes_mem0 表；见 mem0/README.md）
- **Neon Postgres** — 持久化主路：结构化四表 + mem0 向量
- **Cloudflare R2** — 灾备快照副路（Neon→R2 周期快照）

> 2026-09-05: memgraph 编排 Space 正式废弃（hermes+mem0 OSS 已满足需求），全量移入 other/memgraph-20260905/（含其 deploy workflow，已摘出 workflows/ 不再触发）。

> 当前状态：**生产中**。凭证走 HF Space Secrets，仓库内零密钥。

## 仓库结构

```
nexus/
├── hermes/            # 主控 Space 源码（生产, sonoke/h）
│   ├── space/         #   Dockerfile + README + start.sh → HF Space git
│   ├── app/ libs/ mcp/ skills/   # 逻辑层 → HF Bucket 挂载
│   └── scripts/       #   real-start.sh + 持久化链路脚本
├── mem0/              # mem0 记忆层文档 (hermes 进程内 OSS → Neon pgvector)
├── docs/
│   ├── shared/        #   架构/凭证/cron演进 等跨组件文档 (先读 ARCHITECTURE.md)
│   ├── hermes/        #   hermes Space 状态与部署清单
│   ├── memgraph/      #   mem0 历史运维文档 (已标 ARCHIVED)
│   └── archive/       #   历史方案存档 (legacy/ = 2026-09-05 集中归档, 已废弃)
├── scripts/gateway/   # Cloudflare Worker 统一入口
├── other/               # 早期架构物料 (不部署, 仅存史)
└── scripts/           # Bucket 同步等运维脚本
```

## 快速入口

- **架构总览（含数据流/存储分层）**: [docs/shared/ARCHITECTURE.md](docs/shared/ARCHITECTURE.md)
- **凭证清单**: [docs/shared/CREDENTIALS.md](docs/shared/CREDENTIALS.md)
- **mem0 记忆层**: [mem0/README.md](mem0/README.md)
- **hermes Space 状态**: [docs/hermes/hermes-status.md](docs/hermes/hermes-status.md)
- **cron 演进决策 (v0.21)**: [docs/shared/cron-memory-evolution.md](docs/shared/cron-memory-evolution.md)

## 部署链

```
GitHub i3t2y/nexus (public, Actions 免费)
  → .github/workflows/deploy-*.yml
    → deploy-space:  <x>/space/ → HF Space git (触发 rebuild)
    → deploy-bucket: <x>/bucket/ → HF Bucket sync (不 rebuild)
```

## ⚠️ 关键约束

- HF 免费 Space 会休眠 → 外部 cron 保活 `/health`
- Space 无持久存储 → 一切持久化走 Neon（主）/ R2（副）/ Bucket（文件）
- 历史上的 Supabase 方案已于 2026-08-17 全量退役（见 ARCHITECTURE.md 迁移记录）
- 相关独立仓库：`i3t2y/n-omn`（私有，omn LLM 中转网关 Space，自有宪法与血统，不入本仓）
