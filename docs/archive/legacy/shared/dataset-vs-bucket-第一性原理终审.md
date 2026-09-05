# Dataset vs Bucket 介质选型 — 首长第一性原理终审

> 2026-08-01。用户显令(C 路:推翻 2026-07-28 §12 裁决)以首席架构师身份、按第一性原理重审 dataset vs bucket(单或组合),针对 omniroute 与 nexus 4 组件(hermes / langgraph / claude-code / codex)。五视角 agent 独立审(V1 中立八维 / V2 Dataset 专攻 / V3 Bucket 专攻 / V4 反方证伪 §12 / V5 频率第一性)+ 一代码实情核 + 自证伪,合判交付。本文与 [[hermes-agent-换装方案]]、[[nexus-agent-team判定]]、[[nexus最新架构-查证]] 并列作换装方案的存储介质终审章。

## 0. 一句话终审

§12 "三轴分层定局" 在 **nexus 侧部分坍塌(对 nexus 是未施蓝图)**、在 **omn 侧不坍塌(omn Dataset 四件武器真跑非纸面)**。两仓分叉不可互相套用:omn 维持 Dataset+Bucket+R2 三轴;hermes 现役 a142da9 单 Bucket+R2 跑稳,返 Dataset 是增回滚锚非纠错;三下游(langgraph/claude-code/codex)Bucket 与 Dataset 皆无,只有 Supabase+R2。

C 路 "推翻 §12" 边界:hermes 侧立、omn 侧不立。

## 1. 基石:两物理属性 + 一个反证

挂载介质选型由两物理属性决定,非偏好:

| 属性 | Dataset | Bucket | 互斥? |
|---|---|---|---|
| RW 挂载 | ❌ 永远 RO(EROFS,manage-spaces 铁锁:"Only storage buckets support read-write mounts") | ✅ HF 官方唯一 RW mount | 是 |
| 就地版本化 | ✅ git 全套(commit_id 锁/PR/atomic 全件/回滚锚/blood sha256) | ❌ non-versioned + overwrite-in-place,无锚 | 是 |

两属性互斥 → **单介质物理无两全**。此即撬出第三介质(R2)的硬约束根。

**第三支柱(V4 反证补):"运行态真持久"不在 Bucket 也不在 Dataset,在 R2 + Supabase。** Bucket 仅 ephemeral 就地 RW 卷,允许丢(最强模板.md:82-84 框图原文:"运行态持久件层 R2 via litestream + /data ephemeral;/data 卷 = 允许丢,R2 是数据主路径")。

## 2. three pillars 各居一介质(物理硬约束逼出)

| 支柱 | 介质 | 物理硬义 |
|---|---|---|
| 就地 RW 挂载 | Bucket | HF 域唯一 RW mount(Dataset/Model/Space 永远 RO=物理 EROFS 不可用) |
| 就地版本化(回滚锚/PR/原子) | Dataset | git 语义专属(Bucket 无锚覆盖不留旧版) |
| 跨云真持久(litestream/PITR/manifest) | R2+Supabase | 跨云容灾,Bucket ephemeral 允许丢 ← R2 才是真主路径 |

任留其一介质必断裂:单 Bucket 失回滚锚、单 Dataset 写不动 RW 件、单 R2 非挂载态且 §1 铁律禁动。

### 2.1 削 git 全套后 Dataset 全面逊于 Bucket(首长硬论,2026-08-01)

把 git 全套(回滚/PR/原子/锚)从天平上拿掉,Dataset 与 Bucket 在"非 git"维度全平,并在两维 Bucket 独胜:

| 能力 | Dataset(削 git 后) | Bucket | 胜者 |
|---|---|---|---|
| 持久跨重启 | ✅(HF 域 git 仓) | ✅(HF 域 Xet 对象存储) | **平**(hf download vs hf buckets sync 等价 boot 拉取) |
| 配额 | Free 100GB 共享同账户(V1 核 storage-limits) | Free 100GB 共享同账户 | **平** |
| 凭证就位 | HF_TOKEN | HF_TOKEN | **平** |
| **RW 挂载** | ❌ 永远 RO(EROFS 物理不可写) | ✅ **HF 官方唯一 RW mount** | **Bucket 独胜**(不可让) |
| 件数/配额限制 | git 仓限制(<100k 件 / commit 退化) | **豁免 git 仓库限制**(V1.storage-limits) | **Bucket 独胜**(高频小件不撞退化) |
| 改件秒级生效 | 须 Restart 拉新副本(非运行态) | **运行态 RW 改件秒级 live mount**(无 Restart 无 Rebuild) | **Bucket 独胜** |

