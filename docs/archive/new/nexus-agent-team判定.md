# Nexus 算不算广义 Agent Team?— CodexLoom 框架对照判定

> 研读《最佳实践:从 Multi-Agent 到 Agent Team》(CodexLoom 产品文,作者 yan5xu,repo github.com/yan5xu/codexloom)后,提炼框架,逐条对照 Nexus 仓换装后实装,判 Nexus 处于五阶段哪一档。
> **核心判定:Nexus 不算 CodexLoom 定义的 Agent Team,是"有 agent 团队骨架的智能路由系统"。处五阶段中 Long-running + Domain 分化已成,卡在 Human Router → Agent Team 之间。换装把"找谁"从关键词 route 移给 agent 推理 = 迈过 Router 瓶颈半步,但协作外化成可查结构(Message/Topic/Profile/Overview)全缺。**

---

## 0. 方法

研读原文 → 提炼框架(五阶段 + 五支柱 + 判据 + 反例)→ 逐条对仓实证(`spaces/`/`libs/`/`sql/`/`workers/`)→ 裁定。仓内实证点引 `file:line`。

---

## 1. 文章框架提炼

### 主旨

多 Agent ≠ Agent Team。分水岭 = 原本在 Human 脑里的**责任 / 关系 / 交接 / 状态 / 边界**被外化成 Team 可查询的工作结构。Human 从唯一 Router 上移成 Owner。

> 关键金句:"Agent Team 的诞生,不是当它们同时开始运行。而是当它们开始长期承担不同责任,能够找到彼此、直接协作、持续收口,并在 Human 治理下共同推进真实工作。"

### 五阶段责任线

`Task Agent → Long-running Agent → Domain Agent(s) → Human Router(bottleneck)→ Agent Team`

| 阶段 | 本质 | 触发 |
|------|------|------|
| Task Agent | 一次性,边界清,完成即弃 | 初始 |
| Long-running | 同类责任持续回同一主体(Context/纠正/边界带入);非对话拉长,是责任持续 | 同类问题重复 |
| Domain 分化 | Scope 扩张致能力劣化 → 边界从摩擦中显现(非先画) | 真实工作暴露 |
| Human Router 瓶颈 | Agent 并行但入口/Context/结果/下一步全汇聚 Human 串行 → 真瓶颈转移给 Human | Agent 多起来但 Human 仍串每步 |
| Agent Team | 协作责任从 Human 转给 Agent | Human 上移为 Owner |

### Agent Team 五支柱(CodexLoom 实现)

1. **Profile**(Identity/Domain/Scope) — 当前组织假设,非能力证明;上班即写死,真实工作暴露后修正
2. **Message**(request/notification/reply)— Agent 直接通信,带因果链;非聊天框,带边界 Context 不复制全 Thread
3. **Topic**(Responsible + current brief + Participant + Artifact + Needs You)— 跨 Agent 收口,唯一当前版本;非群聊共享 Context
4. **Overview**(Status/Capacity/Token Usage)— Signal 非 Diagnosis;治理循环:发现 Signal → 下钻 Evidence → 判因 → 选小可逆干预 → 后续验
5. **External**(Connection/Address/Membership/Outbox)— 受管入口非接 Bot;Identity/角色/触发/出站/信任域分别治理

### 判据(是不是 Team)

- Agent 能据自己 Scope + 可查 Profile **自判找谁**,而非回头问 Human
- Agent 能**直接**把有边界工作交对方 + 结果沿原链路回流(不需 Human 翻译搬运)
- 跨 Agent 工作**有唯一当前版本 + 责任收口主体**(Topic/Responsible)
- Human 不串联每步,只在方向/事实/Review/授权/边界回
- Team 被**观察治理**(不是读每个 Thread)

### 反例(不算 Team)

- 多 Agent 但每步仍 Human 选入口/整背景/搬 Context/转交结果 = 独立工具组
- 按**预写 Workflow** A→B→C 串行 = 程序节点换皮 Agent
- 群聊共享 Context = 无收口主体
- Automation(自动发消息)≠ Team

### 五支柱各自不充分

- Profile 只是组织假设(非能力证明)
- Message delivered ≠ 正确
- Topic resolved ≠ 完成
- Overview Signal ≠ Diagnosis
- Membership ≠ 权限沙箱
- External receipt ≠ 接受

---

## 2. 对 Nexus 逐条核(仓实证)

### ✅ 沾边(Team 雏形)

| 对照项 | Nexus 实装 | 证据 |
|--------|-----------|------|
| Long-running | hermes 换装 AIAgent 持 `state.db`(SessionDB messages/conversation_history)+ litestream 续命 → 同类责任持续回同一主体 | `agent_server.py:46-47` `session_db=HERMES_HOME/state.db`；`scripts/litestream.yml` WAL→R2 |
| Domain 分化 | 4 Space 各有明确 lane:hermes 主控 / langgraph 编排 / claude-code 编码 / codex 补全 → Domain 边界清晰,非塞一 Space | `spaces/{hermes,langgraph,claude-code,codex}/app/main.py` 各独立 |
| 直接通信(部分) | hermes 经 `nexus_*` tool 直接调下游 `call_space`,**不经 Human 转交** | `scripts/plugins/nexus/tools.py` 三 handler 桥 `call_space` |
| Human Owner 面 | `force_space` 兜底 = Human 显式指派保留方向/选择权 | `app/main.py` `_do_run` force 分流 |

### ❌ 不达(仍 Multiple Agents 非 Team)

