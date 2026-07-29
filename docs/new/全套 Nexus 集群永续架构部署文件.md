# 全套 Nexus 集群永续架构部署文件

> 权威件。八件套部署清单（5 业务件 + 1 GitHub Action + 1 白皮书）+ 5 条核心修正摘要。路径需对齐 Nexus 现役（a142da9 / fe275ae / 4fc098e）：GHCR base 镜像承依赖、Bucket rw `/data` 单挂逻辑层（`app/scripts/libs`）、Dockerfile 退为墓碑。下方表中凡标「omn 血统模板路程」者，指该件在 omn-merge 仓内的实物路程，Nexus 侧或未落同物或路径相异，配套对齐注见说明栏，不删模板叙述但禁误断为 Nexus 既有。

## 一、八件套清单

| 文件 | 路径 | 说明（含量程对齐） |
|---|---|---|
| `Dockerfile` | `docker/nexus-base.Dockerfile`（Nexus 现役）/ `docker/Dockerfile`（omn 血统模板路程） | Nexus 现役 a142da9：墓碑化一行 `FROM ${BASE_IMAGE}` + ARG 占位，零业务逻辑；依赖全部进 GHCR base 镜像永不入 HF repo。fe275ae 修正 `ARG BASE_IMAGE` 全局声明 + `FROM ${BASE_IMAGE}` + ARG 重声明 + `ENV BASE_IMAGE=${BASE_IMAGE}` 转存，填真 GHCR owner `i3t2y`。表内 `docker/Dockerfile` 为 omn 血统模板路程，Nexus 实仓为 `docker/nexus-base.Dockerfile`，已落不取模板。 |
| `start.sh` | `docker/start.sh`（omn 血统模板路程）/ Nexus 在 `spaces/hermes/` 内 | 环境自愈 + 逻辑层拉取。Nexus 现役 a142da9/4fc098e：走 `hf buckets sync` 拉真 **Bucket**（非 Dataset）bootstrap 兜底。竞速根治行（锁 Dataset HEAD commit_id 再按 revision 拉取）为 omn-merge 现役法（见修正项⑦），Nexus 侧因改采 Bucket rw 单挂，逻辑层为 Bucket 同步覆盖、无此 Dataset 锁竞速路径，该行不适用 Nexus 现役，见修正项②⑦对齐注。 |
| `entrypoint.sh` | `logic/entrypoint.sh`（omn 血统模板路程） | PID1 daemon 编排 + 五组件自适应分支。omn 血统：唯一写 `/logic` 处为 `entrypoint.sh:246` 的 `npm install`（一次性），见修正项⑥。Nexus 现役 a142da9：逻辑层由 Bucket rw `/data` 承载（app/scripts/libs），不再 Volume 只读挂 Dataset。该 `logic/entrypoint.sh` 路程属 omn 血统模板，Nexus 无 `logic/` 目录，禁误断为既有。 |
| `gate.js` | `logic/gate.js`（omn 血统模板路程） | PSK 常量时间校验 + 字节限流 + 透明代理。硬断言 `exit 1` 在此件（`gate.js:46-49` PSK 缺失），见修正项⑤。Nexus 侧路程未定稿，**本件为 omn 血统模板叙述，Nexus 现役实际见 `spaces/hermes/*`**，禁误断为既有。 |
| `litestream.yml` | `logic/litestream.yml`（omn 血统模板路程） | **边界警示（必读）**：litestream 仅懂 SQLite WAL，跑不起 Postgres。Nexus 主状态在 Supabase PostgreSQL（ARCHITECTURE.md：AsyncPostgresSaver + Supabase），物理 WAL litestream 读不了，**对 Nexus 是技术误配**；omn-merge/OmniRoute 用 SQLite 故 litestream 正用。Nexus 灾备正解 = Supabase 自带 daily backup + PITR（Pro+ RPO 2min）/ 免费档 `supabase db dump` 入 R2 四桶之一，禁照样配 litestream。见修正项⑧。该 `logic/litestream.yml` 路程属 omn 血统模板。 |
| `commit_scheduler.sh` | `logic/commit_scheduler.sh`（omn 血统模板路程） | A/B 类日志脱敏归档 + C 类严格屏蔽。运行态写件全程去 `/data` 或 `/tmp`（修正项⑥：`init-nim-keys.sh LOG_DIR`、`omn_*.py` 写 `/data/omn-sched/*`，唯 `/logic` 的 `entrypoint.sh:246 npm install` 一次性写入）。`logic/commit_scheduler.sh` 路程属 omn 血统模板，见修正项⑥对齐。 |
| `sync-logic-canary.yml` | `docker/sync-logic-canary.yml`（omn 血统模板路程） | GitHub Action，逻辑层零 Rebuild 推送。**修正项③**：dataset push 不触发挂它的 Space rebuild（manage-spaces Note 逐字："Models,datasets,Spaces always mounted as read-only"；spaces-overview 中 "the Space" 指自身 repo，非被挂 dataset 端），热更真正路径 = git push + 显式 `request_space_hardware`/`restart_space`，不撞付费墙。该 Action 路程属 omn 血统模板。 |
| `Everlasting_Architecture_Specification.md` | `docs/` | 完整白皮书，含拓扑、安全红线、部署指南。应含 Nexus 四 worker（hermes/langgraph/claude-code/codex）+ user-a~e 拓扑、Supabase Postgres + R2 四桶（nexus-backups/checkpoints/skills/artifacts）拆分、用户红线"git push 只能手动、永不 push HF"。 |