→ **削 git 后 Dataset 无一胜项,Bucket 全包且三头独胜。** Dataset 仅存的全部价值 = git 全套本身。留 Dataset 的唯一活口 = "要不要 HF 域内自带 git 回滚锚"一问。

被 GitHub 私库对冲则活口封(nexus 侧封:spaces/hermes/ 本是 git 仓库有 git 血缘 + sync-spaces CI 已建)→ **Dataset 退役,Bucket+R2 单挂即足。**

此即至紧终审:nexus 侧削 git 后 Bucket 单挂 + R2 已全包 Dataset,无需补四件武器返蓝图。omn 侧唯一挡刀的是沉没成本(`sync-logic-nonoke.yml` 四件武器 CI 已建真跑)——剔除沉没成本对新收益,omn 削 git 后 Dataset 亦逊于 Bucket,理论可切 Bucket 单挂 + GitHub 私库血缘,省一个 Dataset 仓 + 统一介质,但切换有成本(动已跑稳 CI),新收益薄。

频率=二阶修正非主轴(V5 与自推一致:30d 改 203 次 vs 14 commit 不独立成选介质轴)。

## 3. 件分三类 → 介质正交分配

件按需求属性(非频率)分三类:

| 件类 | 真实持久锚需求 | 现役介质 | 判 |
|---|---|---|---|
| **I 源码件**(app/scripts/libs/插件/init/gate) | 需**回滚锚**(改坏可退) | hermes=Bucket(a142da9);omn=Dataset;3 下游=GitHub 私库+镜像 | 回滚锚在哪介质就该在哪。hermes 走 Bucket = 无锚可退(V4 A3) |
| **IIa SQLite 运行态件**(state.db/storage.sqlite 对话/会话) | 跨云真持久+RW | hermes=R2 litestream+Bucket ephemeral;omn=R2(仅监 storage.sqlite) | R2 已真路径。Bucket 是就地写盘非持久锚(V4 A2) |
| **IIb 非 SQLite 运行态件**(marker/.or-api-key/日志/config/插件目录) | 就地 RW 持久(非跨云) | hermes=Bucket;omn=ephemeral(**真缺口**) | 仅 Bucket 唯一 RW 挂载。omn 此类件无兜底 = §12 真痛点 |
| **IIc 结构化/Postgres 件**(langgraph thread 状态/checkpointer) | 跨云真持久 | 三下游=Supabase AsyncPostgresSaver + R2 checkpoint blob | 介质=Supabase+R2,Bucket/Dataset 均非主 |

## 4. omn 与 nexus 血统分叉(决定性,易混点)

**omn 现役四件武器真跑非蓝图**:
- `.github/workflows/sync-logic-nonoke.yml` 真跑 sha256 readback 血缘校验 + 私库三件流水(私库源 → Dataset → Actions)
- `start.sh` 走 `hf download --revision $_rev` commit_id 锁竟速根治(同 commit atomic 全件)
- 与 nexus 不同:nexus 现役四件武器全零用(V4 A1 证伪结果,见 §6)

**nexus 现役 a142da9 已改 Bucket rw 单挂逻辑层**:
- `spaces/hermes/start.sh:53` 走 `hf buckets sync` 非 `hf download --revision`
- `scripts/sync-logic-bucket.sh:62` 同名,hf buckets sync 不检 commit_id
- `commit_id` 字样在 spaces/scripts/libs 运行态 `grep` **0 命中**
- sha256 CI 仅停留在 `docs/new/` 模板,现役 `.github/workflows/` 无 `sync-logic-*.yml`
- CommitScheduler 已废,改 `persist_to_r2.py` R2 5min sidecar + litestream WAL
- 最强模板.md:90 修正项④自承:"只读 Dataset Volume 挂载是 audit:268 本轮勘探裁决闭环非实施 的未施蓝图;Nexus 现役已落 a142da9 改走 Bucket rw 单挂逻辑层"

