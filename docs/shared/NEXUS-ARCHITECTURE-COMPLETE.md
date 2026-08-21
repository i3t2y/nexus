# Nexus 完整架构方案 (第一性原理)

> **目标读者**: Gork / 任何 AI 接手人。读完此文档即握 Nexus 系统全貌、设计决策根因、可据此修正完善优化。
> **性质**: 第一性原理驱动，每个架构决策从"为什么做"而非"做了什么"出发。
> **提炼源**: 仓内 100+ 文档 + git log + HF Bucket 实证 + 60+ 篇记忆件 + 源码核证。
> **版本**: 2026-08-22

---

## 目录

1. [核心设计约束与原则](#1-核心设计约束与原则)
2. [永续四层分离架构](#2-永续四层分离架构)
3. [持久化三层架构](#3-持久化三层架构)
4. [HF Storage Bucket 双用途](#4-hf-storage-bucket-双用途)
5. [R2 快照备份架构](#5-r2-快照备份架构)
6. [五 Space 拓扑与通信](#6-五-space-拓扑与通信)
7. [部署拓扑与环境变量体系](#7-部署拓扑与环境变量体系)
8. [CI/CD 链与重建闸门](#8-cicd-链与重建闸门)
9. [安全模型与凭证管理](#9-安全模型与凭证管理)
10. [已知问题、风险与演进路线](#10-已知问题风险与演进路线)

---

## 1. 核心设计约束与原则

### 1.1 不可变约束

Nexus 运行在 **HF Docker Space** 上，以下约束是架构设计的根本前提：

| 约束 | 影响 | 来源 |
|------|------|------|
| HF Space 重启即丢本地盘 | 任何本地文件（state.db、日志、配置）重启后不存在 | HF 平台行为 |
| HF Space 重建触发付费墙 | 每次 git push 触 rebuild = 消耗 1 次付费配额 | 用户实证 |
| HF Docker Space 无 ZeroGPU 免费 | 3 个下游 Docker Space 需 PRO 或 Team 套餐 | HF 定价页 |
| HF 祖父 Docker 仅三席 | sonoke/h + nmem/memlg + nonoke/omn 已占满，新建 Docker 触发付费墙 | 2026-08-21 实证 |
| HF 封 IM 域 DNS 解析 | `api.telegram.org` / `discord.com` DNS 解析被拒 | 2026-08-02 实证 |
| HF Bucket /data 挂载为 FUSE 文件系统 | SQLite WAL 在 FUSE 上并发崩溃 | 2026-08-05 实证 |
| Neon Free 100 CU-hours/月上限 | 长连接方案超限，需 HTTP /sql 短请求 | 2026-08-14 实证 |
| GitHub Actions 免费无限（public repo） | CI/CD 可自由使用 | GitHub 政策 |

### 1.2 三个核心设计原则

**原则一：确定性 > 灵活性**
- 镜像层钉死 GHCR tag，不依赖 HF build 系统的动态解析
- 三文件（Dockerfile/README/start.sh）首切后永不改动，以防 rebuild 触发付费墙
- 逻辑层可任意改，因为只走 Bucket sync + Restart，不触发 rebuild

**原则二：外部持久化 > 本地存储**
- 一切重要状态走外部 DB（Neon）或对象存储（R2/Bucket）
- 本地盘只做运行时缓存和 SQLite state.db（重启丢，靠 Bucket 快照恢复）
- 丢失窗口设计：Neon 主路 600s、R2 副路 1800s、state.db 300s

**原则三：异步 > 同步**
- 长任务走 task_queue + FOR UPDATE SKIP LOCKED poll
- 短工具调用走 plugin 直调下游 Space
- 两轴编排：Hermes 对话入口 + LangGraph 工作流

### 1.3 组件总览

```
Nexus = 3 个 HF Docker Space + 1 个 CF Worker + 3 个外部服务

HF Space:
  sonoke/h (Hermes)     — 入口/路由/调度/IM (三件套宿主, 唯一热大脑)
  nmem/memlg (Memgraph) — 冷备 Hermes (同镜像/同 Bucket/同 Neon-R2, 暂停态)
  nonoke/omn (OmniRoute) — 模型路由 (339+ provider, 不是备脑)

CF Worker:
  tele.nexush.cc.cd     — IM 反代 (绕 HF DNS 封)
  workers/gateway        — Space 间统一入口 (仅 omn + 可选备机探测)

外部服务:
  Neon Postgres          — 主路持久化 (mem0 向量 + 四表)
  Cloudflare R2          — 副路灾备快照 (CAS 租约 + 两 tar 覆盖)
  GHCR (GitHub)          — nexus-base 镜像
```

---

## 2. 永续四层分离架构

### 2.1 问题：HF 付费墙

HF Docker Space 的 **rebuild 是消耗品**：
- 每次 git push 触 rebuild（计入付费配额）
- 改 hardware 触 rebuild（不可逆收费）
- pause 后 restart 可能 403 永锁
- 重建代价高（平台锁死、付费墙不确定）

但用户需要**频繁改代码**（逻辑层日改数次）。

**矛盾**：频繁改代码 vs 频繁触发付费墙 rebuild。

### 2.2 解法：四层分离

将整个系统按**改动频率**和**触发 rebuild 与否**拆成四层：

```
┌─────────────────────────────────────────────────────┐
│ 层1: 镜像层 (GHCR nexus-base)    改动频率: 月级      │
│  改依赖/升 Python 版本 → 本地 build + push GHCR     │
│  → 覆盖 :stable tag (不触 HF rebuild)                │
├─────────────────────────────────────────────────────┤
│ 层2: 环境层 (HF git 三文件)       改动频率: 永不      │
│  Dockerfile + README.md + start.sh                  │
│  首切后永不改 (墓碑)                                 │
│  ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable      │
├─────────────────────────────────────────────────────┤
│ 层3: 逻辑层 (HF Bucket /data)     改动频率: 日常      │
│  app/ + scripts/ + libs/ + plugins/                 │
│  sync-logic-bucket.sh 推 → Restart (不触发 rebuild)  │
├─────────────────────────────────────────────────────┤
│ 层4: 配置层 (HF Secrets)          改动频率: 按需      │
│  API keys / 凭证 / 模型配置                          │
│  HF Dashboard 改 → Restart (不触发 rebuild)          │
└─────────────────────────────────────────────────────┘
```

**关键洞察**：三层（镜像/逻辑/配置）的改动都不触发 HF rebuild，**只有环境层（git push）会触发**。把环境层设为墓碑后，日常所有改动都不碰付费墙。

### 2.3 四层详解

#### 层1：镜像层 (GHCR nexus-base:stable)

- **构建**：`docker/nexus-base.Dockerfile` + `docker/requirements-base.txt`
- **内容**：Python 3.11-slim + hermes-agent v2026.8.18 + 所有 pip 依赖 + prebuilt web_dist + 自编 libsqlite3 3.53.4（防 WAL-reset bug）
- **Build 流程**（用户手动）：
  ```bash
  docker build -t ghcr.io/i3t2y/nexus-base:stable -f docker/nexus-base.Dockerfile docker/
  docker push ghcr.io/i3t2y/nexus-base:stable
  ```
- **升级流程**：改 `requirements-base.txt` → 本地 build + push GHCR 覆盖 :stable → HF README 一字符改 + git push 触 rebuild（唯一一次 rebuild）
- **Dockerfile 关键细节**：
  - `ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable`（默认值填真 owner，否则 buildkit 报 InvalidDefaultArgInFrom）
  - `FROM ${BASE_IMAGE}`（HF 可传 ARG 覆盖，但默认值兜底）
  - `ARG BASE_IMAGE`（FROM 后重声明继承值）
  - `ENV BASE_IMAGE=${BASE_IMAGE}`（转存 env，start.sh 可 echo 排查）

#### 层2：环境层 (HF git 三文件)

- **内容**：仅 `Dockerfile` + `README.md` + `start.sh`
- **Dockerfile 墓碑形态**：仅 5 行（ARG + FROM + ARG 重声明 + COPY start.sh + CMD）
- **start.sh 瘦引导**：只做三件事：
  1. 等 `/data` 挂载就绪（`wait_for_bucket_mount`）
  2. `bootstrap_from_bucket` 拉逻辑层
  3. `source real-start.sh`（真逻辑在 Bucket 里）
- **首切顺序**（不可颠倒）：
  1. 本地 build nexus-base 推 GHCR :stable
  2. HF 建 Storage Bucket + 挂 /data
  3. Restart 验挂载通路（旧镜像）
  4. `sync-logic-bucket.sh` 推逻辑进 Bucket
  5. **手动 git push HF repo**（唯一一次 rebuild）
  6. 新镜像启动 → wait-for-mount → uvicorn import 成功

#### 层3：逻辑层 (Bucket /data)

- **内容**：`app/main.py` + `scripts/` + `libs/` + `plugins/` + `real-start.sh` + 三 daemon 持久化脚本
- **同步方式**：`scripts/sync-logic-bucket.sh`
- **PYTHONPATH**：`/data/libs`（`from storage import` / `from shared.gateway import` 顶层包）
- **uvicorn app-dir**：`--app-dir /data`（app 包从 /data/app 解析）
- **Boot 流程**（real-start.sh, 2026-08-09 方案 C 瘦引导）：
  ```
  start.sh (thin, 镜像内)
    → wait_for_bucket_mount (/data rw)
    → bootstrap_from_bucket(): hf buckets sync → /data/
    → source real-start.sh (来自 Bucket /data/scripts/)
      → mkdir /opt/data/{.hermes,logs} (本地盘)
      → restore_home_files.py (Bucket home-backups → /opt/data/.hermes/)
      → restore_state.py (Bucket state-backups → /opt/data/.hermes/state.db)
      → stage two plugins (Bucket scripts/plugins/ → HERMES_HOME/plugins/)
      → config.yaml: 有则 retained, 无则 seed template
      → nohup persist_to_neon.py (后台, 600s)
      → nohup persist_to_r2.py (后台, 1800s)
      → nohup state_db_uploader.py (后台, 300s)
      → nohup home_files_uploader.py (后台, 600s)
      → nohup keepalive.py (后台, 4min)
      → exec hermes 主进程 (python -m app.main:boot)
        → daemon thread 1: asyncio.run(start_gateway) [gateway + api_server + IM]
        → daemon thread 2: web_server.start_server(7860) [dashboard SPA]
        → main thread: while sleep (任一 daemon 死则 SystemExit 1)
  ```

#### 层4：配置层 (HF Secrets)

- 所有 API key/token/password 走 HF Secrets，不入 git
- `NEXUS_AUTH_MODE` 空=生产 fail-closed；`dev`=本地免鉴权
- 鉴权 header 分两层：`Authorization` 留给 HF 层，下游用 `X-Nexus-Key`

### 2.4 首切顺序（鸡生蛋问题）

**不可颠倒**，否则服务不可用：

```
Step 0: 本地 build nexus-base 推 GHCR :stable     ← 否则 HF rebuild 拉不到 base
Step 1: HF 建 Bucket + 挂载 /data                ← 否则 /data 不存在
Step 2: Restart 验挂载通路（旧镜像）                ← 先验挂载再倒逻辑
Step 3: sync-logic-bucket.sh 推逻辑进 Bucket      ← 否则 /data 空
Step 4: ★手动 git push HF repo（唯一 rebuild）    ← 付费墙窗口
Step 5: 新镜像启动 → import 成功                    ← 验证
```

### 2.5 依赖升级场景

改 pip 依赖 → 改 `docker/requirements-base.txt` → 本地 build + push GHCR 覆盖 :stable → HF README 一字符改 + git push 触 rebuild（唯一一次 rebuild）。Dockerfile 永不动。

---

## 3. 持久化三层架构

### 3.1 问题：HF Space 无状态

HF Docker Space 的核心架构约束：**容器重启后本地盘清零**。这意味着：
- SQLite state.db 重启丢
- 日志重启丢
- 配置重启丢

但用户期望：
- AI 记忆跨重启保留
- 对话历史跨重启保留
- 配置/设置跨重启保留

**矛盾**：无状态容器 vs 有状态持久化需求。

### 3.2 解法：三层分离

按**数据重要性**和**丢失容忍度**将持久化拆成三层：

```
┌─ 第1层: 主路 (Neon Postgres) ──────────────────────┐
│  丢失容忍: 0 (不可接受)                               │
│  内容: mem0 向量记忆 + 四表结构化状态                  │
│  方式: HTTP /sql 600s                                │
│  恢复: Neon 自带持久化 (R2 副路灾备)                   │
├─────────────────────────────────────────────────────┤
│  ┌─ 第2层: 副路 (Cloudflare R2) ──────────────────┐ │
│  │  丢失容忍: 30min (可接受)                         │ │
│  │  内容: 四表 JSON 快照 + manifest                  │ │
│  │  方式: persist_to_r2.py 1800s                    │ │
│  │  恢复: restore_from_r2.py R2→Neon                │ │
│  └─────────────────────────────────────────────────┘ │
├─────────────────────────────────────────────────────┤
│  ┌─ 第3层: 配置 (HF Bucket home-backups) ─────────┐ │
│  │  丢失容忍: 5min (可接受)                          │ │
│  │  内容: state.db + config.yaml + .env + SOUL.md   │ │
│  │  方式: state_db_uploader 300s / home_files       │ │
│  │         _uploader 600s                           │ │
│  │  恢复: restore_state.py / restore_home_files.py  │ │
│  └─────────────────────────────────────────────────┘ │
└─────────────────────────────────────────────────────┘
```

### 3.3 主路：Neon Postgres (HTTP /sql)

**为什么选 Neon**：
- Serverless Postgres，scale-to-zero，免费 100 CU-hours/月
- pgvector 支持（768/1536 维向量搜索）
- 与 Supabase 同源（Postgres），迁移成本低
- 对比 Supabase：无 7 天自动暂停风险

**为什么用 HTTP /sql 而非 psycopg2 直连**：
- psycopg2 长连接 = ~180 CU-h/月 → 超免费额度
- HTTP /sql 短请求 = ~0.5-3 CU-h/月 → 远低于额度
- 每次 POST 完即断 → Neon 自然 scale-to-zero
- 端点：`POST https://{host}/sql`，header `Neon-Connection-String: postgresql://...`
- **禁止任何定时 SQL 心跳**（15s/4min 打 Neon = 不让 Neon 睡，CU 直线涨）

**主路 4 表**（`persist_to_neon.py` 周期 600s 写，合并为 dirty + 10min + TERM 一次打包）：
- `agent_states` — 对话状态 (thread_id PK, state jsonb)
- `task_logs` — 任务日志 (bigserial, 业务索引)
- `long_memory` — 长期记忆 (key PK, value jsonb)
- `skills_index` — 技能索引 (skill_name PK)

**注意**：四表不是记忆真相。**真相源 = Mem0（向量语义）+ MEMORY.md（文件人设）+ skills（技能）+ task_queue（任务状态）**。四表是辅助持久化层，丢失可从 R2 恢复。

**mem0 向量记忆**（进程内 oss，不经过 memlg HTTP）：
- Hermes agent → OSSBackend → pgvector（pooler 短 TCP，用完即关）
- 嵌入维数 768 或 1536（**禁用 2048**，否则撑爆 0.5GB 盘）
- 搜索：hybrid (keyword + vector)
- `session_end` / 图收尾才 `add`（每句 add 烧 CU）

### 3.4 副路：R2 快照备份

**为什么需要副路**：
- Neon 是主路，但 Free 层有 CU 上限
- 若 Neon 数据丢失（CU 超限拒绝连接、用户误操作），需要可恢复的备份
- R2 免费层 10GB + 1000万 Class A 操作/月 → 零成本副路

**为什么 manifest-only 不进 DB**：
- 旧架构用 `backup_snapshots` 表存元数据，但 Neon 删此表
- 元数据（sha256/bytes/rows）全放 R2 `_manifest.json`
- 不倒退 Neon schema，省 CU-h

**R2 快照内容**（2026-08-22 snapshots/<ts>/ 不可变 blob）：
- `snapshots/<ts>/agent_states.json` + sha256
- `snapshots/<ts>/task_logs.json` + sha256 (LIMIT 10000)
- `snapshots/<ts>/long_memory.json` + sha256
- `snapshots/<ts>/skills_index.json` + sha256
- `MANIFEST.json` (gen/ts/objects.*.key, 纯指针, 无 CAS 无租约)

**恢复流程**（`restore_from_r2.py`）：
1. `--list`：读 R2 manifest 看各表最新快照
2. `--verify-only`：复算 sha256 比对 manifest
3. `--table <name>`：R2 读 → Neon HTTP /sql INSERT ON CONFLICT DO UPDATE
4. `--all`：全表恢复
5. 安全保护：空快照跳过、sha256 不符跳过、task_logs (bigserial) 不写回

### 3.5 配置层：HF Bucket 快照（降为可选镜像）

**为什么需要配置层**：
- state.db 重启丢（本地盘清零）
- config.yaml 重启丢（dashboard 编辑的设置）
- .env / SOUL.md / MEMORY.md 重启丢

**2026-08-21 收口**：热路径改 R2 两 tar.gz + MANIFEST CAS。Bucket 快照降为可选镜像，不再作为主热备。

**两脚本（仍保留，但非主热路径）**：
- `state_db_uploader.py`：周期 300s `sqlite3 backup API` 一致快照 → Bucket `state-backups/`
- `home_files_uploader.py`：周期 600s 增量（mtime+size 跳）→ Bucket `home-backups/`

### 3.6 丢失窗口分析

| 路径 | 周期 | 丢失窗口 | 场景 |
|------|------|---------|------|
| Neon 主路 | 600s | 10min | hermes 内部状态未持久化时崩溃 |
| R2 副路 | 1800s | 30min | 四表快照未上传时 Neon 挂 |
| state.db 快照 | 300s | 5min | 本地盘 state.db 未上传时崩溃 |
| 正常 shutdown | 同步 | 0 | SIGTERM trap 触发强制最后 sync |

### 3.7 已知持久化问题

1. **Neon Free 100 CU-hours 天花板**：HTTP /sql 短请求勉强够（~0.5-3 CU-h/月），但 mem0 pgvector 查询 CU 消耗大。并发查询多时超限拒连。候选：升 Launch $19/mo（500 CU-h）或迁 SQLite 方案。

2. **R2 30min 丢失窗口**：persist_to_r2.py 默认 1800s 周期。对对话场景可接受，对 task_queue 可能丢任务。可缩到 300s（代码改）。

3. **mem0 向量记忆备份有限**：只有主路 Neon memories 表，2026-08-21 收口计划加低频逻辑备份（每天或每 50 次 add，HTTP `/sql` `COPY`/`SELECT` 成 JSON 上 `neon-json/memories.json`）。pgvector 数据比 JSON 复杂，恢复难度大。若 Neon 数据丢失，mem0 记忆部分不可恢复。

4. **state.db 崩溃窗口**：若 HF Space 异常崩溃（SIGKILL）且未触发 shutdown trap → 最后 5min 数据丢。

---

## 4. HF Storage Bucket 双用途

### 4.1 问题：逻辑层存哪

永续四层分离的核心问题是：**逻辑层（常改）存哪，才能不触发 rebuild？**

候选方案：
- Dataset（HF git 类仓库）：只读（EROFS），不可写运行时
- Bucket（HF 对象存储，FUSE 挂载）：读写，运行时 live mount
- 两者组合

### 4.2 决策：Bucket 全包，Dataset 退役

2026-08-01 第一性原理终审（用户拍板）后，Bucket 全胜：

| 维度 | Dataset | Bucket |
|------|---------|--------|
| 运行时读写 | ❌ EROFS 只读 | ✅ RW FUSE 挂载 |
| 配额 | <100k 文件/commit | 无限制（官方未文档上限） |
| 计费 | 同配额表 | 纯 per-TB/月，无操作费 |
| 重启持久 | ✅ | ✅（挂载配置存 Space settings） |
| rebuild 影响 | 不变 | 独立 entity，git push 改不动 |
| 无 git 全套 | 继承 HF git | ❌ 无版本化/回滚 |

**最终判**：Bucket 全包，Dataset 退役。唯一活口（HF 域内 git 回滚锚）已被 GitHub 私库对冲。

### 4.3 Bucket 双用途

Bucket 在 Nexus 中承担两个角色：

**角色 A：运行时挂载（runtime）**
- FUSE 挂载到 `/data`，rw 模式
- `hf buckets sync` 在 boot 期拉逻辑层
- PYTHONPATH 指向 `/data/libs`
- uvicorn `--app-dir /data`
- 日常改代码：`sync-logic-bucket.sh` 推 → Restart

**角色 B：备份目标（backup）**
- `state-backups/` — state.db 快照（state_db_uploader 300s）
- `home-backups/` — 配置快照（home_files_uploader 600s）
- `plugins/` — 两 plugin 源（boot 期 stage 到 HERMES_HOME/plugins/）

**Bucket 架构风险**：
- 单点故障：Bucket 服务本身宕 → Space 起不来（无第二存储兜底）
- FUSE 文件系统：/data 挂载是 FUSE（Xet），非本地文件系统。SQLite WAL 在 FUSE 上已知崩（已治本：state.db 移 /opt/data 本地盘）
- 删除不可逆：删 object "immediate and permanent, no way to recover"。无版本化/软删除。靠 git 真源 + sync-logic-bucket.sh 重推恢复。

### 4.4 sync-logic-bucket.sh 协议

```bash
# 推逻辑到 Bucket
HF_TOKEN=... HF_OWNER=... bash scripts/sync-logic-bucket.sh

# 行为：
# 1. hf buckets sync "hf://buckets/${owner}/${bucket}" "$APP_DIR/" --no-delete
# 2. --no-delete: 不删 remote 多余文件（Bucket 双用途区，有 home-backups/state-backups）
# 3. 增量: rsync-style, size+mtime 比对, 仅传输变更
# 4. Xet CDC: 跨多次 sync CAS 全局去重同 chunk 仅存一次
```

### 4.5 Bucket vs Dataset 最终架构决定

```
hermes (sonoke/h):    单介质 Bucket + R2 (a142da9 现役已切)
memgraph (nmem/memlg): Bucket (nmem/logic 挂载, 冷备同源)
omniroute (nonoke/omn): Dataset (官方血统, 四件武器真跑)
langgraph/claude-code/codex: **永久取消重建** (P4: 无 Docker 四席; 执行器改 CNB curl NPC + 本机桥)
```

---

## 5. R2 快照备份架构

### 5.1 问题：Neon 无备份

Neon 是主路持久化，但：
- Free 层有 CU 上限，超限拒连
- 无内置跨区域备份
- 数据丢失场景存在（用户误操作、平台故障）

### 5.2 解法：R2 作副路灾备快照

**为什么选 R2**：
- 免费层 10GB + 100万 Class A 操作/月（**注意：官方免费是 100 万 Class A + 1000 万 Class B**，文档既往 1000 万为误）
- S3 兼容 API（boto3 直接用）
- 与 Cloudflare 生态整合（Worker 同账户）
- 对比 HF Bucket：R2 有版本化/生命周期管理

**数据流**：
```
Neon (主路) ──(HTTP /sql 读)──→ persist_to_r2.py ──(boto3 写)──→ R2
  ↑                              ↓
  └──── restore_from_r2.py ──────┘
```

**2026-08-22 收口修正**：R2 简化为 snapshots/<ts>/ 不可变 blob + MANIFEST.json 纯指针。
同时只开一台 Hermes → 不需要 CAS 租约。恢复端读 MANIFEST → objects.*.key → 下载即可。
- `MANIFEST.json` = gen/ts/objects.*.key（无 fence 无 space_id 无 etag）
- 4 表 × 1 次 PUT / 周期 = 192 Class A/天 << 免费额 1000万/月

### 5.3 manifest-only 设计

**为什么 manifest-only 不进 DB**：
- 旧架构：`backup_snapshots` 表存元数据（sha256/bytes/rows/updated_at）
- 新架构：元数据全放 R2 `_manifest.json`
- 好处：不倒退 Neon schema（七表已定稿）、省 CU-h、恢复时不依赖 Neon 可用性

**MANIFEST 结构（2026-08-22 snapshots/<ts>/ 版）**：
```json
{
  "gen": 7,
  "ts": "2026-08-22T10:00:00Z",
  "objects": {
    "agent_states": {
      "key": "snapshots/2026-08-22T10:00:00Z/agent_states.json",
      "sha256": "abc...",
      "bytes": 12345,
      "rows": 100
    },
    "task_logs": { "key": "...", "sha256": "...", "bytes": ..., "rows": ... },
    "long_memory": { "key": "...", "sha256": "...", "bytes": ..., "rows": ... },
    "skills_index": { "key": "...", "sha256": "...", "bytes": ..., "rows": ... }
  }
}
```

### 5.4 恢复验证流程

**安全保护**：
1. 空快照跳过（防把表整体清空）
2. sha256 复算比对（防静默损坏覆盖好数据）
3. task_logs (bigserial) 不写回（代理键冲突）
4. `--verify-only` 模式：只校验不写

**恢复命令**：
```bash
# 只读：看 R2 内各表最新快照
python restore_from_r2.py --list
# 仅校验（复算 sha256 比对 manifest）
python restore_from_r2.py --table agent_states --verify-only
# 恢复单表（幂等 upsert，可重跑）
python restore_from_r2.py --table agent_states
# 恢复全部业务表
python restore_from_r2.py --all
```

### 5.5 丢失窗口

- 默认 1800s 周期 → 最多 30min 数据丢失
- 可缩到 300s（代码改，但 R2 Class A 操作量×6）
- Class A 写：4 表 × 3 步/次 × 48 次/天 ≈ 9600/天 << 免费额 100万/月

---

## 6. 三 Space 拓扑与通信

### 6.1 问题：多 Space 如何协作

HF Docker Space 是独立容器，彼此不共享进程空间。但 Nexus 需要：
- 一个入口接收用户消息
- 多个 Space 分担不同职能（入口/模型路由/冷备）
- Space 间安全通信

**P4 约束**：祖父 Docker 三席已满，**禁止再新建 Docker Space**。执行器改走 CNB curl NPC + 本机桥，不占 HF 席。

**冷备降级**：nmem/memlg 2026-08-22 降级为可选，单 Space 部署不依赖此。非安可项目无需冷备。

### 6.2 三 Space 布局（收口版）

```
┌─ 外部世界 ──────────────────────────────────────┐
│  用户 → Telegram                                  │
│    → CF Worker 反代 (tele.nexush.cc.cd)           │
│      → 防火墙: ALLOWED_TOKENS 白名单               │
└─────────────────────────────────────────────────┘
                      │
                      ▼
              ┌─ sonoke/h  HERMES 热（三件套同进程）──┐
              │  四层：墓碑镜像 + /data 逻辑 + Secrets   │
              │  Hermes boot + LangGraph 库 + Mem0 库   │
              │  persist daemons + SIGTERM flush         │
              └──────────────┬──────────────────────────┘
                 │           │            │
                 ▼           ▼            ▼
          Neon HTTP/sql   Neon 短TCP    R2 S3
          task_queue      Mem0 pgvector home.tar.gz
          可选四表         checkpoints   sessions.tar.gz
                          (直连,用完关)  MANIFEST CAS
                 │
                 ▼
          nonoke/omn  OmniRoute（模型网关，不是大脑）
                 保持 RUNNING；cron 可轻量保活

              nmem/memlg  暂停 = Hermes 冷备
              （同 GHCR + 同 Bucket 逻辑 + 同 Neon/R2）
              无 cron；密钥可预填；抢不到 MANIFEST 租约不准写
```

### 6.3 Space 职责

| Space | 职能 | 为什么独立 |
|-------|------|-----------|
| Hermes | 唯一大脑：入口/路由/IM/三件套宿主 | 必须常热，与用户交互 |
| OmniRoute | 模型路由（339+ provider 聚合） | 独立账号，不属仓内，不是备脑 |
| Memgraph | **冷备 Hermes**（暂停态，不运行时） | 祖父席位不可浪费，改灾备用 |

**同时只开一台 Hermes。** OmniRoute 可常开（无共享 home）。Memgraph 与主 Hermes **禁止双 RUNNING**。**三下游（langgraph/claude-code/codex）永久取消重建**，执行器改走 CNB curl NPC + 本机桥。

### 6.4 通信模式

**请求流**（用户 → Telegram → Hermes → 推理）：
```
用户 → Telegram
  → CF Worker 反代 (tele.nexush.cc.cd)  # 绕 HF DNS 封
    → hermes telegram polling (PTB, custom base_url)
      → hermes agent loop
        → omniroute (nonoke-omn /v1/chat/completions)
          → nvidia/z-ai/glm-5.2 推理
        ← 语义判 → 命中 nexus plugin tool
          → INSERT task_queue kind=npc (CNB CodeBuddy 派发)
          ← 或 agent 内置函数 (无需下游 Space)
        ← 回复文本 → Telegram 消息
```

**持久化流**（Hermes → Neon + R2，合并为 dirty + 10min + TERM 一次打包）：
```
Hermes 内部 → 四表 (agent_states/task_logs/long_memory/skills_index)
  → persist (dirty + 10min + TERM) → Neon HTTP /sql + R2 两 tar.gz

Hermes 向量记忆 → OSSBackend → pgvector pooler 短 TCP (用完即关)
  → session_end / 图收尾才 add

Hermes 配置 → R2 home.tar.gz (strip .env/auth.json/mem0.json.password/*.pid/logs/cache/)
Hermes state.db → R2 sessions.tar.gz (sqlite3 .backup, 禁止 cp state.db/WAL)
```

**Boot 流**（HF 启动 → 服务就绪）：
```
HF Space 启动
  → Dockerfile CMD → start.sh (thin)
    → wait_for_bucket_mount (/data rw)
    → bootstrap_from_bucket: hf buckets sync → /data/
    → source real-start.sh
      → mkdir /opt/data/{.hermes,logs}
      → restore R2 MANIFEST → staging → mv home/sessions   # 原 restore_* 改读 R2
      → rm -f .env auth.json *.pid
      → 合成 .env（Secrets）
      → 注入 mem0 密码
      → CAS 抢 MANIFEST；失败 → 不写、standby
      → nohup persist（--once 可重入，dirty + 10min + TERM 一次打包）
      → exec hermes main.py:boot
        → daemon thread 1: gateway + api_server + IM
        → daemon thread 2: dashboard SPA :7860
        → main thread: while sleep
```

**Shutdown 流**（SIGTERM → 优雅关闭）：
```
HF 发 SIGTERM
  → real-start.sh trap handler
    → kill -TERM hermes + daemons
    → persist --once ×（home, sessions, 可选 neon/r2 json）
    → sleep 8-12
    → exit 0
  → hermes 主进程收 SIGTERM
    → gateway graceful shutdown
    → dashboard graceful shutdown
  → HF 发 SIGKILL (超时后)
```

### 6.5 CF Worker Gateway

**为什么需要 Gateway**：
- Hermes 是私有 Space，需要 `HF_TOKEN` 鉴权
- 需要防 SSRF（只允许访问 `/health` `/run` `/complete` 等白名单路径）
- 需要保活探测（OmniRoute 48h 休眠唤醒）

**2026-08-21 收口**：三下游取消后，Gateway 收缩为只留 omn +（可选）备机探测。不要 ping 已废 URL。

**Gateway 路由**：
- `/health` — 存活探测（无鉴权）
- `/probe` — 探测全部下游 Space（需 `NEXUS_API_KEY`）
- `/route` — 路由请求到下游 Space（需 `NEXUS_API_KEY`）
- SSE 防：`isAllowedPath` 验证路径精确匹配白名单

**鉴权 header 分层**：
- 入站：`Authorization: Bearer NEXUS_API_KEY`（网关层）
- 出站到下游：`X-Nexus-Key: Bearer NEXUS_API_KEY`（app 层）+ `Authorization: Bearer HF_TOKEN`（HF 层）

### 6.6 task_queue 消费模型

**为什么 task_queue 在 Postgres 不在内存**：
- 跨重启任务不丢
- 多消费者竞争（FOR UPDATE SKIP LOCKED）
- 与 Neon 持久化统一

**Stage A 表结构**（2026-08-18 统一扁平表，2026-08-21 收口补 claim 字段）：
```sql
CREATE TABLE IF NOT EXISTS task_queue (
  task_id      text PRIMARY KEY,
  task         text,                    -- 人读摘要兜底
  user_id      text,
  status       text NOT NULL DEFAULT 'pending',
  -- pending | claimed | running | completed | failed | dead
  -- (2026-08-21 收口: 加 claimed + dead, 删 claude_code 枚举)
  kind         text NOT NULL DEFAULT 'generic',
  -- generic | graph | npc | pi | dsh
  -- (claude_code 取消重建; workbuddy_npc 路废, Gork 2026-08-18 裁决)
  input        jsonb       NOT NULL DEFAULT '{}',
  output       jsonb,
  result       text,                    -- 兼容旧读端
  attempts     int         NOT NULL DEFAULT 0,
  created_at   timestamptz DEFAULT now(),
  updated_at   timestamptz DEFAULT now(),
  completed_at timestamptz
);
```

**消费模型**：
```
写端 (act delegate 调用点) → INSERT INTO task_queue (status='pending')
读端 (GET /worker/tasks)   → SELECT ... WHERE status='pending'
                              ORDER BY created_at LIMIT 1
                              FOR UPDATE SKIP LOCKED
更新 (PATCH /tasks/{id})   → SET status='running'|'completed'
                              (+ output jsonb, completed_at now())
```

---

## 7. 部署拓扑与环境变量体系

### 7.1 Space 配置表

| Space | HF 账号 | Space 名 | 镜像 | 类型 | 端口 | 状态 |
|-------|---------|---------|------|------|------|------|
| Hermes | sonoke | h | `ghcr.io/i3t2y/nexus-base:stable` | Docker | 7860 | ✅ RUNNING (唯一热脑) |
| Memgraph | nmem | memlg | `nmm0912/memgraph:latest` → **改 nexus-base:stable** | Docker | 7860 | ⏸ Pause → 冷备 |
| OmniRoute | nonoke | omn | OmniRoute 官方 | Docker | 7860 | ✅ RUNNING (模型路由) |
| langgraph/cc/codex | — | — | — | — | — | **永久取消重建** |

### 7.2 网络依赖图

```
Hermes (sonoke/h) — 唯一热脑
  ├─ → OmniRoute (nonoke/omn):         HTTPS /v1/chat/completions (模型推理)
  ├─ → Neon Postgres:                   HTTP /sql (四表持久化) + pgvector 短 TCP
  ├─ → Cloudflare R2:                   HTTPS S3 API (两 tar 快照 + MANIFEST CAS)
  ├─ → HF Bucket sonoke/logic:          hf buckets CLI (逻辑同步)
  └─ → Telegram (CF Worker 反代):      HTTPS tele.nexush.cc.cd (IM 轮询)

Memgraph (nmem/memlg) — 冷备态, 无运行时依赖
  (同镜像 + 同 Bucket + 同 Neon/R2; 无 cron; 抢不到 MANIFEST 租约不准写)

OmniRoute (nonoke/omn) — 模型路由, 非备脑
  └─ → cron-job.org (可选独立轻 ping, 与 Hermes 租约无关)
```

### 7.3 环境变量总表

**Hermes (sonoke/h) 必填**：

| 变量 | 用途 | 来源 |
|------|------|------|
| `GLM_API_KEY` | omniroute 鉴权 (zai provider) | HF Secret |
| `GLM_BASE_URL` | omniroute endpoint (必带 /v1) | HF Secret |
| `API_SERVER_KEY` | api_server 触发 + /v1/* Bearer 鉴权 (≥16 字符) | HF Secret |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` | dashboard 登录 | HF Secret |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` | dashboard 登录密码 | HF Secret |
| `HERMES_DASHBOARD_BASIC_AUTH_SECRET` | dashboard session 签名 | HF Secret |
| `HF_TOKEN` | Bucket 拉/推 + 私有 Space HF 层 | HF Secret |
| `HF_OWNER` | Bucket namespace | HF Secret |
| `NEXUS_LOGIC_BUCKET` | Bucket 名 (默认 logic) | HF Secret |
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` | Neon 连接 (主路+R2 副路共用) | HF Secret |
| `R2_ENDPOINT/ACCESS_KEY_ID/SECRET_ACCESS_KEY/BUCKET` | R2 灾备快照层 | HF Secret |
| `NEXUS_API_KEY` | 下游鉴权 (X-Nexus-Key) | HF Secret |
| `TELEGRAM_BOT_TOKEN` | Telegram bot token | HF Secret |
| `PORT` | 7860 | HF 自动注入 |
| `SPACE_AUTHOR_NAME` | hermes 知 owner | HF 自动注入 |

**Hermes 可选**：

| 变量 | 默认值 | 用途 |
|------|--------|------|
| `HERMES_MODEL` | (无) | cron 模型覆盖；**勿填裸名**（omn 400 Ambiguous） |
| `DASHBOARD_BIND_HOST` | 0.0.0.0 | dashboard 绑定地址（nexus 自造） |
| `TELEGRAM_PROXY` | (无) | Telegram 代理 (IP 封兜底) |
| `DISCORD_BOT_TOKEN` | (无) | Discord bot token (disabled) |
| `DISCORD_PROXY` | (无) | Discord 代理 |
| `NEXUS_AUTH_MODE` | (空=fail-closed) | `dev`=本地免鉴权 |
| `SYNC_INTERVAL_SEC` | 600 | 仅 persist_to_neon.py daemon 兼容（2026-08-22 改 --init 模式，不再跑定时 SQL） |
| `R2_SYNC_INTERVAL_SEC` | 1800 | R2 快照周期 |
| `KEEPALIVE_ENABLED` | 0 | **下游保活开关，2026-08-22 默认关**（只保留 cron 主 /health） |
| `HERMES_HOME` | /opt/data/.hermes | 本地盘 home 路径（2026-08-07 已定） |
| `MEM0_VECTOR_DIM` | 768 | 嵌入维数（768 或 1536，禁用 2048；2026-08-21 收口新增） |

**Memgraph (nmem/memlg) 可选冷备**（2026-08-22 降级可选，单 Space 部署不依赖此）：

| 变量 | 用途 |
|------|------|
| `ADMIN_API_KEY` | mem0 鉴权 |
| `POSTGRES_HOST/PORT/USER/PASSWORD/DB` | Neon 连接 (mem0 + task_queue) |
| `R2_*` 五件 | R2 MANIFEST CAS 租约 |
| `HF_TOKEN` / `HF_OWNER` | Bucket 同步 |

**全系统统一**：

| 变量 | 用途 | 所有 Space |
|------|------|-----------|
| `NEXUS_API_KEY` | 下游鉴权 (X-Nexus-Key) | 全 Space 同一把 |

---

## 8. CI/CD 链与重建闸门

### 8.1 问题：怎么安全地部署

四层分离后，每层有独立的部署路径：
- 镜像层改 → 本地 build + push GHCR
- 环境层改 → git push HF（触 rebuild 付费墙）
- 逻辑层改 → sync-logic-bucket.sh 推 Bucket（不触 rebuild）
- 配置层改 → HF Dashboard 改 Secrets（不触 rebuild）

但 CI 需要：
- 语法闸门：挡低级语法错误进 HF
- 同步闸门：挡逻辑层不一致
- 类型闸门：挡 TypeScript 类型错误

### 8.2 四工作流

```
GitHub i3t2y/nexus (public, Actions 无限免费)
  │
  ├─ docker-base.yml ──────────────── (manual trigger)
  │   → build+push GHCR :stable
  │   → 改依赖时手动触发
  │
  ├─ sync-hf-space.yml ────────────── (paths: hermes/space/**)
  │   → cp 三文件 + git push HF sonoke/h
  │   → 触发 HF rebuild (付费墙)
  │   → 仅首切/依赖升级时走
  │
  ├─ sync-check.yml ───────────────── (paths: hermes/** + memgraph/** + workers/**)
  │   → step1: sync-spaces --check (conditional skip, 待三 Space 重建)
  │   → step2: py_compile hermes + memgraph (语法闸门)
  │   → step3: cd workers/gateway; npm i; npx tsc --noEmit
  │
  └─ deploy-memgraph.yml ──────────── (paths: memgraph/**)
      → deploy-space:  memgraph/space/ → HF Space git (rebuild)
      → deploy-bucket: memgraph/bucket/ → HF Bucket sync (不 rebuild)
```

### 8.3 重建闸门规则

| 操作 | 触发 rebuild? | 安全措施 |
|------|--------------|---------|
| 改镜像层 (GHCR push) | ❌（覆盖 :stable tag） | 本地 build 验过才推 |
| 改环境层 (git push HF) | ✅（付费墙） | 用户手动明确同意 |
| 改逻辑层 (sync-logic-bucket) | ❌（Restart 即可） | sync-check.yml 语法闸门 |
| 改配置层 (HF Secrets) | ❌（Restart 即可） | 无 |
| 改 README 一字符 + git push | ✅（触发 rebuild） | 依赖升级唯一路径 |

### 8.4 红线铁律

1. **所有 git push 须经用户显式同意**（包括 origin/main 和 HF repo）
2. **三文件（Dockerfile/README/start.sh）不动**（除非架构升级）
3. **sync-logic-bucket.sh 默认 --no-delete**（防删 Bucket 双用途区）
4. **HF rebuild 不可自动触发**（必须用户手动）

---

## 9. 安全模型与凭证管理

### 9.1 鉴权链

```
┌─ 用户 → Telegram ──────────────────────────────────┐
│  Telegram bot token 鉴权 (PTB 层)                    │
│  CF Worker ALLOWED_TOKENS 白名单 (反代层)            │
└────────────────────────────────────────────────────┘
         │
         ▼
┌─ Hermes (sonoke/h) ───────────────────────────────┐
│  dashboard: BasicAuthProvider (USERNAME/PASS/SECRET) │
│  api_server: API_SERVER_KEY Bearer 鉴权              │
│  IM: bot token 鉴权 (Telegram/Discord)              │
└────────────────────────────────────────────────────┘
         │
         ▼
┌─ CF Worker Gateway ───────────────────────────────┐
│  NEXUS_API_KEY Bearer 鉴权 (Authorization header)   │
│  下游 Space 路由: 透传 X-Nexus-Key 或 Authorization  │
└────────────────────────────────────────────────────┘
         │
         ▼
┌─ 下游 Space ──────────────────────────────────────┐
│  NEXUS_API_KEY 鉴权 (X-Nexus-Key header)            │
│  fail-closed (缺 key 则 500, 不降级)                 │
└────────────────────────────────────────────────────┘
```

### 9.2 安全红线

1. **所有 API key/token/password 走 HF Secrets，不入 git**（`.env` 已 gitignore）
2. **`NEXUS_AUTH_MODE` 空=生产 fail-closed**；`dev`=本地免鉴权
3. **鉴权 header 分两层**：`Authorization` 留给 HF 层，下游用 `X-Nexus-Key`
4. **SSRF 防**：CF Worker `isAllowedPath` 精确匹配白名单，URL 编码 `%2e%2e` 拒
5. **Neon 无 RLS 机制**：靠连接串权限控制（非 row-level）
6. **旧 `WORKER_API_KEY` 泄漏**（gZixCwagvHt7JSJYDj89AA）：已随 MCP 孤儿块删，但旧 key 多处扩散，须 worker 侧 revoke+轮转

### 9.3 凭证清单

**现役凭证**（必须走 HF Secrets）：

| 凭证组 | 用途 | 归属 Space |
|--------|------|-----------|
| GLM_API_KEY / GLM_BASE_URL | omniroute 鉴权 | Hermes |
| API_SERVER_KEY | api_server 鉴权 (≥16 字符) | Hermes |
| BasicAuth 三件套 | dashboard 登录 | Hermes |
| HF_TOKEN | Bucket + 私有 Space | Hermes |
| POSTGRES_* 五件 | Neon 主路 + R2 副路 | Hermes + Memgraph |
| R2_* 五件 | R2 灾备快照 | Hermes |
| NEXUS_API_KEY | 下游鉴权 | Hermes + CF Worker |
| TELEGRAM_BOT_TOKEN | Telegram bot | Hermes |
| ADMIN_API_KEY | mem0 鉴权 | Memgraph |

**已退役凭证**（可从 HF Secrets 清除）：

| 凭证组 | 退役原因 | 退役时间 |
|--------|---------|---------|
| SUPABASE_URL / SERVICE_ROLE_KEY / ANON_KEY / DB_URI | Supabase → Neon 迁移 | 2026-08-17 |
| MEM0_PG_URI | mem0 SelfHostedBackend 替代 | 2026-08-17 |

---

## 10. 已知问题、风险与演进路线

### 10.1 技术债清单

| # | 债项 | 根因 | 影响 | 修复方向 |
|---|------|------|------|---------|
| 1 | Neon Free CU 天花板 | 100 CU-h/月不够 | 并发查询多时 Neon 拒连 | 升付费 $19/mo 或迁 SQLite |
| 2 | R2 30min 丢失窗口 | persist_to_r2.py 1800s 周期 | 最多 30min 数据丢失 | 缩周期到 300s (代码改) |
| 3 | ~~三 Space 待重建~~ | **永久取消 (P4: 无 Docker 四席)** | 执行器改 CNB curl NPC + 本机桥 | 取消重建, 不占 HF 席 |
| 4 | psycopg→httpx 改写 | 旧代码用 psycopg 直连 | memgraph 写端不统一 | Stage B 候选 |
| 5 | Stage B 本机桥 | 扫 pending+kind=npc 调 CNB CodeBuddy | 无 NPC 派发能力 | 实现 poll_worker_tasks.py 增强 |
| 6 | MCP pip 包缺 base | optional extra --no-deps 跳 | 无 MCP 工具 | 重 build base (付费墙) |
| 7 | WORKER_API_KEY 泄漏 | gZixCwagvHt7JSJYDj89AA 随孤儿块删但已扩散 | 安全风险 | 用户侧 revoke+轮转 |
| 8 | mem0 向量记忆无备份 | 只有主路 Neon memories 表 | 若 Neon 数据丢失不可恢复 | R2 快照 memories 表 |
| 9 | Shutdown trap 实战未验 | SIGTERM 路径本地测过但 HF 未实战 | 最后 flush 可能未触发 | HF 发 SIGTERM 验 boot log |

### 10.2 架构风险

| 风险 | 概率 | 影响 | 缓解 |
|------|------|------|------|
| HF Docker Space 平台变更 | 中 | 服务不可用 | 永续四层分离, 逻辑层在 Bucket, 可快速切平台 |
| HF Bucket FUSE 故障 | 低 | 逻辑层不可读, 服务不可用 | bootstrap 拉下后 /data 异常只影响 daemon, 主进程有缓存 |
| Neon 服务中断 | 低 | 持久化不可写, 服务降级 | R2 副路可恢复, 但实时写丢失 |
| Telegram API 封禁 HF IP 段 | 高 (已实证) | IM 不可用 | CF Worker 自定义域反代 ✅ (pending Worker 部署) |
| HF TOKEN 泄漏 | 低 | Bucket 数据泄露 | 最小权限 token, 定期轮换 |
| Bucket 单点故障 | 低 | Space 起不来 | 现 bootstrap 是 hf buckets sync while 自愈, 无第二存储 |

### 10.3 关键未决问题（待 Gork 裁决）

1. **持久化方案选择**：现态 Neon 升付费 vs SQLite 中心化（全迁 Bucket）vs 混合（mem0 留 Neon，其余迁 SQLite）。2026-08-21 收口暂维持 Neon HTTP /sql + Mem0 短 TCP
2. **mem0 向量搜索替代**：若迁 SQLite，pgvector 精度降级可接受否？
3. ~~三 Space 重建~~ **已取消**(P4 无 Docker 四席)
4. **Neon 升付费**：$19/mo Launch 层还是 $0 免费层硬扛？现态对话短 TCP 勉强够，若 CU 涨需升
5. **sync-logic-bucket.sh 活化**：改完逻辑后手动跑 vs CI 自动跑？风险：CI 自动跑可能推旧代码
6. **Bucket-HA 二线兜底**：R2 作逻辑层 fallback 要不要真做？

### 10.4 演进路线

**阶段 A（已完成 2026-08-22）** — 表结构统一 + 代码清理
- ✅ task_queue 扁平表统一（kind/input/output/attempts, Stage A）
- ✅ workers/gateway 源挪回根
- ✅ sync-logic-bucket.sh 路径对齐
- ✅ sync-check.yml 修活
- ✅ 三工作流入根活
- ✅ MCP nexus-worker 配置孤儿清
- ✅ 批3 fastapi 修复（0.115.6→0.133.1, 治 dashboard 崩循环）
- ✅ 完整架构文档（本文档）
- ✅ 收口版部署方案（2026-08-21 三件套合同）

**阶段 P0** — R2 简化 + SIGTERM 闭环（2026-08-22 完成）
- ✅ persist --once + TERM trap 实战（Gork SIGTERM 钩子 + 4 脚本 handler）
- ✅ R2 简化：supabase-snapshot/ → snapshots/<ts>/ 不可变 blob，移除 CAS 租约
- ✅ restore_from_r2.py 读 MANIFEST.objects 指针，兼容旧快照回退
- ✅ real-start.sh R2 restore 段 + persist-neon 改 --init 模式
- /health: 原生 SPA 自带，不碰 DB
- ✅ keepalive 默认关（KEEPALIVE_ENABLED=0）
- ⏳ sqlite .backup 一致快照；strip 密钥（.env/auth.json/mem0.json.password/*.pid/logs/cache/）

**阶段 P0.5** — 单 Space 部署收尾（2026-08-22 完成）
- ✅ 确认无 Neon 定时心跳（persist_to_neon.py 去 space_health + 改 --init）
- ✅ Mem0 切 oss pgvector（pooler 短 TCP，默认行为）
- ✅ embed 维数对齐（MEM0_VECTOR_DIM=768，禁用 2048）
- ✅ 冷备 nmem/memlg 降级可选，单 Space 部署不依赖

**阶段 P1** — NPC 派发 + 本机桥
- ⏳ psycopg→httpx /sql 改写（memgraph 写端统一）
- ⏳ poll_worker_tasks.py 增强：扫 pending+kind=npc → CNB CodeBuddy（OpenAPI curl 路）
- ⏳ act delegate 节点解析 kind='npc' 智能触发
- ⏳ kind=graph 异步长图增强（FOR UPDATE SKIP LOCKED poll）

**阶段 P2** — 持久化加固 + 清理
- ⏳ 短图进库；废 agent_states/long_memory 双写
- ⏳ memories JSON 低频上 R2（每天或每 50 次 add）
- ⏳ WORKER_API_KEY 轮转（已泄漏 gZixCwagvHt7JSJYDj89AA）
- ⏳ 持久化方案选型（Neon 升付费 vs SQLite 中心化）
- ⏳ R2 缩周期（1800s→300s）

**阶段 D（远期）** — 平台扩展
- ⏳ MCP pip 包补 base（重 build nexus-base）
- ⏳ CNB MCP 升（STDIO 路, 需 Node 重 build base）
- ⏳ 异地 Agent 路由（通过 CNB CodeBuddy 派发）

---

## 附录：架构决策索引

| 决策 | 日期 | 来源 |
|------|------|------|
| 永续四层分离 | 2026-07-28 | `nexus-hermes-bucket-perpetual` |
| Bucket 全包 Dataset 退役 | 2026-08-01 | `nexus-dataset-vs-bucket-first-principles` |
| Hermes 原生三组件替代自建 | 2026-08-02 | `nexus-4comp-strict-audit-2026-08-02` |
| R2 副路恢复（读源=Neon） | 2026-08-18 | `nexus-r2-neon-dual-snapshot-2026-08-18` |
| task_queue 扁平表（Stage A） | 2026-08-18 | `shiny-moseying-quasar.md` plan |
| Gork 裁决 workbuddy_npc 路废 | 2026-08-18 | `nexus-gork-arbiter-ruling-verified-2026-08-18` |
| Telegram CF Worker 反代 | 2026-08-05 | `nexus-hermes-telegram-cfworker-2026-08-05` |
| state.db 移 /opt/data 治 malformed | 2026-08-05 | `nexus-hermes-statedb-malformed-fix-2026-08-05` |
| 方案 C thin 引导 | 2026-08-09 | `nexus-hermes-plan-c-thin-bootstrap-2026-08-09` |
| SIGTERM shutdown trap | 2026-08-18 | `nexus-hermes-sigterm-trao-deploy-2026-08-18` |
| sync-spaces 留 old 待重建 | 2026-08-19 | `nexus-workflow-reorg-2026-08-19` |
| sync-check 死件修活 | 2026-08-21 | commit d455bd0 |
| Stage A commit | 2026-08-22 | commit 1f106b0 |
| **三件套生产部署收口版** | **2026-08-21** | **`nexus-3piece-deploy-contract-2026-08-21`** |
| §12 勘误取消三 Space 重建 | 2026-08-21 | 收口版合同 P4 |

---

**文档结束。Gork 可据此文档分析系统全貌，定位问题，提出优化方案。** 每个章节的"来源"字段可供追溯原始证据。