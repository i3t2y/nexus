# Nexus 完整方案（Upstash 仅作队列）

供重新规划用的可执行蓝图。原则：**Hermes 大脑 · 异步任务 · Upstash 热队列 · Neon 真相 · R2 大文件 · Mem0 语义记忆**。

---

## 1. 目标与边界

**目标**

- 多组件可分布（多 HF Space / 本机 Worker）
- 低成本、可保活
- 任务可追踪、可重试、可审计

**边界**

| 做 | 不做 |
|----|------|
| Upstash = **仅队列**（task_id + 少量元数据） | Redis 存业务详情 / 记忆 / 大 JSON |
| Neon = 任务真相 + Mem0 向量 + 结构化数据 | 用 Redis 替代 Postgres |
| R2 = 日志、产物、长文本 | 免费 HF 上跑 Claude Code / Codex 全量 |
| Hermes 统一路由与验收 | 组件之间进程级直连 |

---

## 2. 逻辑架构

```text
                    用户 / IM / Webhook
                            │
                            ▼
                    ┌───────────────┐
                    │    Hermes     │  规划 · 路由 · 验收 · 写记忆
                    │  (入口 Space) │
                    └───────┬───────┘
                            │
           ┌────────────────┼────────────────┐
           ▼                ▼                ▼
    Upstash Redis      Neon Postgres        R2
    (热队列 only)      (真相 + Mem0)      (产物/日志)
           │                ▲
           │  task_id       │  status/result
           ▼                │
    ┌─────────────┐   ┌─────┴─────┐
    │ LangGraph   │   │ Mem0 API  │  （可同 Space 或独立）
    │ Worker      │   │ add/search│
    └─────────────┘   └───────────┘
           │
           ▼（可选本机，非 HF）
    Claude Code / Codex / WorkBuddy
           │
           ▼
    Buddy NPC ←── CNB OpenAPI / Issue @npc
```

**组件角色**

| 组件 | 职责 |
|------|------|
| Hermes | 唯一编排入口；入队；读结果；调 Mem0；对外回复 |
| Upstash | `nexus:queue:*` 待办队列；可选延迟/死信 |
| Neon | `nexus_tasks` 全生命周期；Mem0 pgvector；可选 checkpoint |
| R2 | `artifacts/{task_id}/...` |
| LangGraph Worker | 消费 `kind=graph`；跑图；回写 Neon |
| Mem0 | 语义记忆 API（底层 Neon） |
| CC/Codex | **本机 CLI**，由 Hermes/本机 Worker 调，不进免费 Space |
| WorkBuddy | IM/队列桥，本机 |
| Buddy NPC | CNB 云端编码 |

---

## 3. 技术栈与账号

| 层 | 选型 | 说明 |
|----|------|------|
| 队列 | **Upstash Redis** Free | 256MB · ~50万 commands/月 · 1 DB |
| DB | **Neon** Free | 任务表 + `CREATE EXTENSION vector` |
| 对象存储 | **Cloudflare R2** | 主存储 |
| 记忆 | **Mem0** + Neon pgvector | 不经 Redis |
| 入口 | HF Space / 本机 Hermes | 尽量薄 |
| Worker | HF Space 或本机 | 只消费队列、跑对应 kind |

---

## 4. Upstash 队列约定（仅队列）

### 4.1 Key 设计

```text
nexus:queue:default          # List：主队列（RPUSH / BLPOP）
nexus:queue:graph            # 可选：按 kind 分流
nexus:queue:npc
nexus:queue:dlq              # 死信
nexus:processing:{task_id}   # 可选：处理中锁，TTL 900
```

**队列元素：只放短字符串（建议仅 task_id）**

```text
RPUSH nexus:queue:default "<uuid>"
```

禁止把 goal 全文、代码、日志放进 Redis。

### 4.2 推荐命令模式

**入队（Hermes）**

1. Neon `INSERT` status=`pending`
2. `RPUSH nexus:queue:{kind|default} task_id`
3. 返回 task_id 给用户

**出队（Worker）**

1. `BLPOP nexus:queue:default 30`（或按 kind）
2. Neon 将 status → `running`，写 `locked_at`、`worker_id`
3. 执行业务
4. 成功：Neon → `done` + result 指针；失败：重试或 `RPUSH` DLQ

**省 commands**

- 用 BLPOP，避免空转轮询
- 不把大 value 写入 Redis
- 状态查询走 Neon，不扫 Redis