| 对照项 | Nexus 实装 / 缺口 | 证据 |
|--------|------------------|------|
| Agent 自判找谁 | hermes agent loop 据 prompt 语义智能选 `nexus_*` tool = **agent 推理选 tool,不是查 Profile 自判找谁**。下游是匿名 HTTP 端点,无 Identity/Domain/Scope 可查 | `tools.py` tool description 写擅长域让 agent 选,而非查 Profile Directory |
| Profile / Directory | **全缺**。4 Space 无可查询 Profile,无 Organization/Collaboration 声明关系。下游只是 HTTP 端点(`/run`/`/complete`/`/execute`) | 仓内 grep `topic/responsible/membership/outbox/participant/profile` 命中 0(仅 node_modules 噪声) |
| 双向 Message 协作 | **单向** hermes→下游透传 LLM。下游不回 hermes 主动 Message、不互发 notification、不协议轮校正(下游 thin proxy 无 agent loop) | `spaces/claude-code/app/main.py:45` `POST /run` httpx 透传;`codex/app/main.py:47` `POST /complete` 透传 — 无 agent loop 回 Message |
| Topic / 收口 | **全缺**。跨 Space 工作无唯一当前版本 + 责任收口主体。`agent_states`({thread_id,state jsonb,updated_at})= 任务级状态非工作当前 brief;`task_queue` = 队列非 Topic | `sql/00_schema.sql` agent_states/task_queue 表定义 — 无 Responsible/current_brief/Participant/Artifact 字段 |
| Overview 治理 | keepalive = 健康探(ping /health)+ 防休眠(写 space_health)**非流动治理**(无 new-work wait/backlog/Capacity/Signal→Diagnosis 循环);Gradio 系统状态 Tab = 健康面板非治理 Overview | `scripts/keepalive.py` 头注"两类用途:被 ping + ping 下游防休眠" — 无 Capacity/Signal/backlog |
| External 受管入口 | **全缺**。hermes 是入口但无 Membership/Address/Outbox;身份/角色/触发/出站策略未分别治理 | 仓内无 External 结构实装 |
| 协作责任从 Human 转出 | 换装前 = Human(或 route 关键词)定找谁;换装后 = agent 推理定。**转给 agent 了,但"找谁"基于 tool description 非可查 Profile**;边缘 case 仍 `force_space` Human 兜底 | `app/main.py` force_space 兜底路 A + agent 路 B 并存 |

---

## 3. 判定

**广义上:不算 CodexLoom 定义的 Agent Team,是"混合架构 + Multiple Agents with smart router"。**

- 有 Team **雏形**:Long-running hermes 主控 + Domain 4 Space 分化 + agent 自主路由(路 B)+ Human 兜底 Owner 面
- 缺 Team **核心**:Profile/Directory 可查、双向 Message、Topic 收口、Overview 治理、External 受管入口
- 协作链路是 **hermes 单向调下游 thin proxy**(下游无 agent loop),非"长期责任主体互发有边界工作 + 结果回流 + 逐轮校正"
- `force_space` 兜底 = 文章"Human Router"阶段残留(用户仍可直接指派,绕 agent 推理)

**最精确归类**:处五阶段中 **Long-running + Domain 分化已成,卡在 Human Router → Agent Team 之间**。换装把"找谁"从关键词 route 移到 agent 推理 = 迈过 Router 瓶颈半步,但"协作外化成可查结构(Message/Topic/Profile/Overview/External)"全缺。

---

## 4. 升级到真 Team 的路径(对应五支柱)

若要把 Nexus 升真 Agent Team,缺件对应文章五支柱:

1. **Profile / Directory**:4 Space + omniroute 各声明 Identity/Domain/Scope,hermes 可查(下游 HTTP 端点升级返 Profile,非匿名 tool description)
2. **双向 Message**:下游从 thin proxy 升为有 agent loop 的 Domain Agent(= plan 阶段四"受控执行器改造"真意 — 不只 subprocess+worktree,还要能回 Message/notification);hermes 与下游互发有边界请求 + 结果沿链路回流
3. **Topic 收口**:跨 Space 任务建 Topic — hermes 当 Responsible,下游 Participant;current brief + Artifact + Needs You 替代现 task_queue/agent_states(或在其上叠 Topic 层)
4. **Overview 治理**:keepalive 升为流动观察(new-work wait/backlog/Capacity/Token),Signal→Diagnosis→选干预→验证循环
5. **External 受管**:hermes 入口加 Membership/Address/Outbox 分别治身份/角色/触发/出站

---

## 5. 与 plan 阶段四的暗合

- **下游"受控执行器改造"(方案二)** = 上述 #2 的雏形(子进程化 + 统一 schema)。但文章视角揭示:**只到受控执行还不够,要配 Message 回流 + Topic 收口才成 Team**
- **双 Hermes Worker** = 文章"稳定 Agent 动态 Team" — Worker 是动态扩展,Control 是稳定入口。match 文章"稳定的 Agent,动态的 Team"
- **单先双后** = 文章"单满负荷再分化"。match

---

## 6. 一句话

Nexus 现 = **有 agent 团队骨架的智能路由系统**,广义划不进 CodexLoom 定义的 Agent Team。要升 Team 须把 plan 阶段四(下游执行器 + 双 hermes)再向 Collaboration 协议(Message/Topic/Profile/Overview/External)走一层。这正是文章"从 Multiple Agents 到 Agent Team"那段缺的那一步。

---

**判定日期**:2026-08-01
**对照仓状态**:hermes 内核已换装 NousResearch Hermes Agent(分支 `feat/hermes-coreswap-nousresearch`)
**研读材料**:《最佳实践:从 Multi-Agent 到 Agent Team》(CodexLoom/yan5xu.github.io, 微信发布)
**关联**:[[hermes-agent-换装方案.md]] · [[nexus最新架构-查证.md]] · [[nexus-hermes-agent-coreswap-done]] 记忆
