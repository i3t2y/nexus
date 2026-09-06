# AI 书签知识库方案 v2：七牛云 + 坚果云 + EverOS 闭环

> 日期：2026-09-06 ｜ 取代 v1（`AI书签知识库方案-KVault+Karakeep+Obsidian+Telegram.md`）
> 数据核实来源：EverMind-AI/EverOS 仓库与 README、七牛云官方免费额度页、坚果云版本对比与帮助中心、思源/Joplin 官方文档、evermemos-mcp 仓库与 PyPI

---

## 0. 为什么要从 v1 演进到 v2

v1 方案（`K-Vault + Karakeep + Obsidian + Telegram`）经逐项核实后有四个硬伤：

| v1 的问题 | 核实结论 | v2 的解法 |
|---|---|---|
| 电报当存储后端 | 违反 ToS（当网盘），有封 Bot/删频道风险 | **七牛 Kodo** 扛合规容量（10GB/账号永久） |
| 电报不解析内容 | 只存字节，不认 `[[双链]]`，全文检索为零 | **坚果云 + Nutstore Sync** 让 vault 原生双链生效 |
| 检索要自建索引 | Bot API 无文件检索接口，得手搓 D1 索引 | **EverOS** 自带 BM25 + 向量混合检索 |
| 只能存不能"变聪明" | 静态仓库，无记忆抽取、无自演进 | **EverOS 的 OME 离线引擎**自动抽取事实/画像/技能 |

---

## 1. EverOS 是什么（核实结论）

| 项 | 内容 |
|---|---|
| 仓库 | `EverMind-AI/EverOS` |
| **曾用名** | **EverMemOS**（2026-09-04 官方提交专门澄清过改名） |
| 定位 | 面向 AI Agent 的**便携式记忆层**：local-first、Markdown 原生、用户自有、跨应用自演进 |
| 许可证 | **Apache-2.0** |
| 技术栈 | Python 3.12+（uv 管理）；DDD 五层单向依赖（import-linter 强制） |
| 存储 | **Markdown = 唯一事实来源** + SQLite（状态） + **LanceDB**（向量 + BM25 + 标量，可重建） |
| 活跃度 | 2026-06-05 首发 → v1.2.3（2026-08-07），94 commits，最新提交 2026-09-04；生态产品含 Raven、EverMe、SkillCorpus |
| 官方集成 | Raven（内置）、Dify、OpenClaw、Hermes、DeepSeek Harness |

### 最关键的两句话（来自官方 README）

> "It stores conversations, files, and agent trajectories as readable Markdown, then syncs local SQLite and LanceDB indexes."

> "Open any `<app>/<project>/users/<user>/` folder in **Obsidian** — your agent's brain is just files."

**这意味着：EverOS 从设计上就把 Obsidian vault 当作它的记忆根目录。** 它不是一个需要额外对接的外部系统，而是"长在你的 vault 上的记忆层"。

### 存储布局（决定了怎么和 Obsidian 共存）

```
~/.everos/
├── <app>/<project>/
│   ├── users/<user>/
│   │   ├── user.md              # 用户画像
│   │   ├── episodes/            # 日常记忆（可见，Obsidian 里直接读）
│   │   ├── .atomic_facts/       # 原子事实（隐藏）
│   │   └── .foresights/         # 预测性记忆（隐藏）
│   └── agents/<agent>/
│       ├── agent.md
│       ├── .cases/              # 任务案例（隐藏）
│       └── skills/              # 程序性记忆技能（可见）
├── .index/                      # 派生索引，可从 md 重建
│   ├── sqlite/system.db
│   └── lancedb/*.lance/
└── .tmp/
```

---

## 2. EverOS 补上的三块（这才是"闭环"的实质）

