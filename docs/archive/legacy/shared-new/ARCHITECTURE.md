# 三件套统一架构 (2026-08-22 勘误: Mem0 未部署至 sonoke/h)

## 三件套
```
┌─────────────────────────────────────────────────────┐
│  hermes (sonoke/h)    云上大脑: 入口/路由/调度       │
│  └→ persist_to_neon.py 主路 → Neon 四表             │
│  └→ persist_to_r2.py 副路 → Neon 读 → R2 快照备份   │
│  └→ act delegate 写 task_queue kind='npc' → 本机桥  │
│  (Mem0 未部署, LangGraph 是 hermes 的 Python 库)     │
├─────────────────────────────────────────────────────┤
│  memgraph (nmem/memlg) 冷备 (暂停态, 不运行)         │
│  └→ 同 GHCR 镜像 + 同 Bucket 逻辑 + 同 Neon/R2 密钥  │
│  └→ LangGraph worker 代码在 bucket/ 中 (待活跃时用)  │
├─────────────────────────────────────────────────────┤
│  Neon Postgres  数据持久化 (主路)                    │
│  └→ agent_states / task_logs / long_memory /        │
│    skills_index (hermes 结构化四表)                  │
│  └→ task_queue (扁平表 + kind/input,                │
│     Stage A 2026-08-18 详见 memgraph/STATUS.md)     │
│     kind: {generic|graph|npc|claude_code|pi}        │
│     (workbuddy_npc 路废 Gork 2026-08-18)           │
│     消费: FOR UPDATE SKIP LOCKED poll (本机桥)       │
│  └→ (pgvector 扩展已装, 待 Mem0 部署时启用)          │
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

(Mem0 未部署, 无向量记忆层。当前真相源 = Neon 四表 + MEMORY.md + skills)
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
| (Mem0 未部署) | 向量记忆 | — | 待后续注入 real-start.sh + HF Secrets |

## Supabase → Neon 迁移 (2026-08-17) + R2 副路恢复 (2026-08-18)
- **原**: mem0 oss mode 直连 Supabase pgvector + persist_to_r2.py 四表 Supabase→R2 双写
- **2026-08-17**: mem0 self_hosted 代码侧已落但生产未部署 → persist_to_neon.py 直连 Neon 四表 (主路)
- **2026-08-17 砍掉**: Supabase (7天暂停风险) + MEM0_PG_URI; R2 当时一并砍 (Neon 已持久化)
- **2026-08-18 R2 副路恢复**: persist_to_r2.py 读源 Supabase→Neon (HTTP /sql), R2 作灾备快照层
  与 Neon 主路双写,manifest-only 不进 DB (sha256/bytes/rows 放 R2 MANIFEST.json)
- **2026-08-22 勘误**: Mem0 代码侧已完成 (mem0.json.template + base 镜像含 mem0ai) 但生产未部署,
  real-start.sh 无 mem0 注入段, sonoke/h 未配 Mem0 Secrets
- **现役持久化**: Neon (主路持久) + R2 (副路灾备快照) 双层;Supabase 全退役
- **DDL**: `memgraph/docs/neon-schema.sql` (七表幂等, 无 RLS, 不含 backup_snapshots)

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