→ **§12 "Dataset 留逻辑层五件" 对 omn 是现役,对 nexus 是未施蓝图。** 两仓血统分叉不可互相套用。

## 5. 逐组件终局推荐

### omniroute(omn)— 维持 §12 三轴分层

- **逻辑层五件 = Dataset** ✅(现役已是,四件武器真跑非蓝图 → V4 A1 证伪**不适用 omn**)
- **IIb 运行态件(.or-api-key/.init-done/LOG_DIR)= Bucket 挂 /data RW**(§12 已签未实施)。现役 ephemeral = 真痛点。env fallback 在(init-nim-keys.sh:564-567),但 litestream.yml 仅监 `/app/data/storage.sqlite`,不监 `/data/.or-api-key`/`/data/.init-done` 字面路径 → 两状态件真未入 R2 兜底。增 量门(init-nim-keys.sh:1133 `COMBO_COUNT>0 || -f INIT_MARKER`)部分缓解 marker 丢不全量重跑,但 R2 restore 复活 combo 时 marker 丢仍走增量不全量 409 之痛部分过期(V3 夸大被自证伪)。
- **storage.sqlite = R2 litestream**(现役已是)

→ **omn = Dataset(逻辑层四件武器真用)+ Bucket(IIb 运行态件,补实施)+ R2(SQLite)**。C 路 "推翻 §12" 在 omn **不适用** —— 推翻一个现役真用护盾无益。

### nexus hermes — 单介质 Bucket + R2(现役 a142da9 结构)

- **逻辑层 = Bucket /data**(现役)。nexus 侧四件武器**现役零用**(V4 A1) → 回退到 Dataset 须补齐 commit_id 锁/PR/sha256 CI/CommitScheduler 四件才不退化;现役零用基线上 Bucket 覆盖已跑稳,补 Dataset 回滚锚是"启用蓝图"非"保武器"(V4 锁定此重述)。
- **state.db = R2 litestream**(现役,`start.sh:93-104` restore+replicate 双向 + L141 watchdog)
- **IIb 件(config.yaml/插件目录/日志)= Bucket ephemeral**(现役)

→ **hermes = 单介质 Bucket(逻辑层+IIb 就地 RW)+ R2(state.db 真持久)**。§12 "逻辑层该回 Dataset" 对 hermes 是未施蓝图非返修 —— a142da9 跑稳,返 Dataset 是增回滚锚(用户赋权决定),非纠错。**第一性原理判:hermes 维持现役单 Bucket,需回滚锚再补 Dataset(二线)非必为**。

注:hermes 逻辑层真源在 GitHub 私库(`spaces/hermes/` 本身是 git 仓库有 git 血缘 + sync-spaces CI 已建),故 "Bucket 挂载态失血缘" 的代价部分被 GitHub 私库上游血缘对冲 —— Bucket 仅是挂载态副本非真源,真源 git revert 仍在 GitHub 侧可用。此点弱化 V1/V2 "hermes 失 git 血缘" 痛点。

### langgraph / claude-code / codex — 无 Bucket 无 Dataset

- 纯 thin proxy。`spaces/claude-code/app/main.py:45-84` 实证:`/run` 直透 Anthropic Messages API,无 SQLite 无本地件,状态走 `save_state`(Supabase)+`save_checkpoint`(R2 blob)。
- 逻辑层真源在 GitHub 私库(镜像层 COPY),git 血缘 + sync-spaces CI 已建(`sync-spaces.sh:11-14` hermes 剔出 SPACES 注 "逻辑层进 HF Storage Bucket 不再走 git 副本 build context")。
- langgraph checkpointer=`shared/checkpointer.py:61` `AsyncPostgresSaver.from_conn_string`,非文件;`thread_id.json` 是 R2 object key(`storage.py:48`),非本地件(V3 "thread_id 丢对话断裂"痛点不存在,已走 Postgres+R2)。

