# Gork 方案分析：合理性、优异性、可行性

> **背景**: Gork 是 Nexus 旧架构原作者（首席架构师）。2026-08-18 做出三裁决点+三补充方案，经仓内搜索+源码核证+官方文档查证分析如下。
> **性质**: 事实核证分析，非观点。每个结论附证据链。

---

## 一、三裁决点核证

### 裁决①：废除 nexus-worker MCP stdio 桥

**结论：✅ 合理，已闭环**

| 维度 | 分析 |
|------|------|
| 合理性 | MCP stdio 桥在 Hermes 换装后成为冗余层。Hermes Agent 原生 plugin 可直调下游，中间多一层子进程无收益 |
| 证据 | `old/hermes/mcp/nexus_worker_mcp.py` 194 行全仓零引用（grep 无 import/config/路径） |
| 替代已落 | `scripts/plugins/nexus-r2/tools.py` 三 tool（`nexus_call_claude`/`nexus_call_codex`/`nexus_route_langgraph`）经 `libs/shared/gateway.call_space` 直调下游 Space |
| 隔离性 | Gork 判"子进程隔离在 HF 收益小，真隔离靠 Space 边界+队列"——worker 本就是另一 HF Space，跨进程隔离天然成立 |
| 现态 | 文件已归 `old/`，`mcp-server-for-hermes.md` 两份已标 DEPRECATED，`SKILL.md` 已标 DEPRECATED |

### 裁决②：kind=graph 两路并存

**结论：✅ 合理，已落**

| 维度 | 分析 |
|------|------|
| 合理性 | 短图（用户等回复）同步走 plugin 直调；长图（后台工作流）异步走 task_queue + SKIP LOCKED poll。两路互补，不互斥 |
| 同步路证据 | `_TARGET_PATH["langgraph"]="/execute"` 实存，`_handle_nexus_route_langgraph` handler 已注册 |
| 异步路证据 | `kind='graph'` 已入 task_queue enum，`neon-schema.sql` 注释含两路并存说明 |
| 索引支持 | `idx_task_queue_status_kind (status, kind)` 复合索引已建，符合 SKIP LOCKED 性能要求 |
| 现态 | Stage A 已落，Stage B 只通 kind=npc 优先，kind=graph 非阻塞 |

### 裁决③：Upstash→Neon task_queue + FOR UPDATE SKIP LOCKED

**结论：✅ 合理，标准方案，已落**

| 维度 | 分析 |
|------|------|
| 合理性 | `FOR UPDATE SKIP LOCKED` 是 PostgreSQL 9.5+ 原生扩展（非 hack），官方文档明确将其作为队列消费的标准模式 |
| 官方证据 | PostgreSQL 17 文档 `sql-select` 章节：`SKIP LOCKED` 是 PG 扩展，与 `FOR UPDATE` 结合是队列消费标准模式 |
| Neon 证据 | Neon 官方 `queue-system.md` 指南同路：`WITH cte AS (SELECT ... LIMIT 1 FOR UPDATE SKIP LOCKED) UPDATE ... RETURNING *` |
| 行业证据 | Prisma、Netdata、PgQueuer 等生产项目均使用此模式 |
| 索引已建 | `idx_task_queue_status_kind (status, kind)` 复合索引 |
| 补校正 | poll 频率须 >5min（Neon Free .25 CU scale-to-zero 5min 必睡），唤醒靠 cron-job.org /health 不靠长连 |

---

## 二、补充方案核证

### 补充④：持久化 R2+Neon 双轨

**结论：✅ 方向正确，大半已落，措辞膨胀**

| 维度 | 分析 |
|------|------|
| 合理性 | R2 作副路快照层、Neon 作主路持久化，双层防丢失——标准灾备模式 |
| 已落程度 | 6 脚本全存：`persist_to_neon.py`（主路）, `persist_to_r2.py`（副路, 已改读 Neon HTTP /sql）, `restore_from_r2.py`（恢复, sha256 校验）, `restore_home_files.py` / `home_files_uploader.py`（home 文件）, `restore_state.py` / `state_db_uploader.py`（state.db） |
| SIGTERM 钩子 | 4 脚本全实现 `_on_sigterm` handler + `--once` 模式 |
| real-start.sh | trap handler 已实现：kill -TERM boot 子进程 + 四 persist daemon + 10s 等待 flush |
| 真缺口 | 极小：开机 restore 路径是否从 Bucket→R2 切换（待真实 failover 演练），非架构级缺口 |
| 措辞校正 | Gork 说"另辟路径"——实为"补钩子+核 restore"，非新挖 |