| v1 缺的能力 | EverOS 提供的对应能力 |
|---|---|
| **语义检索**：电报不索引、D1 要手搓 | **混合检索**：BM25 + 向量（HNSW/IVF-PQ）+ 标量过滤，LanceDB 单次查询搞定 |
| **记忆自演进**：只有静态存储 | **OME 离线记忆引擎**：闲时自动合并 episode 簇、精炼 profile 与 skills；带指数退避重试与运行记录 |
| **Agent ↔ 笔记双向**：笔记是死文件 | **Cascade watcher + REST API**：你在 Obsidian 改 `.md` → 秒级增量重索引；Agent 写回落成 `.md` → 直接出现在 Obsidian |

**闭环的本质一句话：笔记就是记忆，记忆就是笔记。**

---

## 3. 完整架构：六阶段闭环

| 阶段 | 组件 | 做什么 | 成本 |
|---|---|---|---|
| 1 采集 | Karakeep 扩展 / Obsidian Web Clipper / Floccus | 在已登录浏览器里抓网页与 x.com 长文，落成 `.md` | ¥0 |
| 2 AI 原子化 | Ollama 本地模型 / 七牛内置 AI | 拆成原子笔记，补标签、摘要、`[[双链]]` | ¥0（本地推理） |
| 3 存储脊柱 | 七牛 Kodo（S3）+ 坚果云（Nutstore Sync） | 七牛扛合规容量与附件；坚果云做 Obsidian 双向增量同步 | ¥0（免费额度内） |
| 4 Obsidian vault | 双链 / MOC / Dataview / Omnisearch | 唯一的人类可读层，也是 EverOS 的唯一事实来源 | ¥0 |
| 5 EverOS 记忆层 | Markdown → LanceDB + OME | 改 `.md` 秒级重索引；闲时抽取原子事实、画像、技能 | ¥0（配 Ollama） |
| 6 Agent 闭环 | REST API / MCP | `recall` 混合检索带溯源；`remember` 写回落成 `.md` → 回到阶段 4 | ¥0 |

---

## 4. 各组件职责与数据流向

```
采集 → AI原子化 → 七牛/坚果云(存储) → Obsidian vault(双链)
                                            ↓  memory root
                                      EverOS(LanceDB + OME)
                                            ↓  REST / MCP
                                        Agent(recall/remember)
                                            ↓  写回 .md
                                      回到 Obsidian vault  ⟲
```

| 组件 | 职责 | 不可替代性 |
|---|---|---|
| **七牛 Kodo** | 合规容量 + 附件归档 + 内置 AI（HTML2Markdown / 视觉理解） | 10GB/账号**永久**免费，多账号叠加；上传流量免费 |
| **坚果云 + Nutstore Sync** | Obsidian 双向增量同步、字符级冲突合并、历史版本 | 目前**唯一**由云厂商官方出品的 Obsidian 同步插件 |
| **Obsidian** | 双链、MOC、Dataview 检索、人类可读层 | `[[wikilinks]]` 只在 vault 内解析，不可替 |
| **EverOS** | 语义检索、记忆抽取、自演进、Agent 记忆接口 | 唯一 Markdown 原生 + 可自托管的 Agent 记忆层 |
| **Ollama** | 本地 LLM / embedding，让 EverOS 零 API 成本 | 替代 OpenRouter/DeepInfra，是"零成本"的关键 |
| **Karakeep**（可选） | AI 标签/摘要、整页归档 | 若要存 x.com 整页快照，仍需它（跑免费 VM） |
| **Telegram / K-Vault**（可选降级） | 二进制附件镜像、手机端通知 | v2 中**非必需**，可退场 |

---

## 5. 零成本可行性核算

| 组件 | 免费额度 | 是否真零成本 |
|---|---|---|
| 七牛 Kodo | 10GB 标准存储/月（永久）、CDN 回源 10GB、PUT/DELETE 10万、GET 100万、上传流量免费 | ✅ 是（多账号可叠加） |
| 坚果云 | 上传 1GB/月、下载 3GB/月；空间随上传量 | ✅ 是（文本增量同步远低于上限） |
| EverOS | Apache-2.0 自托管 | ✅ 是（**前提：LLM 走本地 Ollama**） |
| Ollama | 本地推理 | ✅ 是（需一台机器，可用现有电脑或 Oracle 免费 ARM VM） |
| Cloudflare（可选缓冲层） | R2 10GB、Cron 免费 | ✅ 是 |
| **必需的付费项** | — | ❌ **EverOS 默认要 OpenRouter + DeepInfra key；必须改配 Ollama 才能免费** |

