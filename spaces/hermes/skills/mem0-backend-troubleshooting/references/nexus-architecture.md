# Nexus Architecture: Hermes + LangGraph + Mem0

User-defined architecture (2026-08-15) for unified cloud memory and multi-agent orchestration on free tiers. This is the user's "定稿" (finalized) architecture.

## Architecture Overview

```
┌─────────── Local ───────────┐
│ AgentOS (skills, memory raw) │
│ Hermes + Studio (cockpit)   │
│ Code agents: CC / Codex / Pi │
└──────────────┬──────────────┘
               │
┌──────────────▼─── Cloud ───┐
│ Entry: Hermes Space          │
│   chat · plan · route · write │
│                              │
│ Worker: LangGraph + Mem0     │
│   (same HF Space)            │
│   LangGraph = orchestration  │
│   Mem0 = memory storage      │
│                              │
│ Neon: task table + vectors   │
│ R2: artifacts/logs/snapshots │
│ (Upstash: optional queue)    │
│                              │
│ Code: NPC Buddy / local CLI  │
└─────────────────────────────┘
```

## Component Roles

| Layer | Responsibility |
|-------|----------------|
| Local AgentOS + Studio | Migratable assets, daily organization, heavy coding |
| Cloud Hermes | Always-available chat entry point, routing, memory writes |
| Cloud LangGraph + Mem0 (Worker Space) | Orchestration + memory execution |
| Neon / R2 | Source of truth (SQL + vectors) + files (artifacts, logs) |
| NPC / local CLI | Actual code changes (heavy work offloaded) |

## LangGraph ↔ Mem0 Relationship

**LangGraph uses Mem0, does NOT replace Mem0.**

| Aspect | LangGraph | Mem0 |
|--------|-----------|------|
| Role | Orchestration (state graph, branching, retry, reflection) | Memory storage + retrieval |
| Layer | Execution layer | Memory layer |
| Calls | Uses Mem0 search/add as nodes/tools in the graph | Provides HTTP API for memory CRUD |

**Preferred implementation**: Mem0's `search`/`add` as LangGraph graph nodes (in-process function calls when same container). When split across processes/Spaces, use localhost HTTP.

**Graph pattern**:
```
start → retrieve_memory(mem0) → plan → act → verify
                                      ↓
                              reflect → write_memory(mem0) → end
```

Hermes can call Mem0 directly (simple search/write) without going through LangGraph. LangGraph only enters the picture for multi-step tasks needing branching/retry/fixed reflection.

## LangGraph on Free HF Space: What Works, What Doesn't

| Capability | Free HF | Notes |
|-----------|---------|-------|
| State graph, conditional edges, retry | ✅ | Core value — this is why LangGraph |
| Checkpoint persistence | ✅ | Store in Neon, not Space disk |
| Human-in-the-loop interrupt | ⚠️ | Needs external state store + user re-entry |
| Large-scale parallel subgraphs | ❌ | Don't push free tier this hard |
| In-graph heavy coding agents | ❌ | Offload to external Claude Code / Codex / NPC |
| Hour-long uninterrupted runs | ❌ | Sleep/timeout; use async task pattern instead |

**Conclusion**: Free HF LangGraph = 瘦编排 (thin orchestrator), NOT 全功能 agent runtime. Orchestration in cloud, execution offloaded.

## Infrastructure: Neon / R2 / Upstash

**Not three components each with their own infra.** R2 and Upstash are Nexus-wide infrastructure, attached by need:

| Infra | Who uses | For what | Required? |
|-------|----------|----------|-----------|
| Neon | Mem0, task table, (optional) LangGraph checkpoint | Vectors, task state, graph state | Yes |
| R2 | Hermes / Worker / local sync | Large files, artifacts, logs, skill snapshots, backups | Strongly recommended |
| Upstash | Hermes queue, Worker task grab | Only task_id hot queue | Optional (Neon polling works) |

