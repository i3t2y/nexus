# AI 书签知识库方案 v5：nexus 云脑闭环（终版）

> 日期：2026-09-06 ｜ 全链路收官  
> 组成：Obsidian（人侧）+ EverOS（深记忆）+ n-omn（模型网关）+ nexus/hermes（云上大脑 + Telegram 入口）  
> 核实来源：`i3t2y/n-omn`、`i3t2y/nexus` 及 `docs/shared/ARCHITECTURE.md`、EverOS README、`EverMind-AI/EverOS` main 分支源码（memory_root.py / default.toml）、HF Spaces / Storage Buckets 官方文档  
> ⚠️ **2026-09 修正**：EverOS 持久化原设想的 rclone→R2 + litestream 全套复杂度，已查证作废，改走 **HF Storage Bucket 卷挂载**（免费 + 持久 + 本地快路径）。见 §6 补 1。



---

## 0. 结论

**是的。加上 `i3t2y/nexus`，这才是真正的云脑闭环。**

v4 遗留的两个待确认点已全部解决：

| v4 待确认          | 你的答复                           | 影响       |
| --------------- | ------------------------------ | -------- |
| Space 是否免费      | **之前创建的免费 Docker Space，可免费使用** | ✅ 零成本成立  |
| 池里是否有 embedding | **有可用的 embedding 模型**          | ✅ 语义检索成立 |

而 nexus 补上了 v4 架构里**一直缺的那层——Agent 编排层与持久化主路**。

---

## 1. nexus 是什么（核实结论）

| 项                    | 内容                                                                                                  |
| -------------------- | --------------------------------------------------------------------------------------------------- |
| 仓库                   | `i3t2y/nexus` —— 混合 Agent 系统，**生产中**                                                                |
| **hermes（sonoke/h）** | **云上大脑**：Telegram 入口 / 路由 / 调度                                                                      |
| **mem0**             | 向量记忆层（hermes 进程内 OSS 模式 → Neon pgvector `hermes_mem0` 表）                                            |
| **Neon Postgres**    | **持久化主路**：`agent_states` / `task_logs` / `long_memory` / `skills_index` 四表 + `task_queue` + mem0 向量 |
| **Cloudflare R2**    | 灾备快照副路（Neon→R2 周期快照，manifest-only）                                                                  |
| 模型                   | 经 OmniRoute（n-omn）`/v1/chat/completions`                                                            |
| 派发                   | `task_queue` → 本机桥（`FOR UPDATE SKIP LOCKED` poll）→ CNB CodeBuddy                                    |
| 保活                   | **README 关键约束已明确**：外部 cron 保活 `/health`                                                             |

### 官方数据流（原文）

```
用户 → Telegram → hermes (sonoke/h)
  → LLM 推理 → OmniRoute (nonoke/omn) /v1/chat/completions
  → persist_to_neon.py 主路 (后台 600s) → Neon 四表 (结构化状态)
  → persist_to_r2.py 副路 (后台 1800s) → Neon 读 → R2 快照备份
  → act delegate → task_queue kind='npc' → 本机桥 poll → CNB CodeBuddy

真相源 = Neon 四表 + hermes_mem0 + MEMORY.md + skills
```

### 存储分层（原文）

| 层              | 内容                           | 存储                        | 持久化方式                                    |
| -------------- | ---------------------------- | ------------------------- | ---------------------------------------- |
| hermes 结构化（主路） | 四表状态                         | **Neon**                  | `persist_to_neon.py` 直连（600s）            |
| hermes 结构化（副路） | 四表快照                         | **R2**                    | `persist_to_r2.py`（1800s, manifest-only） |
| task_queue     | 委托任务                         | Neon                      | act delegate 写，本机桥消费                     |
| 逻辑层            | scripts/app/libs/plugins     | HF Bucket（rw 挂载）          | Actions sync                             |
| home 文件        | .env / SOUL.md / config.yaml | HF Bucket `home-backups/` | restore + uploader 周期                    |
| state.db       | 会话历史                         | 本地盘 + Bucket 快照           | restore_state + state_uploader           |
| **Mem0**       | **向量记忆**                     | **Neon pgvector**         | 进程内 OSSBackend（`hermes_mem0` 表）          |

> mem0 配置：`nemotron-3-embed-1b` 2048 维 + `hnsw:false` + 智谱 LLM

---

## 2. 关键判断：mem0 与 EverOS 不是重复，是分工

nexus 已有 mem0，所以必须先回答：**为什么还要 EverOS？**