→ **三下游 = GitHub 私库(逻辑层源)+ Supabase(结构化状态)+ R2(blob 备份)**。Bucket/Dataset 对三下游均非必需,维持现役不动。

## 6. V4 证伪结果摘要(对 §12 的击中点)

| 假设 | 判 | 击中 |
|---|---|---|
| A1 四件武器现役已用 | **证伪** | commit_id 0 命中/PR 未开/sha256 CI 仅模板/CommitScheduler 已废改 R2 sidecar;nexus 侧 §12 Dataset 系未施蓝图(omn 侧不适用,A1 只击中 nexus) |
| A2 运行态 RW 件层只能 Bucket | **夸大** | litestream start.sh:93-104 双向真落,state.db R2 是主路径 Bucket 是 ephemeral;Postgres 走 Supabase PITR |
| A3 运行态件低版本需求 | **部分** | §15.2 回滚锚指向 R2/Supabase/Dataset 非 Bucket;仅 marker/init-done/日志真低版本需求,state.db/config/插件/skill 非也 |
| A4 Bucket 故障有 Dataset /logic 兜底 | **证伪** | 兜底是 `hf buckets sync` 拉同 Bucket 非 Dataset /logic;/logic 仅 omn 血统模板,Nexus 现役无 |
| A5 R2 不动+永不双写 | **成立+窄化** | R2/HF Bucket 真分离正交无同份 state 双写;但 "R2 不动" 字面过窄 — R2 已跨多桶多职非仅 litestream 副本 |
| A6 痛点 Rebuild 非 Restart 403 | **部分** | sync-logic 已落日常零 Rebuild,Rebuild 窄化为升依赖单点撞墙;Restart 403 文档无明示已不是痛点(仅可从 sync+Restart 永不 git push 间接推断) |

## 7. 单/组合对答(用户正题)

| 组件 | 介质方案 | 单 / 组合 |
|---|---|---|
| omniroute | Dataset 逻辑层 + Bucket IIb + R2 SQLite | **组合(三轴分层)** |
| hermes | Bucket 单挂(逻辑层+IIb)+ R2(state.db) | **单介质(Bucket)+ R2 兜底** |
| langgraph(下游) | GitHub 私库(逻辑层)+ Supabase(状态)+ R2(blob) | 无 Bucket/Dataset |
| claude-code(下游) | 同 langgraph | 无 Bucket/Dataset |
| codex(下游) | 同 langgraph | 无 Bucket/Dataset |

组合只在"持久锚跨介质"层出现(R2+Supabase 是所有组件的真持久共性);Bucket/Dataset 的单/组合分叉留给 omn(Dataset+Bucket 组合)与 hermes(Bucket 单)。

## 8. 遗留真问题(三轴与单介质都不解,V4 锁定)

**HF Bucket 服务本身宕 → Space 起不来,无第二存储兜底。** 现 bootstrap 是 `hf buckets sync` 重试 while 自愈(start.sh:128-146),非 Dataset /logic fallback。若 HF Bucket 域 s3.hf.co 服务本身 Droped,Space 永世起不来。

此问非 dataset-vs-bucket 主轴,是 Bucket-HA 单点。三轴分层与单介质方案都不解此单点。两选待显令定:

1. 接受单点 + while 自愈重试协议(现役);或
2. 真补二线存储:把逻辑件镜像进 R2 某桶做 fallback(逻辑层真二线)+ 补 Dataset /logic 真 fallback(对 hermes 是启用蓝图回滚锚,对 omn 是无益因 Dataset 已在)。

## 9. 待显令两件

1. **hermes 是否启用 Dataset 回滚锚**(返蓝图):现役 a142da9 零用四件武器 + GitHub 私库上游已有 git 血缘对冲,启用非必为。是否补齐 commit_id 锁/PR/sha256 CI/CommitScheduler 四件武器回 Dataset,取决用户给"逻辑层挂载态回滚锚"赋多少权重。
2. **Bucket-HA 单点兜底要不要真做二线**:三轴与单介质都不解,是独立于 dataset-vs-bucket 主轴的 Bucket-HA 问题。

## 附:关键代码实证锚点(绝对路径)

