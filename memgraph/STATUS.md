# memgraph Space — 三件套之一

## 定位
- **HF Space**: `nmem/memlg` (public, Docker SDK, port 7860)
- **职能**: Mem0 server (记忆层) + LangGraph worker (编排)
- **后端**: Neon Postgres (pgvector, AWS us-east-1)
- **保活**: cron-job.org 每 4min ping `/health`

## 三件套
1. Hermes (sonoke/h) — 入口/路由/调度 (云上大脑)
2. memgraph (nmem/memlg) — 记忆+编排 (本目录)
3. Neon — 数据持久化 (mem0 memories + hermes 结构化四表)

## 文件结构 (2026-08-17 重构)
- `space/` — 三文件 (Dockerfile + README.md + start.sh), 推 HF Space git repo
- `bucket/` — 逻辑层 (entrypoint.sh + run.py + graph/ + patches/ + requirements.txt), sync HF Bucket
- `docs/` — neon-schema.sql + DEPLOY.md + SECRETS.md
- `STATUS.md` — 本文件

## 持久化
- 三文件 → HF Space git repo (不频繁改, Actions 推)
- 逻辑层 → HF Bucket `nmem/logic` (rw 挂载 /data, Actions sync)
- 配置 → HF Secrets (零文件持久化)
- 版本化 → GitHub `i3t2y/nexus` public 仓库

## 部署链
```
GitHub i3t2y/nexus (版本化真源, public)
  → Actions (两个 job 分开跑)
    → deploy-space:  三文件 space/ → HF Space git repo (触发 rebuild)
    → deploy-bucket: 逻辑层 bucket/ → HF Bucket sync (不 rebuild)
```

## Neon 表结构
- `memories` — mem0 记忆 (pgvector 2048维, hnsw=False)
- `agent_states` / `task_logs` / `long_memory` / `skills_index` — hermes 结构化四表 (主路 persist_to_neon.py + 副路 R2 快照)
- `task_queue` / `space_health` — 辅助表
- 不含 `backup_snapshots` (R2 副路 manifest-only 不走 DB)
- DDL: `docs/neon-schema.sql` (幂等, 不需要 RLS)
- `task_queue` (memlg 专属; hermes 不双写): Stage A 2026-08-18 统一双 DDL
  → 扁平表 (task_id PK / task / user_id / status[pending|running|completed|failed]
  / kind / input jsonb / output jsonb / result / attempts / updated_at + touch trigger);
  新增 kind/input/output/attempts/updated_at 供 Stage B 本机桥 WHERE kind='npc';
  Stage B (2026-08-22 已落) 桥脚本 bridge/poll_worker_tasks.py 扫 pending+kind=npc
  → CNB CodeBuddy OpenAPI (POST /{repo}/-/build/start event=api_trigger_npc,
  Gork 2026-08-18 裁决 workbuddy_npc 路废 → kind 枚举收
  {generic|graph|npc|claude_code|pi}), 成败回写 Neon output; 先 OpenAPI curl 路
  无 Node 依赖 (MCP stdio 需装 Node 重 build base 付费墙, 走通再议升 MCP);
  FOR UPDATE SKIP LOCKED 标准消费模式 (Gork 裁定③); act/delegate 仍写
  kind='generic' 兜底, kind='npc' 写端待后续 Hermes plugin 或手动入;
  kind=graph 两路并存 (同步 plugin route_langgraph 短图 + 异步 task_queue +
  SKIP LOCKED poll 长图[Stage B 增强])。

## 状态 (2026-08-22)
- ✅ memlg Space RUNNING, /health ok (不碰 Neon, let it scale-to-zero)
- ✅ Neon memories 表有数据
- ✅ Actions run #9 success (两 job 分开跑通)
- ✅ Bucket nmem/logic 挂载 rw
- ✅ 2026-08-17 Neon Free 保活反策略落地 (persist_to_neon httpx /sql 短请求 + /health 不碰 Neon, commit 3fbd846)
- ✅ 2026-08-18 R2 副路恢复 (persist_to_r2.py 读源 Supabase→Neon, 与 Neon 主路双写)
- ✅ Stage B 本机桥: bridge/poll_worker_tasks.py 已落 (FOR UPDATE SKIP LOCKED + CNB OpenAPI curl 路, 2026-08-22)
- ⏳ Neon 七表 DDL 待执行 (neon-schema.sql)
- ⏳ hermes Space 待加 POSTGRES_* Secrets (主路+R2 副路共用) + R2_* Secrets (副路灾备)
- ⏳ hermes Space 待删旧 SUPABASE_* + MEM0_PG_URI Secrets
- ⏳ hermes Space 待重启 (让 mem0.json self_hosted + Neon 主路 + R2 副路生效)