| 维度         | **mem0（热记忆）**        | **EverOS（深记忆）**                                                             |
| ---------- | -------------------- | --------------------------------------------------------------------------- |
| 存储形态       | Neon pgvector 向量（黑盒） | **Markdown 文件**（透明）                                                         |
| 人类可读       | ❌ 需 API/SDK 才能看      | ✅ Obsidian 直接打开、可编辑                                                         |
| 自演进        | 基础                   | ✅ **OME reflection**：合并 episode 簇、提炼 skills                                 |
| 记忆分轨       | 单轨                   | ✅ **双轨**：user（episodes/profile/facts/foresights）+ agent（cases/skills）       |
| 检索         | 向量语义                 | ✅ **混合**：BM25 + 向量 + 标量过滤，单次查询                                              |
| 可干预        | ❌                    | ✅ 改 `.md` 即改记忆，watcher 秒级重索引                                                |
| 与 Obsidian | ❌ 无交集                | ✅ **官方设计**："Open the folder in Obsidian — your agent's brain is just files" |
| 定位         | **机器读 · 低延迟**        | **人机共享 · 可深可演进**                                                            |

### 结论：快记忆 / 慢记忆两层，互补而非替代

- **mem0 = 热记忆**：hermes 运行时低延迟调用，服务 Telegram 实时对话。快，但黑盒。
- **EverOS = 深记忆**：结构化、可检索、可自演进、能进 Obsidian 图谱。慢，但人能读能改。

**EverOS 真正的增量价值**：把 nexus 真相源里的 `MEMORY.md`（一个会无限膨胀的纯文本文件）升级成**结构化、可检索、能自演进、能进知识图谱的记忆树**。

> ⚠️ 诚实的提醒：记忆量小时，`MEMORY.md` 更简单。**EverOS 的价值随记忆量增长而增长**。如果你当前的 `MEMORY.md` 还很短，可以暂缓。

---

## 3. 电报角色的升华（这条对话线的漂亮收尾）

| 阶段         | 电报的角色    | 问题                                                        |
| ---------- | -------- | --------------------------------------------------------- |
| v1（最初设想）   | **存储后端** | ❌ 违反 ToS（当网盘），有封 Bot 风险；不解析内容；不接 Obsidian                 |
| **v5（现在）** | **交互入口** | ✅ nexus/hermes 原生支持；数据落在 Neon（主）+ R2（副）+ Obsidian（人侧），全合规 |

**电报从"违规的存储后端"变成了"合规的交互入口"——既保留了它随手发送、跨设备直达的便利，又彻底规避了 ToS 风险。** 这是整条链路最漂亮的一次转身。

---

## 4. 完整云脑架构

```
┌──── 入口层 ────────────────────────────────────────┐
│  Telegram（交互入口）                                │
│  hermes (sonoke/h) 云上大脑 · 路由 / 调度            │
└──────────┬─────────────────────────────────────────┘
           │
┌──── 模型层 ────────────────────────────────────────┐
│  OmniRoute (n-omn) https://omn.360710.xyz/v1        │
│  NIM 池 32 key 轮询 + 自定义节点 + Health Autopilot │
└──────────┬─────────────────────────────────────────┘
           │
┌──── 记忆层（双层）──────────────────────────────────┐
│  🟡 mem0 · 热记忆  →  Neon pgvector hermes_mem0      │
│     机器读 · 低延迟 · 服务实时对话                    │
│  🟢 EverOS · 深记忆 →  Markdown 原生                  │
│     人机共享 · 混合检索 · OME 自演进 · 进 Obsidian    │
└──────────┬─────────────────────────────────────────┘
           │
┌──── 持久化层 ──────────────────────────────────────┐
│  主路：Neon 四表（600s）+ hermes_mem0               │
│  副路：R2 快照（1800s, manifest-only）               │
│  EverOS：EVEROS_ROOT = HF Bucket 卷挂载（免费持久）  │
│    ├─ .md 真相源 → 桶（持久，重启免拉）              │
│    └─ .index/ → symlink 本地 ephemeral（快+安全）    │
│       重启只重建索引（从桶 .md 自动重建，零 R2/rclone）│
└──────────┬─────────────────────────────────────────┘
           │
┌──── 人侧 ──────────────────────────────────────────┐
│  Obsidian vault：PARA + Zettelkasten + 99-Memory/   │
│  坚果云 Nutstore Sync 双向增量同步                    │
│  单一写入者：人写区（你）/ AI 写区（99-Memory/）       │
└──────────┬─────────────────────────────────────────┘
           │
┌──── 运维层 ────────────────────────────────────────┐
│  保活：外部 cron /health（nexus 已实现，直接复用）    │
│  网关：CF Worker（scripts/gateway + FlareTunnel）    │
│  派发：task_queue → 本机桥 → CNB CodeBuddy           │
└─────────────────────────────────────────────────────┘
                        ⟲ 闭环
```