- `/home/laisi/nexus/spaces/hermes/start.sh`(L39-56 bootstrap_from_bucket 用 `hf buckets sync` 非 Dataset /logic;L93-104 litestream restore+replicate 双向;L128-146 while 自愈重试)
- `/home/laisi/nexus/scripts/sync-logic-bucket.sh`(L62-63 push 到 HF Bucket + Restart 不触 Rebuild)
- `/home/laisi/nexus/spaces/hermes/app/agent_server.py:46-47`(session_db=/data/.hermes/state.db 注入 AIAgent,运行态件非逻辑层,走 R2 非 Bucket 持久)
- `/home/laisi/nexus/spaces/hermes/scripts/litestream.yml`(L9-20 type:s3+R2 endpoint+sync 10s,path:/data/.hermes/state.db)
- `/home/laisi/nexus/spaces/hermes/scripts/persist_to_r2.py`(L32,126-132 R2 5min sidecar;L94-107 sha256+manifest 登记)
- `/home/laisi/nexus/spaces/hermes/scripts/restore_from_r2.py`(L97-111 sha256 复算比对门;L83-149 反向恢复 time-travel)
- `/home/laisi/nexus/spaces/claude-code/app/main.py`(L45-84 纯 thin proxy 透 Anthropic Messages,无 SQLite)
- `/home/laisi/nexus/libs/storage/storage.py:48`(save_checkpoint 写 R2 object `{thread_id}.json`,key 非本地件)
- `/home/laisi/nexus/libs/shared/checkpointer.py:61`(langgraph 走 Supabase AsyncPostgresSaver 非 Bucket 非本地件)
- `/home/laisi/nexus/docs/new/Nexus集群永续架构最强模板.md`(L90 修正项④自承 §12 系未施蓝图;L82-84 R2 是运行态主路径;L844-863 sha256 CI 仅模板;L1461-1468 §15.2 回滚锚指向 R2/Supabase/Dataset 非 Bucket;L514 修正项⑧ litestream 仅 SQLite)
- `/home/laisi/nexus/docs/ARCHITECTURE.md`(L130-145 R2 与 Bucket 分离正交只用 Bucket 不建 dataset repo;L140 引 HF 官方 low-version-need 适用场景)

## 附:omn 侧对照(为何不适用 V4 A1 证伪)

`/home/laisi/omn-merge/audit/2026-07-28-storage-bucket-勘察.md` §12 定局段(本仓文档未含此文件,见最强模板.md:90 修正项④转引):
- §12 三轴分层对 omn:R2 不动 / Dataset 留逻辑层五件(版本化+PR+血缘+K3 commit_id 锁四件武器现役已用)/ Bucket 挂 /data RW 作运行态持久件层
- omn 真痛点实证:`/data`=ephemeral(官方铁义 lost if Space restarts);运行态写件四枚 `.or-api-key`/`.init-done`/LOG_DIR/storage.sqlite 现役丢/重生成崩溃链
- omn 侧 V4 A1 证伪不适用:`omn-merge/.github/workflows/sync-logic-nonoke.yml` 真跑 sha256 readback + commit_id 锁,四件武器**真跑非纸面**

故 C 路推翻 §12 在 omn 侧不立。

## 10. nexus 4 组件架构第一性原理终审(满血 / 存储 / 通讯,2026-08-01)

用户显令:claude-code、codex 满血;langgraph 是否需要满血?四组件是否都需要存储?四组件间该如何通讯?本节为该令首长终审,与 §5 存储介质终局接续 —— §5 定"挂什么介质",§10 定"组件本身是什么形态 + 如何对话"。

### 10.0 物理基底:四组件各是什么

| 组件 | 本质 | 性质 | 现役形态 |
|------|------|------|---------|
| **hermes** | 主控 / orchestrator | agent loop + tool 集合 + state.db | NousResearch Hermes Agent 内核,路 B 调下游 |
| **claude-code** | 执行器 / executor | LLM 推理引擎 | **退化**:HTTP thin proxy 直打 Anthropic API |
| **codex** | 执行器 / executor | LLM 推理引擎 | **退化**:HTTP thin proxy 直打 OpenAI API |
| **langgraph** | 编排器 / orchestrator | graph orchestration + checkpointer | library 形态 in-process,Supabase Postgres saver |

