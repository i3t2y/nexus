# docs/新架构/ 归档(old/docs/新架构/)

本目录归档 nexus 早期演进提案(2026-08-15 前后会话产物)。

## 来源

接手 nexus 时,本地工作区 `docs/新架构/` 为未追踪(`??`)状态 4 件方案稿 +
`AGENTS.md` 1 件。`git reset --hard origin/main` 前用 `git stash -u` 抢救,本轮从
`stash@{0}^3` blob 捞出归 old/(其余 stash 过时的 Modified 件 — Supabase 旧路径改 — 弃)。

## 为何归 old/

origin/main 已于 2026-08-17 (`f035a48`/`7eaad95`) 落地 Supabase→Neon 全量迁移 +
`memgraph` 空间(mem0 server + LangGraph worker)新建。本目录提案的核心想法
(HF 三组件 Hermes+LangGraph+Mem0 + Neon + R2 + 本地 AgentOS)**已被 hermes 实现
为现役 memgraph 架构**。提案文档已超越版本,留作演进史参考。

## 归档件

| 文件 | 内容(摘要) | 与现役关系 |
|------|-----------|-----------|
| `New Nexus 完整方案-Upstash 仅作队列.md` | 可执行蓝图:Hermes 大脑 · 异步任务 · Upstash 热队列 · Neon 真相 · R2 大文件 | 主体已落地;**Upstash 热队列未采**(方案提到同步 HTTP→异步队列演进,自由量热心但触 Upstash 免费层瓶颈暂未做) |
| `三组件.md` | 论"无需第四重组件",云端 Hermes+LangGraph+Mem0 闭环 | ✅ 对齐现役三件套(hermes+memgraph+Neon) |
| `云端三组件.md` | 本地 AgentOS + 云端三组件两套并行分工 | ✅ 本地 AgentOS 未落地(用户独立任务);云端三组件已活 |
| `本地.md` | 论"本地一团乱的项目/Agent 状态,统一收纳/备份/迁移" = 本地 AgentOS 目录规范 | ⚠️ 可能仍可(纯加法,跟云架构正交)— 用户独立任务待推进 |

## 关联

- 现役三件套权威件:`docs/shared/ARCHITECTURE.md`(origin/main 2026-08-17 重写版)
- memgraph STATUS:`memgraph/STATUS.md`
- 本目录同阶段核证件(我接手时做的 plan):`~/.claude/plans/shiny-moseying-quasar.md`(三选项对比,选项②择增量)
- 长期记忆:`nexus-chat-extracted-decisions-2026-08-15.md`(16 决策含演进方向)

> `AGENTS.md` 同期归 `old/` 根(`old/AGENTS.md`),内容基于 d96c408 旧结构已全过时;
> 现役项目说明待按 memgraph 新结构重写(非本目录范围)。