---

## 5. Neon Schema（真相层）

```sql
-- 任务主表
CREATE TABLE nexus_tasks (
  id            UUID PRIMARY KEY,
  kind          TEXT NOT NULL,          -- graph|npc|mem_index|shell|generic
  status        TEXT NOT NULL,          -- pending|queued|running|done|failed|cancelled
  priority      INT DEFAULT 50,
  input         JSONB NOT NULL DEFAULT '{}',
  output        JSONB,
  error         TEXT,
  artifact_key  TEXT,                   -- R2 key 前缀
  parent_id     UUID REFERENCES nexus_tasks(id),
  created_by    TEXT DEFAULT 'hermes',
  worker_id     TEXT,
  attempts      INT DEFAULT 0,
  max_attempts  INT DEFAULT 3,
  locked_at     TIMESTAMPTZ,
  created_at    TIMESTAMPTZ DEFAULT now(),
  updated_at    TIMESTAMPTZ DEFAULT now()
);

CREATE INDEX idx_nexus_tasks_status ON nexus_tasks(status);
CREATE INDEX idx_nexus_tasks_kind ON nexus_tasks(kind);
CREATE INDEX idx_nexus_tasks_created ON nexus_tasks(created_at DESC);

-- 可选：事件流水
CREATE TABLE nexus_task_events (
  id         BIGSERIAL PRIMARY KEY,
  task_id    UUID REFERENCES nexus_tasks(id),
  event      TEXT NOT NULL,             -- enqueued|started|progress|finished|error
  payload    JSONB,
  created_at TIMESTAMPTZ DEFAULT now()
);

-- Mem0：按 Mem0 pgvector 要求；先执行
CREATE EXTENSION IF NOT EXISTS vector;
```

**input 示例（存在 Neon，不在 Redis）**

```json
{
  "goal": "实现登录 API",
  "repo": "org/nexus",
  "constraints": ["加测试"],
  "model_hint": "deepseek-v4-flash",
  "callback": null
}
```

**output 示例**

```json
{
  "summary": "...",
  "pr_url": "https://cnb.cool/...",
  "artifact_key": "artifacts/<task_id>/result.json"
}
```

---

## 6. R2 路径约定

```text
artifacts/{task_id}/input.json      # 可选超大输入
artifacts/{task_id}/result.json
artifacts/{task_id}/logs.txt
artifacts/{task_id}/diff.patch
backups/hermes/{date}/...
```

Neon 只存 `artifact_key = artifacts/{task_id}/`。

---

## 7. 核心流程（给 AI 实现用）

### 7.1 创建任务（Hermes）

```text
1. 生成 task_id (UUIDv4)
2. INSERT nexus_tasks (status=pending, input=...)
3. 若 input 很大 → 写 R2，input 里只留指针
4. RPUSH nexus:queue:{kind} task_id
5. UPDATE status=queued
6. INSERT nexus_task_events(enqueued)
7. 返回 { task_id, status: "queued" }
```

### 7.2 Worker 消费

```text
1. BLPOP queue 30s
2. 若拿到 task_id：
   a. SELECT * FROM nexus_tasks WHERE id=...
   b. 若 status 已 terminal → 丢弃
   c. UPDATE running, attempts+1, worker_id, locked_at
   d. 执行 kind 对应 handler
   e. 成功：output + artifact → done
   f. 失败：attempts < max → 再 RPUSH；否则 failed + DLQ
3. 超时锁：定时任务扫 locked_at 过期 → 重新入队
```

### 7.3 Hermes 取结果

```text
轮询 Neon：GET task by id
或：用户再问时 search Mem0 + 查最新 done 任务
长结果：读 R2 artifact
```

### 7.4 写记忆

```text
任务 done 后由 Hermes（不要每个 Worker 乱写）：
Mem0.add(summary, user_id="nexus", metadata={task_id, kind})
```

---

## 8. Kind → Handler 映射

| kind | 执行位置 | Handler |
|------|----------|---------|
| `graph` | LangGraph Space/本机 | 编译图 `invoke`，checkpoint 可写 Neon |
| `npc` | 任意 Worker | CNB OpenAPI 或建 Issue `@npc/CodeBuddy` |
| `mem_index` | Mem0 侧或 Hermes | 批量索引文档 |
| `claude_code` | **仅本机 Worker** | `claude -p "..."` |
| `codex` | **仅本机 Worker** | `codex` CLI |
| `workbuddy` | 本机桥 | 写本地队列 / IM 指令 |
| `generic` | Hermes 可直接做 | 轻量推理，可不出队 |