关键:hermes=指挥、langgraph=指挥、claude/codex=士兵。**两指挥 + 两士兵**,非四等并。

### 10.1 满血三问

**claude-code 满血 —— 要。** 现役 `spaces/claude-code/app/main.py:74` 直打 `/v1/messages` = 把 Claude Code 当 Messages API 用,打死只返文本。Claude Code 真身 = CLI agent loop:`claude -p --bare --allowedTools --output-format json --json-schema`,带 tool use、会话恢复、结构化输出、allowedTools 沙箱。现役扔掉全套 agent 能力只取一个推理 lane = 退化浪费。满血形态(阶段四方案二,见 §10.4)= 常驻 uvicorn 外壳 + 每任务 `subprocess` 起 `claude -p` 子进程 + ephemeral `/tmp` git worktree + wall-clock 超时 kill + JSON 结构化返 + patch 上 R2。

**codex 满血 —— 要。** 同理。Context7 核 `/openai/codex` codex-rs/exec/src/cli.rs:`codex exec [opts] [prompt]` flag 集 = `--json`(JSONL)/`--output-schema FILE`(等 `claude --json-schema`)/`-C <dir>`(cwd 进 worktree)/`--ephemeral`/`-o`/`--output-last-message`/`resume --last`(等 `claude --resume`)/`--skip-git-repo-check`/`exec review` 子命令。与 `claude -p` **对等 dual 执行器** 契基。现役 `spaces/codex/app/main.py:72` 直打 `/chat/completions` 同退化。codex 可经 omniroute(`OPENAI_BASE_URL` env 现役已支持重指)。

**langgraph 满血 —— 不要 CLI 化,有个"要不要上 Server 形态"的真问题。**

先破概念混淆:langgraph 与 claude/codex 不同物。
- claude/codex = LLM 推理引擎(provider)。满血 = 访问代码库 + tool loop + 结构化产出(执行器面)。
- langgraph = orchestration framework(编排框架,非推理引擎)。它"满血"不指 CLI 化,指**用其真身编排能力**。

现役 langgraph space:`graph.ainvoke()` + `AsyncPostgresSaver` checkpointer(`libs/shared/checkpointer.py:61`,见 §5)。**这已是 library 形态满血** —— StateGraph 真身 + Postgres checkpointer 跨 Space 状态续 + R2 blob 归档(`storage.py:48`)。无 CLI 可升。

langgraph 有两形态(Context7 `langgraph dev/up` 核):
- **library 形态(现役)**:`graph.ainvoke()` in-process + 自配 checkpointer。轻,与 HF Space 单进程共生。
- **Server 形态**:`langgraph dev/up` 自带 durable execution runtime(Docker + `langgraph.json` config + Task Manager + Cron + Webhooks + Store API),崩溃续、定时、Webhook 触发。

Server 增量价值窄 —— 现役 Postgres checkpointer 已给跨重启状态续(checkpoint 存 Supabase),Server 多给的是 runtime 级调度(Cron/Webhook),nexus 用 hermes 主控 loop 指挥已覆盖任务调度,Server 与 hermes 主控职能重叠且需 Docker 自管与 HF 单进程共生冲突。

→ **判:langgraph 维持 library 形态。"满血"已达到(真身 StateGraph + Postgres saver)。** Server 化是阶段四以后选项,非必须。

**满血语义三类不能一刀切:**
- claude/codex 满血 = **升 CLI 执行器**(执行器面,现役退化须升)
- langgraph 满血 = **用真身编排 + checkpointer**(编排面,现役已满)
- hermes 满血 = **NousResearch Hermes Agent 内核**(已换装完成,见 [[hermes-agent-换装方案]])

### 10.2 是否都需要存储

按件分类配介质(三类介质第一性根,见 §3 + §5):