### 补充⑤：CNB NPC 三连法

**结论：⚠️ 方向正确，实据充分，两盲区待解**

| 维度 | 分析 |
|------|------|
| 合理性 | 异地 Agent 执行必须走外部平台，CNB CodeBuddy 是成熟方案 |
| 三连法证据 | ① Streamable HTTP `https://mcp.cnb.cool/mcp` ② SSE `/sse` ③ STDIO `npx -y -p @cnbcool/mcp-server cnb-mcp-stdio` |
| 派 NPC 双路 | ① Issue `@npc/CodeBuddy` `issue.comment@npc` 触发 ② OpenAPI `POST /{repo}/-/build/start` event=`api_trigger_npc` + npc.name=CodeBuddy |
| 盲区① | HF 容器出网 `api.cnb.cool`/`mcp.cnb.cool` 可达性未证（同 telegram SNI 黑名单机制风险，`nexus-hermes-telegram-cfworker`） |
| 盲区② | STDIO 路需 Node ≥18+npx，base 镜像未装 Node，装 Node 需重 build 付费墙 |
| 实操路 | Stage B 先 OpenAPI curl 路（无 Node 依赖），走通再议升 MCP |

### 补充⑥：砍 WorkBuddy 路

**结论：✅ 合理，已落**

| 维度 | 分析 |
|------|------|
| 合理性 | WorkBuddy = 腾讯 CodeBuddy 桌面/IM 客户端。异地 Agent 路由不须 IM 桌面出口，Hermes 直连 CNB 即可 |
| 已落证据 | kind 枚举已收：`{generic|graph|npc|pi|dsh}`（移 `workbuddy_npc`） |
| 冲突点 | 旧记忆 `nexus-chat-extracted-decisions-2026-08-15` 用户曾拍保留 WorkBuddy——需用户复核确认 |

---

## 三、收口版合同视角的再评估

2026-08-21 收口版合同（`nexus-3piece-deploy-contract`）改变了 Gork 方案的部分前提：

| 原 Gork 方案 | 收口合同影响 | 调整 |
|-------------|------------|------|
| 三下游 Space 待重建 + Gateway 路由 | **永久取消重建**，nmem/memlg 改冷备 | 三 tool 目标 Space 不存在，kind=claude_code/codex 枚举需删 |
| R2 MANIFEST CAS 租约（双机抢锁） | 同时只开一台 Hermes，无需 CAS 租约 | R2 只需 `snapshots/<ts>/` 不可变 blob + 恢复端选最大 gen |
| mem0 HTTP → memlg | 进程内 oss pgvector | SelfHostedBackend→memlg 路已废 |
| 三件套 = 三 Space | 三件套缩进 Hermes 一进程 | LangGraph 改库非 Space |

**Gork 方案在收口合同下的有效性**：5/6 仍有效（MCP 桥废、kind=graph 两路、SKIP LOCKED、持久化 R2+Neon、CNB NPC）。仅 MANIFEST CAS 租约因"同时只开一台"而成为过度设计。

---

## 四、总结论

| 维度 | 评估 |
|------|------|
| **合理性** | 4.5/5。全部裁决有源码/文档证据支持，与现有架构无冲突。MCP 桥废、SKIP LOCKED、CNB NPC 三路均有实据 |
| **优异性** | 4/5。MCP 桥废→plugin 直调减少一层 IPC 开销；SKIP LOCKED 比 Upstash Redis 少一个外部依赖；持久化 R2+Neon 双层比单层可靠。但措辞"另辟路径"膨胀了实际已落程度 |
| **可行性** | 4.5/5。5/6 方案已落或 Stage B 可落。真风险只剩 HF 出网 CNB 可达性（盲区①）和 Node 付费墙（盲区②），前者需 curl 实测，后者有 curl 绕路 |

**一句话**：Gork 方案方向准确，证据扎实，大部分已落地；收口合同取消了三 Space 重建和双机抢锁，但不影响 Gork 核心裁决的有效性。