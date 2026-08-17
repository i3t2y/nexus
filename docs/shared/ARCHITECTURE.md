# 三件套统一架构 (2026-08-17)

## 三件套
```
┌─────────────────────────────────────────────────────┐
│  hermes (sonoke/h)    云上大脑: 入口/路由/调度       │
│  └→ mem0 (self_hosted) → MEM0_HOST → memlg Space     │
│  └→ persist_to_neon.py → Neon 四表                   │
├─────────────────────────────────────────────────────┤
│  memgraph (nmem/memlg) 记忆+编排                    │
│  └→ mem0 server → Neon memories 表 (pgvector)       │
│  └→ LangGraph worker (进程内)                       │
├─────────────────────────────────────────────────────┤
│  Neon Postgres  数据持久化                           │
│  └→ memories 表 (mem0, pgvector 2048维)             │
│  └→ agent_states / task_logs / long_memory /        │
│    skills_index (hermes 结构化四表)                  │
│  └→ task_queue / space_health (辅助)                │
└─────────────────────────────────────────────────────┘
```

## 数据流
```
用户 → Telegram → hermes (sonoke/h)
  → mem0_search/mem0_add → SelfHostedBackend → HTTP → memlg Space
    → mem0 server → Neon memories (pgvector 语义搜索)
  → persist_to_neon.py (后台) → Neon 四表 (结构化状态)

保活:
  cron-job.org (每4min) → memlg /health → Neon SELECT 1
```

## 存储
| 层 | 内容 | 存储 | 持久化方式 |
|---|---|---|---|
| mem0 记忆 | 向量记忆 | Neon memories 表 | Neon 持久化 (scale-to-zero auto-wake) |
| hermes 结构化 | 四表状态 | Neon | persist_to_neon.py 直连 |
| 三文件 | Dockerfile+README+start.sh | HF Space git repo | Actions 推 (不频繁改) |
| 逻辑层 | scripts/app/libs/plugins | HF Bucket (rw 挂载) | Actions sync |
| home 文件 | .env/SOUL.md/config.yaml | HF Bucket home-backups/ | restore+uploader 周期 |
| state.db | 会话历史 | 本地盘+Bucket快照 | restore_state+state_uploader |
| 代码版本 | 全部 | GitHub i3t2y/nexus (public) | git push |
| Secrets | Postgres/API keys | HF Space Secrets | 零文件持久化 |

## Supabase → Neon 迁移 (2026-08-17)
- **原**: mem0 oss mode 直连 Supabase pgvector + persist_to_r2.py 四表 Supabase→R2 双写
- **新**: mem0 self_hosted → memlg Space → Neon memories + persist_to_neon.py 直连 Neon 四表
- **砍掉**: Supabase (7天暂停风险) + R2 (Neon 已持久化不需要额外快照) + MEM0_PG_URI
- **DDL**: `memgraph/docs/neon-schema.sql` (七表幂等, 无 RLS)

## 部署链
```
GitHub i3t2y/nexus (public, Actions 无限免费)
  → .github/workflows/deploy-memgraph.yml
    → deploy-space:  memgraph/space/ → HF Space git (rebuild)
    → deploy-bucket: memgraph/bucket/ → HF Bucket sync (不 rebuild)
  → hermes/ 代码逻辑层 (Bucket 挂载引用)
```

## per-Space Token
| Token | 用途 | Secret 名 |
|---|---|---|
| HF_H_TOKEN | hermes (sonoke) 推送 | secrets.HF_H_TOKEN |
| HF_M_TOKEN | memgraph (nmem) 推送 | secrets.HF_M_TOKEN |
| HF_L_TOKEN | Bucket 操作 | secrets.HF_L_TOKEN |
| HF_CC_TOKEN | Claude Code Space | secrets.HF_CC_TOKEN |
| HF_C_TOKEN | Codex Space | secrets.HF_C_TOKEN |

## nexus 仓库结构
```
nexus/
  hermes/
    space/       ← Dockerfile + README.md + start.sh
    app/ libs/ mcp/ scripts/ skills/
  memgraph/
    space/       ← Dockerfile + README.md + start.sh  → HF Space git
    bucket/      ← entrypoint.sh + run.py + graph/    → HF Bucket sync
    docs/  STATUS.md
  docs/
    hermes/   ← hermes 持久化/架构文档
    memgraph/ ← memgraph 部署文档
    shared/   ← 共享 (ARCHITECTURE/COMMUNICATION/CREDENTIALS)
    archive/  ← 不用的旧文档
  old/         ← 暂存不用的 (claude-code/codex/langgraph/honcho)
  .github/workflows/deploy-memgraph.yml
```