| 组件 | Bucket(rw 挂载) | Dataset(git 仓) | R2(blob 持久) | Supabase(结构态) | GitHub 私库(源码) |
|------|:-:|:-:|:-:|:-:|:-:|
| **hermes** | ✅ 逻辑层 + state.db(litestream→R2) | ❌ GitHub 对冲封 | ✅ state.db WAL + 四表快照 | ✅ agent_states/task_queue | ✅ 逻辑层源 |
| **claude-code** | ❌ 现役无;阶段四 worktree 是 ephemeral `/tmp` 非持久 | ❌ | ✅ 阶段四 patch_artifact 归档 | ✅ 任务 log | ✅ 项目真相源 |
| **codex** | ❌ 同 claude-code | ❌ | ✅ 同上 | ✅ 同上 | ✅ 同上 |
| **langgraph** | ❌ | ❌ | ✅ save_checkpoint `thread_id.json` | ✅ AsyncPostgresSaver(主态源) | ✅ 逻辑层源 |

总判:
- **Bucket = 仅 hermes**(及 omn IIb 件,见 §5)。三下游不需 —— 产出去 R2/Supabase,worktree 是一次性 ephemeral 非持久件(non-versioned 挂载对 ephemeral /tmp worktree 无增量价值)。
- **R2 + Supabase = 四组件全共**,无例外。
- **GitHub 私库 = 四组件全共**(逻辑层源 + 阶段四项目真相源)。
- **Dataset = 全退役**(被 GitHub 私库 git 回滚锚对冲封,见 §2.1)。

阶段四 worktree 真相源 = GitHub 私库 shallow clone ephemeral `/tmp`,完销;patch 上 R2 `nexus-artifacts`(见 HANDBOOK.md:186)。**真相源不在三下游长驻** —— 三下游是执行瞬态非真相归宿。

### 10.3 四组件间通讯

**现役 = 同步 HTTP 薄契约:**

```
hermes ──HTTP POST──> {claude-code /run, codex /complete, langgraph /execute}
       经 call_space 透 {thread_id, prompt, request_id}
       同步等返
```

`spaces/hermes/scripts/plugins/nexus/tools.py:_invoke_downstream` → `from shared.gateway import call_space`,`_TARGET_PATH={"claude":"/run","codex":"/complete","langgraph":"/execute"}`(见 [[hermes-agent-换装方案]])。

现役契约 = 同步 HTTP,薄而脆:
- 线索保险:`thread_id` + `request_id` 透传跨跳串联排障。
- 状态:各 Space 自管无跨 Space 共享态。langgraph 状态锁 Supabase Postgres;hermes 状态锁 state.db + R2;claude/codex 薄返无状态。
- 错误:fail-soft(Supabase 503 不冻 event loop,见 [[hermes-agent-coreswap-done]])。

**阶段四应升 = 异步 + 统一任务 schema + 共享态表。**

同步 HTTP 在 HF CPU-Basic 单进程下 wall-clock 紧(hermes agent loop `max_iterations` 已降 15-20 防 7860 超时,见 [[hermes-agent-coreswap-done]])。编码任务(子进程 CLI + worktree + 编译测试)分钟级,同步必超时。

统一任务 schema(见 §10.4):

```
/execute_task (三下游统一契约)
  入: {task_id, repository, base_revision, objective,
       allowed_paths, allowed_commands, network_policy,
       timeout_seconds, max_cost, expected_output_schema}
  返: {task_id, status, base_revision, result_revision,
       patch_artifact (R2 key), changed_files, tests}
```

异步流:
```
hermes 调 /execute_task(批交) → 下游 enqueue 起子进程 → hermes 轮询 GET /task/{tid} 或查 Supabase tasks 表
跨 Space 状态经 Supabase 共享表: tasks {task_id, space, phase, result_r2_key}
```

**编排权归属 = dual orchestrator 分工(非重复)。**

hermes + langgraph 两指挥职责正交:
- **hermes = agent orchestrator(对话面)**:接用户 prompt → agent loop 决策 → 调下游 tool → 收结果回写记忆。主入口 + 任务级编排。
- **langgraph = workflow orchestrator(工作流面)**:用户 Match1 意 —— LangGraph **按需编排**多步工作流。hermes 当 prompt 含规划/多步语义时,自己调 `nexus_route_langgraph` tool 交 langgraph 做 graph 级编排(状态机、分支、human-in-loop、checkpoint 续)。langgraph 节点反过来又可调 claude/codex 执行细节步(经同一 `call_space`)。

