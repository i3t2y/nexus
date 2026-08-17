# `nexus最新架构.md` 查证报告

> 本报告查证 `docs/new/nexus最新架构.md`(另 AI 给的方案审查稿,末尾自标"内容由 AI 生成仅供参考")的论断是否**合理且适合**当前 Nexus 仓换装后方案。
> **核心结论:方向合理,是好的"该往哪走"目标蓝图,但不可当现状描述读 — 三层与仓实装/HF 现状脱节。**

---

## 0. 查证方法

双路并行:
- **外部查证**:HF Spaces 官方文档(2026 政策) / LangGraph persistence 官方文档 / Claude Code headless 官方文档 / OmniRoute Wiki。
- **仓实装探查**:Explore agent 逐条核 `spaces/` 4 组件、hermes 换装现况、omniroute 仓内否、litestream、PostgresSaver、下游执行器性质、`task_queue`、Supabase 表清单、双 Hermes。

---

## 1. 准确 / 仍成立的论断(7 点)

| # | 论断 | 查证依据 |
|---|------|---------|
| 1 | LangGraph Checkpointer(线程内图状态)vs Store(跨线程长期数据)区分 | LangGraph 官方文档逐字吻合:Checkpointer=thread-scoped 短期记忆,Store=cross-thread 长期记忆,多数应用两者并用 |
| 2 | PostgresSaver 生产可用作 Checkpointer | 官方明确列 `PostgresSaver` 为持久 checkpointer 选项;仓 `libs/shared/checkpointer.py:34,61` 已用 `AsyncPostgresSaver.from_conn_string` |
| 3 | Hermes 适合长期代理运行时(跨会话记忆/Skill 迭代/FTS5 会话搜索/Cron/Gateway/并行子代理) | 与 NousResearch Hermes Agent 实证一致 — 仓 `spaces/hermes/` 刚完成此内核换装 |
| 4 | 状态分层(状态/产物/代码外置,不全塞容器内) | 仓已落:litestream WAL→R2(state.db) + Supabase(8 表) + R2(3 桶)三套 |
| 5 | 32 Key 额度感知调度(非纯轮询) | 调度算法合理:`score = 可用性权重 × 剩余额度 × 健康度 ÷ (并发+1)`;429 冷却/认证失败即禁/5xx 退避/上下文超限不盲换 Key 全对 |
| 6 | 单写主 + 候选 Skill + 幂等键 防 Skill 并发写覆盖脑裂 | 仓 `libs/storage/storage.py:130` 已有 `task_queue` + `enqueue_task(idempotency_key=)`;`:137-143` `eq("idempotency_key")` upsert 幂等。双写冲突论点成立 |
| 7 | CLI 不共享个人登录态,自动化用 API Key | Claude headless 官方:`--bare` 推荐(省启不读 OAuth/keychain)+ `ANTHROPIC_API_KEY` 认证 + `--settings apiKeyHelper` 备选;个人登录缓存不入公开 Space |

---

## 2. 与实际脱节的三层(需修正认知)

### 脱节 ① "4 免费 Docker Space 长期部署"前提 ← 已被用户澄清推翻

- 文档(及前查证报告初判)据 HF 2026 政策:"Gradio/Docker Space 需付费计划,免费个人仅最多 2 个 ZeroGPU Gradio"。
- **实情**:用户已申请 4 个 HF 账号,**每个账号历史已建好免费 Docker Space,继续可用**。新付费政策只限**新建**,历史免费配额 Space 不受限。
- → 4 Space 稳。**非**推翻方案地基。文档第九节三方案(VPS / 降 2 Space / 免费实验)的紧迫性下降 — 免费历史配额已解决部署地基问题。

### 脱节 ② 文档假设下游 = 受控短生命周期执行器;仓实装 = 常驻透传 thin proxy

- 文档第七节详述 Claude/Codex Space 应 `subprocess` 起 `claude -p --bare --allowedTools --output-format json --json-schema` CLI 子进程 + 独立 `git worktree` + wall-clock 超时 kill + 统一任务 schema(`/execute_task` 端点)。
- **仓实装**:`spaces/claude-code/app/main.py:45` `POST /run` + `:73` `httpx.AsyncClient(timeout=120.0)` 透传 Anthropic API;`spaces/codex/app/main.py:47` `POST /complete` + `:71` `httpx(timeout=60.0)` 透传 OpenAI。**无** subprocess / worktree / 受控生命周期 = **thin proxy**(短生命周期只是 httpx 超时,非 worktree+timeout 执行器)。
- 文档"假设"实为**目标蓝图**,尚未实装。这正是仓内 plan 阶段 J 标的"方案二最适合,hermes 跑稳后再动"。

### 脱节 ③ Supabase 表清单名实严重不符(命名体系不同,非缺表)

