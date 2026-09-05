# 三件套统一架构 (2026-08-23 更新: Mem0 已部署至 sonoke/h)

## 三件套
```
┌─────────────────────────────────────────────────────┐
│  hermes (sonoke/h)    云上大脑: 入口/路由/调度       │
│  └→ persist_to_neon.py 主路 → Neon 四表             │
│  └→ persist_to_r2.py 副路 → Neon 读 → R2 快照备份   │
│  └→ act delegate 写 task_queue kind='npc' → 本机桥  │
│  (Mem0 已部署 = 进程内 OSSBackend → pgvector,      │
│   LangGraph 是 hermes 的 Python 库)                 │
├─────────────────────────────────────────────────────┤
│  memgraph (nmem/memlg) — 【已废弃 2026-09-05】             │
│  └→ hermes+mem0 OSS 进程内模式已满足需求, 全量移入         │
│     other/memgraph-20260905/ (含 deploy workflow, 摘出       │
│     .github/workflows/, 不再触发部署)                       │
├─────────────────────────────────────────────────────┤
│  Neon Postgres  数据持久化 (主路)                    │
│  └→ agent_states / task_logs / long_memory /        │
│    skills_index (hermes 结构化四表)                  │
│  └→ task_queue (扁平表 + kind/input,                │
│     Stage A 2026-08-18 详见 other/memgraph-20260905/STATUS.md)     │
│     kind: {generic|graph|npc|claude_code|pi}        │
│     (workbuddy_npc 路废 Gork 2026-08-18)           │
│     消费: FOR UPDATE SKIP LOCKED poll (本机桥)       │
│  └→ pgvector 已启用 (Mem0 hermes_mem0 表, 2026-08-23)│
├─────────────────────────────────────────────────────┤
│  Cloudflare R2  灾备快照层 (副路)                    │
│  └→ snapshots/<ts>/{四表}.json + MANIFEST.json      │
│  └→ sha256/bytes/rows, manifest-only 指针格式       │
│  └→ 读源 = Neon HTTP /sql (2026-08-18 恢复)          │
└─────────────────────────────────────────────────────┘
```

## 数据流
```
用户 → Telegram → hermes (sonoke/h)
  → LLM 推理 → OmniRoute (nonoke/omn) /v1/chat/completions
  → persist_to_neon.py 主路 (后台 600s) → Neon 四表 (结构化状态)
  → persist_to_r2.py 副路 (后台 1800s) → Neon 读 → R2 快照备份
  → act delegate → task_queue kind='npc' → 本机桥 poll → CNB CodeBuddy

(Mem0 已部署, 向量记忆层走 hermes_mem0。真相源 = Neon 四表 + hermes_mem0 + MEMORY.md + skills)
```

## 存储
| 层 | 内容 | 存储 | 持久化方式 |
|---|---|---|---|
| hermes 结构化 (主路) | 四表状态 | Neon | persist_to_neon.py 直连 (600s) |
| hermes 结构化 (副路) | 四表快照 | R2 对象存储 | persist_to_r2.py 读 Neon 写 R2 (1800s, manifest-only) |
| task_queue | 委托任务 | Neon | act delegate 写 → 本机桥 FOR UPDATE SKIP LOCKED 消费 |
| 三文件 | Dockerfile+README+start.sh | HF Space git repo | Actions 推 (不频繁改) |
| 逻辑层 | scripts/app/libs/plugins | HF Bucket (rw 挂载) | Actions sync |
| home 文件 | .env/SOUL.md/config.yaml | HF Bucket home-backups/ | restore+uploader 周期 |
| state.db | 会话历史 | 本地盘+Bucket快照 | restore_state+state_uploader |
| 代码版本 | 全部 | GitHub i3t2y/nexus (public) | git push |
| Secrets | Postgres/R2/API keys | HF Space Secrets | 零文件持久化 |
| Mem0 | 向量记忆 | Neon pgvector | 进程内 OSSBackend (hermes_mem0 表, 2026-08-23) |

## Supabase → Neon 迁移 (2026-08-17) + R2 副路恢复 (2026-08-18)
- **原**: mem0 oss mode 直连 Supabase pgvector + persist_to_r2.py 四表 Supabase→R2 双写
- **2026-08-17**: mem0 self_hosted 代码侧已落但生产未部署 → persist_to_neon.py 直连 Neon 四表 (主路)
- **2026-08-17 砍掉**: Supabase (7天暂停风险) + MEM0_PG_URI; R2 当时一并砍 (Neon 已持久化)
- **2026-08-18 R2 副路恢复**: persist_to_r2.py 读源 Supabase→Neon (HTTP /sql), R2 作灾备快照层
  与 Neon 主路双写,manifest-only 不进 DB (sha256/bytes/rows 放 R2 MANIFEST.json)
- **2026-08-23 Mem0 部署**: real-start.sh 加 mem0 注入段 (门控 MEM0_MODE=oss), mem0.json 注入
  (OSSBackend pgvector: nemotron-3-embed-1b 2048 维 + hnsw:false + 智谱 LLM), Neon hermes_mem0 表建成
- **2026-08-23 P0 模型 EOL 修复**: 主模型 nvidia/z-ai/glm-5.2 EOL (410) → 换 nvidia/deepseek-ai/
  deepseek-v4-flash-0731。sync_omn_models.py 三件套过滤 (前缀 nvidia + 关键词 deepseek/glm +
  排除 tllm/oc/openai), 278 omn 模型滤到 117 白名单, 只增不删动态跟随 omn
- **现役持久化**: Neon (主路持久 + hermes_mem0 向量) + R2 (副路灾备快照) 双层;Supabase 全退役
- **DDL**: `other/memgraph-20260905/docs/neon-schema.sql` (七表幂等, 无 RLS, 不含 backup_snapshots)

## 部署链
```
GitHub i3t2y/nexus (public, Actions 无限免费)
  → hermes/ 代码逻辑层 (Bucket 挂载引用)
  (2026-09-05 起 memgraph 部署链废弃: deploy-memgraph.yml 摘入 other/memgraph-20260905/)
```

## per-Space Token
| Token | 用途 | Secret 名 |
|---|---|---|
| HF_H_TOKEN | hermes (sonoke) 推送 | secrets.HF_H_TOKEN |
| HF_M_TOKEN | ~~memgraph (nmem) 推送~~ (memgraph 已废弃 2026-09-05, token 留备) | secrets.HF_M_TOKEN |
| HF_L_TOKEN | Bucket 操作 | secrets.HF_L_TOKEN |
| HF_CC_TOKEN | Claude Code Space | secrets.HF_CC_TOKEN |
| HF_C_TOKEN | Codex Space | secrets.HF_C_TOKEN |

## nexus 仓库结构
```
nexus/
  hermes/
    space/       ← Dockerfile + README.md + start.sh
    app/ libs/ mcp/ scripts/ skills/
  mem0/        ← 向量记忆层文档 (hermes 进程内 OSS → Neon, 2026-09-05 新增)
  docs/
    hermes/   ← hermes 持久化/架构文档
    memgraph/ ← mem0 历史运维文档 (已标 ARCHIVED)
    shared/   ← 共享 (ARCHITECTURE/CREDENTIALS)
    archive/  ← 不用的旧文档
  other/         ← 暂存不用的 (claude-code/codex/langgraph/honcho/memgraph-20260905)
```