dual orchestrator 链:
```
user → hermes (agent loop)
         ├─[简单推理]→ 自己返
         ├─[编码]→ nexus_call_claude / nexus_call_codex → {claude,codex} /execute_task
         └─[规划/多步]→ nexus_route_langgraph → langgraph /execute
                          └─[节点内编码]→ 调 claude/codex（经同一 call_space）
```

claude/codex 被两层都可调,**无回调 hermes,纯执行返 patch**。

**状态真相源分层(无冗余双写,与 [[hermes-agent-换装方案]] 决策4 一致):**
- conversation/messages → hermes state.db(主)+ R2 WAL(litestream 副本)
- workflow graph state → langgraph AsyncPostgresSaver(主)
- task phase/索引 → Supabase agent_states/task_queue(跨 Space 查询面)
- patch artifact → R2 nexus-artifacts
- 项目源 → GitHub 私库(worktree ephemeral,真相源远端)

各件按属性配介质,无两介质做同件事。**Supabase 是跨 Space 唯一交汇点**(共享查询面),非真源转移(真源仍在各组件主库:hermes state.db / langgraph Postgres / R2 blob)。

### 10.4 阶段四计划接入点(非本次,接入时回查)

§10 满血 + 异步通讯结论接入阶段四候选(与 [[hermes-agent-换装方案]] §阶段 J 同一来源,此处补满血 + 通讯维度增量):

- 三下游 Dockerfile 升墓碑 `ARG BASE_IMAGE` 形态(对齐 hermes 永续墓碑 + GHCR base + /data 挂载),逻辑层出镜像。
- claude-code/codex `app/main.py` 加 `/execute_task` 端点接统一任务 schema → `subprocess` 起 `claude -p`/`codex exec` CLI 子进程(`--bare --allowedTools --output-format json --json-schema` / `--json --output-schema -C`)+ 独立 git worktree + wall-clock timeout kill + 解析结构化返 `{task_id,status,base_revision,result_revision,patch_artifact,changed_files,tests}`。
- worktree 隔离:每任务 `git worktree add` 独立目录,完销毁;不用共享可写目录(防并行覆盖)。
- `allowed_paths`/`allowed_commands`/`network_policy` 传 CLI flag + 容器层 seccomp/egress 限(若 HF 允许);超时 `subprocess` timeout + SIGTERM。
- patch_artifact 上 R2(`nexus-artifacts` 桶)+ 记 Supabase `artifacts` 表。
- hermes 侧三 tool 升级调下游 `/execute_task`(新契约),`force_space` 兜底继续指老契约。
- 异步:三下游 `/execute_task` 即交即返 `accepted + task_id`,起子进程跑;hermes 轮询 `/task/{tid}` 或查 Supabase `tasks` 表读 phase;`request_id` 跨跳串联排障。
- worktree 真相源 = GitHub 私库 shallow clone ephemeral `/tmp`,完销;非 Bucket 非 Dataset(Bucket 对 ephemeral worktree 无增量价值,见 §10.2)。

Bucket-HA 单点(§8)对异步通讯无新解 —— 下游 Space 起不起仍依赖 HF 平台 + Bucket mount(hermes 侧),与本节通讯架构正交。

### 10.5 一句话终审

> claude/codex 要满血(升 CLI 子进程执行器,对等 dual);**langgraph 已满血**(library 形态真身 + Postgres checkpointer 即编排面满血,不需 CLI 化);四组件存储按件属性分:Bucket 仅 hermes,R2+Supabase+GitHub 全共,Dataset 全退役;通讯现役同步 HTTP 薄,阶段四升异步 + 统一 `/execute_task` 任务 schema + Supabase 共享态表;编排双轴分工(hermes 对话主入口 / langgraph 按需工作流),claude/codex 纯执行被两轴调无回调。

---

关联:[[hermes-agent-换装方案]] [[nexus-agent-team判定]] [[nexus最新架构-查证]] [[nexus-hermes-bucket-perpetual]] [[nexus-hermes-dual-mount-dataset-bucket]] [[nexus-hermes-agent-coreswap-done]]