**Mem0** writes to Neon (vectors). **LangGraph** can checkpoint to Neon. **R2** is NOT a vector store — it's for large objects (logs, artifacts, snapshots).

## Persistence and Logging on Ephemeral Disk

**Rule: ephemeral disk is scratch space only.**

| Content | Storage | Survives restart? |
|---------|---------|-------------------|
| Memory vectors | Neon pgvector | ✅ |
| mem0 auth/settings | Neon tables | ✅ |
| LangGraph checkpoint | Neon | ✅ |
| LangGraph run logs | Neon `task_logs` table | ✅ (write via psycopg, NOT local files) |
| SQLite history.db | Ephemeral `/app/history/` | ❌ (acceptable — only LLM call history, not memory) |
| uvicorn stdout | HF Space Logs UI | ⚠️ (HF keeps recent, but not permanent) |
| LangGraph worker code | HF Dataset | ✅ (`snapshot_download` on boot) |

**Log debugging pattern**: LangGraph nodes write structured logs to Neon `task_logs` (already exists in schema). NOT local files — those wipe on restart, making post-mortem debugging impossible.

## Architecture Status: Mostly Obsolete (2026-08-16)

The nexus design above was for a **4-Space + R2 + Supabase + Bucket + GHCR** architecture. The actual deployment has diverged significantly:

| Nexus designed | Actual deployment | Status |
|----------------|-------------------|--------|
| 4 Spaces (hermes/langgraph/claude/codex) | 2 Spaces (hermes on sonoke + mem0/LangGraph on nmem/memgraph) | 3 Spaces cut |
| R2 for large files | No R2 | Dropped |
| Supabase for structured state | Neon Postgres | Replaced |
| HF Storage Bucket rw mount | **✅ Active** — `/data` is `hf-mount` FUSE rw mount (`sonoke/logic` bucket) | In use |
| GHCR base image | Not used | Dropped |
| Config in Supabase tables | Config in files (mem0.json, config.yaml) | Different |
| Per-file `_FILES` sync | hermes built-in restore/uploader (same per-file approach) | Legacy |

**The only nexus remnant** is hermes's built-in `restore_home_files.py` / `home_files_uploader.py` — but these are part of the hermes Docker image, not nexus additions. The mem0.json persistence gap is a hermes-native issue.

**Key lesson**: When a project evolves away from the original architecture (nexus → simpler 2-Space + Neon), the persistence strategy should also evolve. Continuing to maintain a per-file `_FILES` list designed for a multi-Space + Supabase architecture creates gaps (like mem0.json) that wouldn't exist in a whole-directory sync approach (HermesFace/HuggingMes).

## Three-Part Triad = Unified Cloud Brain (2026-08-17 session)

The user clarified the final positioning: **三件套 = 云上大脑, 用来统一和调度其他agent (云端或本地)**.

```
┌─────────────────────────────────┐
│  云上大脑 (三件套)                │
│                                   │
│  Hermes (sonoke Space) — 入口/路由/调度 │
│    ├─ MCP → LangGraph worker      │
│    ├─ HTTP → Mem0 server           │
│    └─ Neon — 记忆+任务表           │
│                                   │
│  统一记忆层 ↓ 统一任务分发 ↓       │
└──────────┬──────────────────────┘
           │
    ┌──────┴──────┐
    ▼             ▼
 本地 agent    云端 agent
 (Claude/     (其他 HF Space
  Codex/       /第三方服务)
  本机脚本)
```

**nexus概念不变, 只换基础设施工具**: R2→砍, Supabase→换Neon, GHCR→砍, 4Space→2Space。核心概念(hermes做入口/路由/调度+统一记忆层+逻辑层与镜像分离+永续铁律+HF rebuild付费墙规避)全部保留。Bucket vs Dataset选择按Space需求分: hermes Space用Bucket(rw挂载+state.db快照), memgraph Space用Dataset(boot拉取逻辑层)。两个选择都对。