### 免费额度对知识库够不够？

- 3000 篇 `.md` ≈ 50–200MB → 七牛 10GB 可装 **50–100 倍**当前体量。
- 图片压 WebP（长边 1920）每张 ~200KB → 10GB ≈ 5 万张。
- 坚果云增量同步每天写几篇笔记，流量是 KB–MB 级，月度 1GB/3GB 远到不了上限。

**结论：个人知识库场景下，免费额度宽裕，够用。**

---

## 6. EverOS 部署要点（实操，含必须避开的坑）

### 6.1 安装与启动

```bash
uv pip install everos          # 或 pip install everos；需 Python 3.12+
everos init --root <你的 vault 子目录>     # 关键：指向 vault 下的子目录，不要直接指向 vault 根
everos server start                        # 默认 127.0.0.1:8000
curl http://127.0.0.1:8000/health          # 期望 {"status":"ok"}
```

### 6.2 改成本地 Ollama（零 API 成本的关键一步）

默认配置要求 OpenRouter + DeepInfra 两个 key。改 `.env` / `everos.toml`：

```bash
EVEROS_LLM__BASE_URL=http://localhost:11434/v1
EVEROS_LLM__API_KEY=ollama
EVEROS_EMBEDDING__BASE_URL=http://localhost:11434/v1
EVEROS_EMBEDDING__MODEL=nomic-embed-text      # 或 embeddinggemma
EVEROS_EMBEDDING__API_KEY=ollama
```

> 官方明说端点栈是 **OpenAI 协议兼容**（OpenAI / OpenRouter / vLLM / **Ollama** / DeepInfra / 阿里云 DashScope），改 `*__BASE_URL` 即可指向任意一个。
> 不上 embedding 也能跑，但**只有关键词检索**，语义检索会失效——建议务必配。

### 6.3 核心 API

| 接口 | 作用 |
|---|---|
| `POST /api/v1/memory/add` | 写入对话/文件/轨迹（`session_id`、`app_id`、`project_id`、`messages[]`） |
| `POST /api/v1/memory/flush` | 落盘为 Markdown |
| `POST /api/v1/memory/search` | 混合检索（`user_id`、`query`、`method`、`top_k`） |

### 6.4 ⚠️ 三个必须避开的坑

1. **同步排除清单（最容易翻车）**
   EverOS 会在 memory root 下生成 `.index/`（SQLite + LanceDB）与 `.tmp/`，这些**绝不能被坚果云/七牛同步**——二进制索引多端同步会膨胀与冲突。
   在 Nutstore Sync / Remotely Save 的排除规则里加上：
   ```
   .index/
   .tmp/
   .atomic_facts/
   .foresights/
   .cases/
   ```
   只同步可见层：`episodes/`、`user.md`、`agent.md`、`skills/`。
   好消息：索引可从 Markdown **完全重建**，丢了也不致命。

2. **MCP 的坑：官方生态的 MCP 是 Cloud-first**
   `tt-a1i/evermemos-mcp`（MIT，7 个工具：list_spaces / remember / request_status / recall / briefing / forget / fetch_history）默认连的是 **EverMemOS Cloud**（`api.evermind.ai`），**需要 `EVERMEMOS_API_KEY`，不是本地自托管**。
   - 想省心 → 用 Cloud 版（但引入外部依赖与费用）
   - 想纯本地 → 直接用 EverOS 自带 REST API，自己包一层薄 MCP；或把 `EVERMEMOS_BASE_URL` 指向 `http://127.0.0.1:8000`（**非官方支持路径**，生命周期状态如 `queued/provisional/searchable` 可能降级）