## 二、本轮核心修正与优化（相较历史会话）

- **LangGraph CVE-2026-28277 修复**：entrypoint 中强制 Library Mode，拒绝 `langgraph-api` 二进制。**修正项⑧纠错**：原锁 `langgraph-checkpoint-sqlite>=3.0.1` 为误植，Nexus langgraph Checkpointer 实为 `AsyncPostgresSaver`（`langgraph-checkpoint-postgres`），`app/main.py` FastAPI lifespan 建一次 + `await cp.setup()`，`from_conn_string` 硬码 `prepare_threshold=0`/autocommit/row_factory，底层 psycopg3 入 Supabase PostgreSQL，非 ephemeral SQLite。应锁 `langgraph-checkpoint-postgres` 而非 sqlite 轨。
- **Claude Code / Codex 铁律 L7**：容器内强制 API Key 模式，完全禁止 OAuth 浏览器跳转。
- **三级日志脱敏防火墙**：commit_scheduler 正则过滤 api_key/bearer/psk/jwt，C 类 Init 日志严禁公开。（与清单 commit_scheduler.sh 行同件事，此为摘要侧表述。）
- **竞速根治（omn 血统模板法，Nexus 待定稿）**：**修正项⑦**：omn-merge `start.sh:64-80` 取 Dataset HEAD commit_id 锁（`list_repo_commits` 首个 commit_id + `--revision` 锁同点全件）防 "boot vs sync-logic push 竞速" 拉旧池（boot#4 15:30Z Pull 8 员旧池事件签名实证）。此为 omn-merge 现役实测法；Nexus 侧 B 路径（抄 omn-merge 补 Nexus）探索半程暂停、三分歧悬决（挂载形态 Volume vs hf download+cp vs Bucket rw），文件不据此断言 Nexus 已落地该根治。Nexus 现役 a142da9 改采 Bucket rw `/data` 单挂，逻辑层为 sync 覆盖、无此锁竞速路径（与修正项②一致），本文留存模板法供后续比对不误断既有。
- **双轨灾备（方案 C，含 litestream 边界警示）**：原拟 SQLite 留 ephemeral POSIX（μs fsync）+ R2 Litestream 10s 增量 + Dataset CommitScheduler 5min 全量互不干扰。**修正项⑧纠错**：litestream 仅复 SQLite WAL、Postgres 误配——Nexus 主状态在 Supabase PostgreSQL，**litestream 跑不起、不可照样配**；正解 = Supabase 自带 daily backup + PITR（Pro+ RPO 2min）/ 免费档 `supabase db dump` 入 R2 四桶之一。Dataset CommitScheduler 全量机制亦需对齐修正项①②：Dataset 强制只读、有 Full Git history 作 git 版本化回滚锚（改逻辑 = 1 commit = 1 不可变回退锚，datasets revision 接 commit SHA/git tag）；Bucket 改逻辑 = sync 覆盖无锚可回退（"None mutable,overwrite-in-place"），故灾备回滚锚只能落在 Dataset/Repo 侧，Bucket 专职 rw 运行态写件不充当回滚源。