| 文档列理想(11 表,全仓内 0 同名) | 仓实装(`sql/00_schema.sql` + `01_pgvector.sql`,8 表) |
|---|---|
| `agent_sessions` | `agent_states`(概念重叠) |
| `tasks` | `task_queue`(概念重叠) |
| `task_events` | `task_logs`(概念重叠) |
| `graph_threads` | —(langgraph 自管 checkpoints 表) |
| `worker_leases` | —(未实装) |
| `memory_candidates` | `memory_vectors` + `long_memory`(概念分散) |
| `skill_versions` | `skills_index`(概念重叠) |
| `model_usage` | —(未实装) |
| `artifacts` | —(走 R2 `nexus-artifacts` 桶,非 Supabase 表) |
| `approvals` | —(未实装) |
| — | `backup_snapshots` `space_health`(仓独有,文档未列) |

- 文档是**理想 schema**,不是现状。功能覆盖有重叠但命名全异;`worker_leases`/`model_usage`/`approvals`/`artifacts`(表)未实装(artifacts 实走 R2 桶)。
- 直接照搬 11 表名会误判"已实装"。plan 五阶段递进补理想 schema,不本次 hermes 换装范围。

---

## 3. 小瑕疵

- **"完全跳过权限检查的高特权模式"措辞误**:Claude headless 推荐模式是 `--bare`(省启动加载,非无权限),真跳权是 `bypassPermissions` 模式(文档自己也标"不要用")。文档把两者混为"高特权模式"。

---

## 4. omniroute 实情(澄清前查证报告"隐含底座"判)

- 文档把 omniroute 列为模型平面组件。前查证报告判"仓内无 omniroute 代码"对 — **它不在仓内**。
- **但** omniroute **不是未建/VPS 外置** — 它是**第 5 个 HF Space `nonoke/omn`**,独立账号独立 Space 已跑通,endpoint `https://nonoke-omn.hf.space`。
- 暴露 anthropic Messages 兼容 API,模型名 `glm-5.2`/`claude-sonnet-5` 透传接受。**glm5.2(本会话所用模型)= 经此 omniroute 出**(Claude Code 跑在 `nonoke/omn` → 下游链上)。
- 仓内引用:`.env.example` `ANTHROPIC_BASE_URL=https://nonoke-omn.hf.space`(公开端点,非敏感)+ `agent_server.py` 注释指向。无仓内实体代码 — 与"独立 Space 合码"一致。
- 文档"omniroute 是模型接入平面"正确。前查证报告"仓内无 omniroute"也正确。两者不冲突。

---

## 5. 适合性判断

**合理(方向对)**:LangGraph 轻量化 / 状态分层 / 32 Key 额度调度 / 单写主 / 幂等 / Skill 版本审批 / 实施五阶段递进 — 与 plan 决策 4-7 高度重叠,你换装后方向与它一致。

**但别当现状读**:描述的是**目标态**,三处大前提(免费部署/下游执行器/Schema)与现行仓不符。直接照搬误判"已实装"。

**与 plan 关系**:
- 文档阶段五(Skill 演化)、11 表理想 schema、下游受控执行器 = plan **阶段四候选**(非本次 hermes 换装范围,标"hermes 跑稳后启动")。
- 文档"双 Hermes 推荐单"论点 → 印证记忆 `nexus-hermes-agent-not-selfbuilt` 里"Slots 待对齐" — 单 Hermes 先跑稳,双实例是高并发才值得。

---

## 6. 给后续 3 条建议

1. **本文档接入仓时标"目标蓝图"**,别散进 5 份永续文档当现状(防 omn 血统混入式重复 — 上次整改已踩过)。
2. **HF 部署前确认账号档位**:4 账号历史免费配额已稳;新增 Space 才受新付费政策限制。
3. **下游受控执行器 + 理想 11 表 = plan 阶段四独立做**,不并进当前 hermes 换装部署闸门(V6-V9)。

---

## 7. 部署侧闸门现状(R 项更新)

| 闸门 | 状态 | 依据 |
|------|------|------|
| R1 omniroute 协议 + 鉴权头 + 模型名 | ✅ 大部分通 | `nonoke/omn` Space 跑通 + glm5.2 经此出(本会话实证)。剩 HF Space Logs 抓一条 `POST /v1/messages` 200 锁死鉴权头 |
| R9 custom tool 注册 API 公开 | ✅ 通 | V4 源码实证 `PluginContext.register_tool` |
| R3 run_conversation 返 dict 键 | ✅ 通 | `turn_finalizer.py:574-607` |
| R6 litestream WAL 不回退 DELETE | ✅ 通 | HF ext4 非 NFS |
| R7 nemo-relay Rust 编译 | ✅ 通 | base build 预编译 wheel |
| R4 uv pip vs base pip 覆盖 | ✅ 通 | fastapi/uvicorn 兼容 |
| R5 运行时 lazy_deps 懒装 | ⏸ 部署期验 | 预装 anthropic + disabled_toolsets 列全,日志 grep `pip install` 确认无 |

5 Space 全活(4 组件 nexus + 1 omniroute `nonoke/omn`)。部署侧降为四步非阻塞:GHCR build/push `:stable` → HF Secrets 填真值 → `sync-logic-bucket.sh` 推 Bucket → HF Restart(不 rebuild)。

---

**查证日期**:2026-08-01
**对照仓状态**:hermes 内核已换装 NousResearch Hermes Agent(commit `feat/hermes-coreswap-nousresearch`)
**关联**:[[hermes-agent-换装方案.md]] · [[nexus-hermes-agent-coreswap-done]] 记忆
