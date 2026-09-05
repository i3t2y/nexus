# mem0 归档(old/mem0/)

本目录归档 nexus 旧架构中 **Mem0 挂 hermes 插件位** 路径(选项 A 彻底删之前)的激活链原文。
来源:旧 commit `d96c408`(reset --hard origin/main 之前的本地 HEAD)经 `git show d96c408:<path>` 提取。

> 注:hermes 已将 Mem0 从 hermes 插件位移除,改为独立 **`memgraph` 空间** 跑 Mem0 server 模式(见 `/memgraph/`)。
> 本目录仅留旧路径激活链作历史参考,非现役实现。

## 归档件(5)

| 文件 | 来源 | 内容 |
|------|------|------|
| `config.yaml.template.mem0-active` | `git show d96c408:spaces/hermes/scripts/config.yaml.template` L222-245 | `memory` 激活段(mem0 provider 配置原文) |
| `real-start.sh.mem0-active` | `git show d96c408:spaces/hermes/scripts/real-start.sh` L134-184 | mem0 env 注入段 + Python config merge 块原文 |
| `requirements-base.txt.mem0-active` | `git show d96c408:docker/requirements-base.txt` L92-101 | `mem0ai==2.0.10` + pin 说明原文 |
| `mem0.json.template` | **★ HF Bucket 救回**(`hf://buckets/sonoke/logic/scripts/mem0.json.template` 813 bytes,2026-08-14 推) | mem0 server config 模板(envsubst 占位符):mode=oss + pgvector(`connection_string: ${MEM0_PG_URI}`+collection=hermes_mem0+hnsw=false+2048维)+ llm glu-4.7-flash(z.ai)+ embedder nemotron-3-embed-1b(NIMeken)。JSON valid,全占位符无真值 |
| `sql/04_mem0_selfhost.sql` | **从长期记忆笔记重建**(nexus-mem0-codeside-done L20/L28 原文凭据) | Supabase pgvector `CREATE EXTENSION vector` + RLS 兜底 do$$ 块(`hermes_mem0` 表建后挂 anon deny)。**不预建表**,让 mem0 pgvector 后端 `create_col` 自建 |

## 救回说明(原本以为失档 2 件,2026-08-17 补救)

接手时认为 `mem0.json.template` + `sql/04_mem0_selfhost.sql` 两件因 reset 前为 `??` 未追踪 + git 无 commit → 不可复原。深查后:

- **`mem0.json.template`**: HF Bucket `sonoke/logic/scripts/` 仍存原推件(813 bytes,2026-08-14 推)→ `hf buckets cp` 救回至本目录,字节一致内容闭环
- **`sql/04_mem0_selfhost.sql`**: Bucket 无 SQL(只放 scripts/),但长期记忆笔记 `nexus-mem0-codeside-done-2026-08-10.md` L20/L28 原文详载(`CREATE EXTENSION vector` + RLS 兜底块,**不预建表**让 mem0 `pgvector.py` create_col 自建)→ 凭笔记重建,对齐 `old/sql/03_rls_policies.sql` RLS 风格

> 两件现均完整归档。改回 Supabase 旧 mem0-plugin 路径时可凭此 5 件 + `old/spaces/hermes/`(d96c408 原版旧 hermes 源码含 mem0 激活链)完整复原。

## 关联

- 现役 Mem0 实现:`/memgraph/`(独立 Space,server 模式 + Neon pgvector)
- hermes 插件位移除证据:`hermes/scripts/config.yaml.template`(全 grep `mem0` 无命中)
- 长期记忆笔记:`~/.claude/projects/-home-laisi-nexus/memory/nexus-mem0-codeside-done-2026-08-10.md`