---

## 5. EverOS 怎么接进 hermes

hermes 有 `mcp/` 目录与插件体系（nexus-r2 / nexus-ops），三种接法：

| 接法                       | 做法                                                                     | 适用              |
| ------------------------ | ---------------------------------------------------------------------- | --------------- |
| **A. hermes plugin（推荐）** | 写一个 `nexus-everos` 插件，暴露 recall / remember 两个 tool，内部调 EverOS REST API | 最贴合 hermes 现有架构 |
| **B. MCP server**        | 把 EverOS REST API 包成 MCP，挂到 hermes 的 `mcp/`                            | 若其他 Agent 也要复用  |
| **C. 直接 HTTP**           | hermes 的 tool 里直接 `fetch` EverOS REST API                              | 最快验证，但不够优雅      |

### EverOS 侧配置（零适配，OpenAI 协议直连）

```bash
EVEROS_LLM__BASE_URL=https://omn.360710.xyz/v1
EVEROS_LLM__API_KEY=<PSK>
EVEROS_EMBEDDING__BASE_URL=https://omn.360710.xyz/v1
# embedding 模型：nemotron-3-embed-1b（与 mem0 同款，2048 维）
```

> ✅ 已确认池里有可用 embedding 模型，语义检索成立。  
> 🎁 意外收获：`default.toml` 的 `[embedding]` 默认就是 `Qwen/Qwen3-Embedding-4B`（OpenAI 协议），**不改配置就有语义检索**，只需填 `api_key`（可走 OmniRoute 池或 DeepInfra）。

---

## 6. 仍然要补的两处（v4 已识别，此处重申 + 2026-09 修正）

### 补 1：EverOS 持久化（HF Storage Bucket 卷挂载 + symlink 拆索引）

> ✅ **2026-09 查证修正**：原 v4/v5 设想的"rclone → R2 兜底 `.md` + litestream 复制 `system.db` + LanceDB 快照"整套复杂度**已作废**。HF 现提供 **Storage Buckets**（免费额度内、S3 式、可 attach 为 Space 卷、读写默认），EverOS 记忆直接持久化，无需任何外部云备份（七牛 / R2 / 坚果云都不必为 EverOS 兜底）。

> 📊 **免费额度（2026-09-05/06 查 HF 官方 storage-limits + 官方论坛 discuss.huggingface.co）**：Bucket 继承账户级策略 —— **免费私有存储 100GB 硬额度**（超才 $18/TB/月 PAYG）；免费公共存储为 Best-effort（前几 GB 宽松，之后软性要求社区价值，无按 GB 封号硬线）。**EverOS 记忆作私有 Bucket（MB~几 GB ≪ 100GB）→ 零费用、低风控（非零）**：不撞 100GB 配额硬线；且 HF 确有内部"storage patterns"模式风控（`Your storage patterns tripped our internal systems`，pattern-based 非纯 GB，多在反复大批量/重试上传后触发），但 EverOS 走 Space 卷挂载(Xet)增量小写、非 git/LFS 批量上传路径，**实际风险低**——只要保持**私有 + 小体量 + 避免启动期狂灌/重试风暴/反复重建 bucket** 即可。Bucket 另豁免 Git 仓库结构限制（文件数/单文件/提交限制全不适用），只受账户总配额。

**做法（entrypoint.sh 本质两行）**：

```bash
# 0) 建一个 HF Bucket（免费），在 Space 设置里 attach 为卷，挂载路径如 /mnt/everos
# 1) EVEROS_ROOT 指到桶（.md 真相源持久）
export EVEROS_ROOT=/mnt/everos
# 2) 把 .index/ 换成指向本地 ephemeral 盘的 symlink（索引落本地快盘，避开 Xet 网盘脆弱性）
rm -rf "$EVEROS_ROOT/.index"
ln -s /tmp/everos-index "$EVEROS_ROOT/.index"
everos server start
```

**为什么这样拆（已扒 `memory_root.py` 源码确认）**：