3. **EverOS 必须常驻本地进程**
   它是 Python 服务，绑 `127.0.0.1:8000`，**不能部署到 Cloudflare**。所以"纯 CF 无服务器"这一层要放弃——但你本来就要跑 Ollama，多一个进程而已。

---

## 7. 功能边界与局限（诚实清单）

1. **EverOS 不是书签工具**：它不做书签管理/整页归档，只是记忆层。书签采集仍靠 Karakeep / Web Clipper / Floccus。
2. **EverOS 需要一台常开机的机器**：跑 Python 服务 + Ollama。纯 CF 方案不可行。
3. **本地模型质量有上限**：Ollama 3B/4B 的记忆抽取质量明显弱于云端大模型；要质量需 7B+（速度换质量）。
4. **MCP 闭环非官方**：`evermemos-mcp` 默认走 Cloud，纯本地 MCP 需自行适配。
5. **项目很新**：EverOS 2026-06 才首发（v1.2.3），API 存在 v1/v2 混用痕迹，目录结构可能变动，需接受早期项目风险。
6. **坚果云流量是硬上限**：若 vault 塞满未压缩 PDF/原图，且频繁在手机端全量拉取，1GB 上传 / 3GB 下载/月可能吃紧——解决：大附件放七牛，vault 只放文本 + 压缩图。
7. **多账号管理有运维成本**：七牛多账号意味着多套 AK/SK 与 bucket，需要自己规划分桶策略。
8. **`.md` 双链只在 vault 内解析**：七牛/坚果云只存字节，不解析 `[[链接]]`；双链与原子化始终是 Obsidian 侧的活。

---

## 8. 落地 Checklist

- [ ] 注册七牛云 → 建私有 bucket → 记下 Endpoint / AK / SK
- [ ] 注册坚果云 → 安装 **Nutstore Sync** 官方 Obsidian 插件 → 一键授权
- [ ] 配置同步排除规则（`.index/` `.tmp/` 等，见 6.4）
- [ ] 安装 Ollama → 拉 `nomic-embed-text`（embedding）+ 一个 7B 级聊天模型
- [ ] `uv pip install everos` → `everos init --root <vault 子目录>`
- [ ] 改 `.env` 指向 Ollama（见 6.2）→ `everos server start` → 验证 `/health`
- [ ] 跑通 `/api/v1/memory/add` → `flush` → `search` 最小链路
- [ ] 在 Obsidian 里改一个 `.md`，确认 EverOS watcher 秒级重索引
- [ ] 接 Agent：REST API 直接调，或自包一层 MCP（纯本地）/ evermemos-mcp（Cloud）
- [ ] 采集端就位：Web Clipper 或 Floccus 或 Karakeep 扩展

---

## 9. v1 vs v2 取舍对照

| 维度 | v1（K-Vault+Karakeep+Telegram） | v2（七牛+坚果云+EverOS） |
|---|---|---|
| 存储合规 | ❌ 电报违反 ToS | ✅ 全合规 |
| Obsidian 同步 | ❌ 无原生方案 | ✅ Nutstore Sync 官方双向增量 |
| 语义检索 | ❌ 需手搓 D1 索引 | ✅ EverOS 混合检索 |
| 记忆自演进 | ❌ 无 | ✅ OME 离线引擎 |
| 零 API 成本 | ✅（Ollama） | ✅（Ollama，需改配置） |
| 纯 CF 无服务器 | ✅ | ❌ EverOS 需常驻本地进程 |
| 运维复杂度 | 中 | 中（多一个 Python 服务） |

**取舍一句话：v2 用"放弃纯 serverless"换来了"合规 + 语义检索 + 记忆自演进 + Agent 闭环"。**
如果你的核心诉求是"知识能被真正检索和用起来"，v2 明显更优；如果你坚持"绝不养任何常驻进程"，则 v1 的 CF 部分仍可保留作为采集与缓冲层。