路由策略（系统提示要点）：

1. 先 Mem0.search  
2. 轻量 → Hermes 本地完成  
3. 要状态机 → `kind=graph`  
4. 省钱云编码 → `kind=npc`  
5. 重编码本机 → `claude_code` / `codex`  
6. 结束统一 Mem0 + 用户摘要  

---

## 9. 服务 API 最小集

### Hermes Space

```text
POST /v1/tasks          { kind, input } → { task_id }
GET  /v1/tasks/{id}     → 任务状态与 output
POST /v1/chat           自然语言 → 内部可能创建 tasks
GET  /health
```

### LangGraph Worker

```text
# 无强依赖对外 API；进程内循环 BLPOP
# 可选：
POST /v1/admin/run/{task_id}   调试用手动触发
GET  /health
```

### Mem0 Space（可合并进 Hermes）

```text
POST /v1/memory/add
POST /v1/memory/search
GET  /health
```

---

## 10. 环境变量清单

```bash
# Upstash（REST 或 TCP，Worker/Hermes 都能用）
UPSTASH_REDIS_REST_URL=
UPSTASH_REDIS_REST_TOKEN=
# 或 REDIS_URL=rediss://...

# Neon
DATABASE_URL=postgresql://...?sslmode=require

# R2
R2_ACCOUNT_ID=
R2_ACCESS_KEY_ID=
R2_SECRET_ACCESS_KEY=
R2_BUCKET=nexus-artifacts
R2_ENDPOINT=https://{account}.r2.cloudflarestorage.com

# Mem0 / LLM
OPENAI_API_KEY=          # 或兼容端点（嵌入+小模型）
MEM0_COLLECTION=memories

# CNB NPC
CNB_TOKEN=
CNB_REPO=org/nexus

# Hermes
HERMES_MODEL=...
NEXUS_QUEUE_PREFIX=nexus:queue
```

---

## 11. 部署拓扑（两档）

### A. 省钱云档（仅 HF + 托管）

```text
Space Hermes     — 入口 + 入队 + Mem0 客户端
Space Graph      — 只消费 kind=graph
Neon + Upstash + R2
编码 → 仅 kind=npc（CNB）
```

不跑 CC/Codex。

### B. 完整档（推荐）

```text
本机：Hermes 或 Worker（CC/Codex/WorkBuddy）+ 可选 Graph
云：Neon + Upstash + R2 + 可选薄 Hermes 入口 Space
NPC：CNB
```

---

## 12. Upstash 使用纪律（防打满免费额度）

1. Value **仅 task_id**  
2. **BLPOP**，禁止高频空轮询  
3. 历史与结果 **只写 Neon/R2**  
4. 监控月命令数；接近 50 万则降 Worker 数或加长阻塞时间  
5. DLQ 用 List，人工/定时再处理，避免死循环重试刷命令  

---

## 13. 目录建议（单仓 monorepo）

```text
nexus/
  README.md
  docs/architecture.md          # 本文精简版
  packages/
    common/                     # task schema, redis/neon/r2 clients
    hermes-app/
    graph-worker/
    mem0-service/               # 可选
  sql/
    001_nexus_tasks.sql
  scripts/
    worker_loop.py
    enqueue_demo.py
```

---

## 14. 分阶段落地（给 AI 排期）

| 阶段 | 交付 |
|------|------|
| P0 | Neon 表 + Upstash 入队/出队 demo + 一个 `generic` handler |
| P1 | Hermes `POST /v1/tasks` + 查状态 |
| P2 | LangGraph Worker 消费 `graph` |
| P3 | Mem0 search/add 接到任务完成钩子 |
| P4 | `npc` handler（CNB） |
| P5 | 本机 `claude_code`/`codex` Worker |
| P6 | 保活、DLQ、锁超时回收、基础监控 |

---

## 15. 一句话给重新规划的 AI

> 实现 Nexus 时：**Upstash 只做 task_id 队列；Neon 存任务与记忆向量；R2 存大文件；Hermes 负责入队与验收；LangGraph/NPC/本机 CLI 作为按 kind 消费的 Worker。禁止用 Redis 存业务正文，禁止在免费 HF Space 跑重型编码 Agent。**

按此方案拆服务与接口即可，无需再引入 Temporal 等重型编排层。