- EverOS 的 `index_dir` / `lancedb_dir` / `sqlite_dir` / `system_db` 全是硬编码 `@property`，**强制 `root / ".index"`，无任何 env / config 可独立指定路径**（`default.toml` 的 `[sqlite]`/`[lancedb]` 只有 PRAGMA 参数，零路径项）。所以"把 `.index/` 单独配到本地"只能靠 **symlink**，不能靠配置。
- ✅ **`.md` 真相源在桶**：持久、重启免拉（旧方案要 rclone 拉回）。
- ✅ **`.index/`（SQLite+LanceDB）经 symlink 落本地**：快 + 避开 Xet 网盘挂载的脆弱性（SQLite 怕网络 FS、最终一致 ~10s、首读走网）。
- ✅ **重启只重建 `.index/`**：EverOS 扫描桶里 `.md` 自动重建（官方原话 "wipe `.index/` and the in-process cascade subsystem re-builds everything"）。`system.db` / LanceDB 因此**不再需要** litestream / R2 快照。
- ⚠️ 代价：symlink 需挂载 FS 支持；重建要重新 embedding（靠 OmniRoute 池里的 embedding 模型，已确认可用）。
- 🅱 **备选（更简单）**：整坨 `EVEROS_ROOT` 直接指桶，`.md`+`.index/` 都在 Xet 上。SQLite 在网盘有损风险，但索引可重生 → 崩了 `rm -rf .index/ && 重启`。

> 一句话：**EverOS 持久化 = 一个免费 HF Bucket + 一行 symlink，零 rclone / 零 litestream / 零 R2。**

### 补 2：EverOS 所在 Space 的保活

nexus 的保活 cron 已经跑通，**直接复用同一套机制**，把 EverOS Space 的 `/health` 加进 ping 列表即可（若 EverOS 与 hermes 不同 Space）。

---

## 7. 诚实的提醒：复杂度

终版栈共 **8 个组件**（2026-09 修正后，EverOS 不再额外引入 R2/rclone 备份链）：

| 组件               | 角色                      |
| ---------------- | ----------------------- |
| Telegram         | 交互入口                    |
| hermes（nexus）    | 云上大脑 / 路由调度             |
| mem0             | 热记忆（Neon pgvector）      |
| EverOS           | 深记忆（Markdown，HF Bucket 持久） |
| OmniRoute（n-omn） | 模型网关                    |
| Neon Postgres    | nexus 持久化主路             |
| Cloudflare R2    | nexus 灾备副路（Neon 快照）；EverOS 不再依赖 |
| Obsidian + 坚果云   | 人侧读写与同步                 |
| CF Worker        | 网关 / 保活 / FlareTunnel   |

**这是生产级系统，不是玩具。** 建议：

1. **不要一次性全上**。先跑通 hermes + mem0（你已经在跑），再按记忆量增长决定是否引入 EverOS。
2. **EverOS 是增量优化，不是必需品**。判断标准：你的 `MEMORY.md` 是否已膨胀到难以维护、是否需要"记忆能进 Obsidian 图谱"。
3. 沿用你的 HANDOFF SSOT 纪律记录每一次部署变更。

---

## 8. 落地 Checklist（增量部分）

- [x] Space 免费（已确认）
- [x] OmniRoute 池有 embedding 模型（已确认）
- [x] nexus 保活 cron 已跑通（可直接复用）
- [ ] 判断：当前 `MEMORY.md` 是否已需升级为 EverOS（若否，暂缓）
- [ ] 建 HF Bucket（免费），attach 为 EverOS Space 卷（挂载路径如 /mnt/everos）
- [ ] 部署 EverOS Space（端口改 **7860**，EVEROS_ROOT → 桶挂载路径）
- [ ] 写 entrypoint.sh：symlink `.index/` → 本地 ephemeral（索引留本地、.md 留桶）
- [ ] 验证重启后 `.index/` 自动从桶 `.md` 重建（零 rclone / 零 litestream / 零 R2）
- [ ] EverOS Space `/health` 加入保活 cron 列表
- [ ] 配置 `EVEROS_*__BASE_URL` 指向 OmniRoute
- [ ] 跑通 add → flush → search 最小链路
- [ ] 写 `nexus-everos` hermes 插件（recall / remember 两个 tool）
- [ ] Vault 建 `99-Memory/` 分区，落实单一写入者
- [ ] 按 HANDOFF 纪律记录本次部署

---

## 9. 一句话总结

> **加上 nexus，闭环真正成立。**  
> nexus 补上了 Agent 编排层（hermes）、持久化主路（Neon）、灾备副路（R2）和保活机制——这恰好是 v4 架构里缺的那几块。  
> **电报从"违规的存储后端"升华为"合规的交互入口"**；**mem0 与 EverOS 分工为热/深两层记忆**（黑盒向量 vs 人机共享 Markdown）。  
> 全栈零成本（免费 Docker Space + OmniRoute 聚合 + Neon/R2 免费额度 + 坚果云/七牛免费额度）。  
> **唯一还需动手的**：把 EverOS 的 `EVEROS_ROOT` 指到 **HF 免费 Bucket 卷** + symlink 拆出 `.index/`（索引留本地、.md 留桶）。SQLite/LanceDB 随索引本地重建，**不再需要 rclone / litestream / R2 兜底**——因为 Markdown 才是真相源，而它现在直接持久在桶里。
