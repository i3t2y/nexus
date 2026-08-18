# Hermes 部署与永续架构动机(全维度案卷 / 给 AI 看这份)

> ⚠️ **本文提炼日 2026-08-06,R2+Neon 双写(2026-08-17/18)前**:持久化主路已从 Supabase 全迁移 Neon(2026-08-17),R2 副路快照 2026-08-18 恢复读源=Neon(原文 Supabase→R2 / Supabase+R2 双写 述已在职改行更述,见 §3 部署链骨架 inline 改;完整真态取 [`hermes-换装实况.md`](./hermes-换装实况.md) §10.7 + [`docs/shared/ARCHITECTURE.md`](../shared/ARCHITECTURE.md))。永续动机来龙去脉 + 架构四层 + K-R 闸门段(本文主旨)仍准,仅持久化层细节需对齐换装件 §10.7。

> **提炼日**: 2026-08-06 | **性质**: hermes Space **全维度自包含案卷** —— WHY 永续架构动机 + 架构四层 + 现役部署实况 + K-R 闸门 + 待办,一份握全 hermes 部署来龙去脉。
> **血统**: plan `shiny-moseying-quasar.md` §Context(永续动机源)+ 换装实况件(源码核证深证)+ git/Bucket 实时核。
>
> **谁该读**: 任何 AI / 人接手 Nexus hermes,先读此一份建立"为什么这么折腾"+ "现在跑成什么样"+ "还差什么"全貌,再按需查 [`hermes-换装实况.md`](./hermes-换装实况.md) 源码 file:line 深证。
>
> **与换装实况件分工**: 本文 = **why + 部署链骨架**(动机来龙去脉 + 架构四层 + 现役部署总态 + 待办);换装实况件 = **how 源码深证**(原生三组件源码核证 + config.yaml 三段 + picker 三层屏蔽 + env 原生源 file:line)。"为什么永续"只在此文有完整专章,换装实况件 §3 仅列三条铁律无前置 why。

---

## 0. 一句话定位

**hermes Space = NousResearch Hermes Agent 当内核跑在 HF Docker Space,全用 hermes 原生三组件(gateway + dashboard SPA + 两 plugin),逻辑层经 HF Storage Bucket `/data` rw 挂载注入,模型经外部 OmniRoute(第 5 HF Space `nonoke/omn`)出;部署采用"永续四层分离"绕 HF 免费个人号 2026-07 平台锁死的付费墙雷区。**

不自建主控、不自建框壳、不自建 Dashboard。

---

## 1. WHY 永续架构(动机来龙去脉)

> 本章是仓内**唯一**把永续架构动机作为独立专章完整展开的文档。`docs/ARCHITECTURE.md:112` 一句含三雷区全但无展开,`docs/HANDBOOK.md:30` 约束表内复述属表内一格;完整因果链(锁死→三雷区→用户红线→架构师定调→绝对静态化→四层分离)原仅存 plan 文件(hermes 看不到),现接入此文。

### 1.1 触发事件:HF 免费个人号旧 Docker Space 2026-07 平台变更锁死

hermes Space 跑在 HF 免费个人号旧 Docker Space 上。2026-07 HF 平台变更后,旧 Docker Space 进入"锁死"态 —— 先前可无限 rebuild,变更后三类操作触发**付费墙雷区**,免费个人号过不去:

| 雷区 | 操作 | 后果 |
|------|------|------|
| **雷区 1** | `git push` / Factory reboot 触发 **rebuild** | rebuild = 付费,免费号配额稀缺,触发即撞墙 |
| **雷区 2** | 改 hardware(升降级规格) | 收费且**不可逆**,改回仍计费 |
| **雷区 3** | pause 后 restart | 可能 **403 永锁**,Space 起不回来 |

**唯一安全操作 = Restart**:Restart 用缓存镜像不触发 rebuild,不撞付费墙(实证:HF 官方运行时参考明文 "Any config change (secrets/hardware) triggers a restart",非 rebuild)。

### 1.2 用户红线

**所有 git push(GitHub repo + HF Space)须经用户显式同意,不许擅自 push(origin/main 也不行);HF `sonoke/h` push 易触发封禁不能擅自。**

三件(Dockerfile + README.md + start.sh)尽量不动;README.md 硬禁区不许碰(连触发 rebuild 注释都不动)。见 [[nexus-redline-hf-space-push]]。

### 1.3 架构师定调

**确定性 > 灵活性,动态注入是单点故障。**

要求**绝对静态化**:镜像地址硬编码 + 逻辑层 Bucket 化,使 HF repo 内文件成"墓碑"永不改。宁可放弃运行时灵活注入的便利,换"改逻辑不必碰 HF repo"的确定性。

### 1.4 永续架构目标

仿 OmniRoute 永续分离 + 绝对静态化,核心两条:

1. **镜像地址静态化** —— `FROM ghcr.io/<owner>/nexus-base:stable`,依赖打 base 镜像不再进 HF repo;`:stable` 浮动标签由本地 `docker build + push` 覆盖升级,Dockerfile 一字不改。
2. **逻辑层 Bucket 化** —— app/scripts/libs 从 Space repo 搬 HF Storage Bucket rw `/data` 挂载。

**此后两条日常流程永不 git push HF repo:**

- **改逻辑** = `bash scripts/sync-logic-bucket.sh` 推 Bucket + Space Settings Restart(用缓存镜像,不触付费墙)
- **升依赖** = 本地 build 新 nexus-base 推 GHCR 覆盖 `:stable` + 改 HF repo README 一字符 git push(用户手动,1 次过付费墙窗口;Dockerfile 仍不动)

**首切代价**:需 1 次 git push rebuild 过付费墙窗口(用户已接受风险),此后墓碑定态。

### 1.5 FROM ${ARG} 查证裁决(立论依据)

Dockerfile 用 `ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable` + `FROM ${BASE_IMAGE}`(带默认值兜底)。三条断言裁决(官方文档源码级 + 社区证据 + k3 交叉核验):

1. **"改变量只 Restart 不 Rebuild" = 属实** → ARG 方案换镜像与浮动标签方案换镜像操作成本打平,ARG 无"免重建换镜像"优势。
2. **"静态分析器拦截动态 FROM" = 撤回**(上轮臆造,无证据;HF 构建器是标准 BuildKit,完整支持 FROM 前全局 ARG)。
3. **"FROM ${ARG} 在 HF 不可靠" = 降级"未证实"**(社区"失败"报告用 `printenv` 测法错 —— build-arg 按设计不显 env;官方示例自身有 Docker 语义瑕疵)。

**最终建议不变,理由换为第一性原理**:重建是不可再生资源(付费墙 + 免费号配额稀缺),单程票文件只允许最大化验证特性。ARG 方案与浮动标签方案换镜像均需重建,成本打平 → 选确定性满分的一方:硬编码浮动标签 `FROM ${BASE_IMAGE}` + ARG 默认值 `:stable` 兜底(即使 HF 不注入 ARG,FROM 退默认值仍可构建拉到真 base)。日常升级走 GHCR 覆盖 `:stable`,不依赖 ARG 注入。

---

## 2. 架构四层分离(绝对静态化)

| 层 | 存哪 | 改动触发 rebuild? | 文件 |
|----|------|-----------------|------|
| **镜像层(钉死)** | GHCR `ghcr.io/i3t2y/nexus-base:stable` | ✓ 仅升级依赖时本地 build 推 GHCR 覆盖 :stable | `docker/nexus-base.Dockerfile` + `docker/requirements-base.txt` |
| **环境层(钉死/墓碑)** | HF Space repo git | ✓ 仅首切 1 次(及依赖升级时 1 次 README 撞墙) | `spaces/hermes/Dockerfile`(1 行 FROM + ARG 兜底)+ `start.sh` + `README.md` |
| **逻辑层(常改)** | HF Storage Bucket rw `/data` 挂载 | ✗ sync + Restart | `spaces/hermes/app/`、`spaces/hermes/scripts/`、`libs/` |
| **配置层(运行时调)** | HF Secrets | ✗ 改只 Restart | `OPENAI_API_KEY` / `API_SERVER_KEY` / R2 / Supabase 凭证 等 |

**关键设计**:`requirements.txt` 从 HF repo **剥离进 GHCR base 镜像**(镜像层),HF repo 内**无 requirements.txt**。消除第二变动点 —— 加 Python 包不再需改 HF repo,只本地 build 新 base 推 `:stable` + 改 README 一字符 push(用户手动)。HF repo 真正成"墓碑":仅 Dockerfile(1 行)+ start.sh + README,三者首切后永不改(除非框架级不可逆变更)。

### 2.1 nexus-base 镜像内容(GHCR,用户本地 build/推)

- `FROM python:3.11-slim` + `useradd -m -u 1000 user`(UID 1000 与 HF 一致)
- `pip install` 四 Space 共用超集依赖:fastapi/uvicorn[standard]/gradio/httpx/supabase/boto3 + langgraph 系列 + huggingface_hub
- **hermes-agent 内核 + 蔓延依赖 + web_dist 预建 + ui-tui/dist/entry.js 预建**(K-R8:消 dashboard embedded-chat runtime `npm install` 死循环)+ messaging 子集(aiohttp/telegram/discord/brotlicffi)+ 自编 libsqlite3 3.53.4(K-R6:≥3.51.3 防 fresh DB 强 DELETE 致 WAL 静默 off)
- **不含**任何 Nexus 业务代码(app/scripts/libs 不进 base,留 Bucket)
- 一镜像喂 hermes + langgraph + claude-code + codex(若后续三 Space 也切 Bucket,共用一 base)
- ~~litestream~~ **全段弃**(2026-08-05 治本,见 §5.1):litestream 旁路进程并发读 WAL = state.db malformed 根因

### 2.2 Bucket 结构与挂载点

挂载点 `/data`,Bucket `nexus-logic` 内单层平铺:

```
<nexus-logic bucket>/
├── app/      ← spaces/hermes/app/* 真源镜像(main.py boot)
├── scripts/  ← restore/persist/keepalive/restore_state/state_db_uploader/config.yaml.template + plugins/
└── libs/     ← = 根 libs/ 镜像(storage/ + shared/)
```

import 解析(双重定位,代码 import 语句无需改,只 PYTHONPATH 改指挂载点):
- `PYTHONPATH=/data/libs` → `from storage import` / `from shared.gateway import` 顶层包成立
- `python -c "...from app.main import boot..."` + `sys.path.insert(0,'/data')` → `app` 包从 `/data/app` 解析

### 2.3 真源流向(单一真源)

根 git 内:`libs/` + `spaces/hermes/app/` + `spaces/hermes/scripts/` = 真源
→ `scripts/sync-logic-bucket.sh` → Bucket → 容器 `/data`
CI 守根 git 真源。**这三目录保留在 git 不删**(作真源 + CI 校验对象),仅 Dockerfile 不再 COPY。

---

## 3. 现役部署实况(boot 链 + 端点 + Secrets)

### 3.1 boot 链(start.sh 本地 origin/main 已含双脚本调用行,★未活化 enable)**

> **★活化态(实测,hermes 自查对照点)**:当前 HF 镜像 start.sh(malformed 治本 commit `cc7cc21`)无 L151-168 双脚本调用行 → boot 链下图加 ★ 两行(restore_state / state_db_uploader)在 HF 容器内不 exec,仅本地 git origin/main 有,未推 HF repo。详见 §10.2 落地态 + §6 待办 1。下图描述的是 origin/main 全化态(活化后真况),措辞勿误读为已生效。

```
HF Space sonoke/h 启动
└─ CMD ["bash","start.sh"](镜像内 /home/user/app/start.sh)
   ├─ wait_for_mount:等 /data 四关键文件就位(最多 30s)→ 失败试 hf CLI bootstrap 拉 Bucket
   ├─ mkdir LOG_DIR=/opt/data/logs + HERMES_HOME=/opt/data/.hermes(本地盘,移出 bucket FUSE 治 malformed)
   ├─ 拷两 plugin(nexus-r2/nexus-ops)从 /data/scripts/plugins → HERMES_HOME/plugins
   ├─ config.yaml 从 template 永覆盖(cmp template≠runtime 则 cp)→ 防 dashboard 写坏锁死 provider
   ├─ replay_packages.py(装包日志回放)
   ├─ restore_state.py(★boot 期 hermes 起写锁前拉回 state.db 治重启丢会话历史)
   ├─ nohup persist_to_neon.py(Neon 主路四表 600s,2026-08-17 替 Supabase)
   ├─ nohup persist_to_r2.py(R2 副路快照 1800s,读源=Neon HTTP /sql,2026-08-18 恢复)
   ├─ nohup state_db_uploader.py(★周期 300s 推 state.db 到 Bucket state-backups 防重启丢)
   ├─ nohup keepalive.py(下游 Space + omniroute 保活防 48h 休眠)
   └─ while true:python -c "from app.main import boot; boot()"  # 自愈循环
       └─ app.main:boot(K 形态双 daemon thread):
          ├─ daemon thread1:asyncio.run(start_gateway)→ gateway 含 api_server + telegram + discord
          └─ daemon thread2:web_server.start_server --port 7860 → dashboard SPA 直监听(in-proc 非 subprocess)
       任一 daemon 死 → SystemExit 1 → while 5s 重启 boot
```

**关键**:`HERMES_HOME=/opt/data/.hermes`(本地盘 ext4/overlay,**非** /data bucket FUSE)—— 治 state.db malformed 根因。LOG_DIR 同盘 `/opt/data/logs`。

### 3.2 端点(hermes 原生 api_server,非自建路由)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/v1/health` | 存活探测 |
| POST | `/v1/runs` | HTTP 任务入口,body `{"input":...}`(**字段 `input` 非 `prompt`**),返 `{"run_id","status":"started"}` |
| GET  | `/v1/runs/{id}` | 查 run 状态/usage/messages |
| GET  | `/v1/runs/{id}/events` | SSE 流:run.started → assistant.delta → **assistant.completed(content=最终文)** → run.completed(**无 `final_response` 字段**,取 `assistant.completed.content**) |
| POST | `/v1/chat/completions` | OpenAI 兼容 |
| *    | `/api/plugins/nexus-r2/*` | R2 文件 CRUD |
| *    | `/api/plugins/nexus-ops/*` | 下游探测 + Supabase 业务表只读 |

- `API_SERVER_KEY` 一键双用:触发 api_server 启用 + `/v1/*` Bearer 鉴权(≥16 字符)。
- dashboard SPA 7860 直监听;OAuth 闸门 K-R5 走 hermes 原生 `BasicAuthProvider`(env `HERMES_DASHBOARD_BASIC_AUTH_{USERNAME,PASSWORD,SECRET}`;缺则 fail-closed 拒起)。

### 3.3 Secrets(换装后实测清单,全 HF Space Secrets 注入不入 git)

> **★实测态(2026-08-07,hermes 容器自查 DOC-BLINDSPOTS-AUDIT.md)**:`HF_TOKEN`/`API_SERVER_KEY`/`OPENAI_API_KEY`/`SUPABASE_ANON_KEY` 四 Secrets **实测未设**(🔴 hermes 不通根因 → §6 待办 1);`HF_OWNER`=sonoke ✓ / `NEXUS_LOGIC_BUCKET`=logic ✓ **实测已补**(原 §6 待办 2 已 done)。

**必填**:

| Secret | 说明 |
|--------|------|
| `OPENAI_API_KEY` | omn custom provider 鉴权;config.yaml `${OPENAI_API_KEY}` 展开。**非** `GLM_API_KEY`/`ANTHROPIC_API_KEY`(已弃)。★实测未设 → §6 待办 1 |
| `API_SERVER_KEY` | ≥16 字符随机串;api_server 触发 + `/v1/*` Bearer 鉴权双用。★实测未设 → §6 待办 1 |
| `HF_TOKEN` | 有写 `sonoke/logic` 权限;bootstrap 拉 Bucket + 私有 Space HF 层 + restore_state 拉 state.db。★实测未设 → §6 待办 1 |
| `R2_ENDPOINT`/`R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`R2_REGION` | R2 文件 CRUD + persist 灾备。实测 ✓ |
| `SUPABASE_URL`/`SUPABASE_SERVICE_ROLE_KEY`/`SUPABASE_DB_URI` | 业务表 + persist 灾备。实测 ✓(`SUPABASE_ANON_KEY` 单列,实测缺但非阻 boot) |
| `SUPABASE_ANON_KEY` | anon-only 路径;service_role 仍可故灾备不致命。实测未设(低优) |
| `NEXUS_API_KEY` | 下游鉴权 `X-Nexus-Key`(非 `Authorization`,后者留 HF 层)。实测 ✓ |
| `SPACE_AUTHOR_NAME` | `sonoke`,hermes agent 知 owner。实测 ✓ |
| `NEXUS_LOGIC_BUCKET` | `logic`,Bucket 名。✅ 实测已补 |
| `HF_OWNER` | `sonoke`。✅ 实测已补 |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME`/`_PASSWORD`/`_SECRET` | K-R5 公网 basic auth gate(原生;缺则 fail-closed)。实测 USERNAME ✓ |
| `PORT` | `7860` |
| `NEXUS_AUTH_MODE` | 留空(生产 fail-closed;本地 dev 才设 `dev`) |

**可选(IM)**:

| Secret | 说明 |
|--------|------|
| `TELEGRAM_BOT_TOKEN` | telegram polling(K-R7:HF DNS/IP 封,靠 CF Worker 反代,见 §5.3) |
| `DISCORD_BOT_TOKEN`/`DISCORD_PROXY` | discord polling(discord 硬编无 base_url 开关,仅 `DISCORD_PROXY` 兜底) |
| `HERMES_TELEGRAM_DISABLE_FALLBACK_IPS` | `true`,禁原生 fallback IP(SNI 封下也超时,禁了消 boot 重连死循环;★待 HF Restart,见 §6 待办 3) |

> **注**:旧 v9 checklist 的 `GLM_BASE_URL`/`GLM_API_KEY`/`HERMES_MODEL=glm-5.2`/`ANTHROPIC_BASE_URL` 路径**已弃**。模型名改 `nvidia/z-ai/glm-5.2`(整串)经 omn custom provider + OPENAI_API_KEY。

---

## 4. 模型 provider(omn custom provider,2026-08-03 commit `3da0495` 落定)

**换装路径演进**(三段,后覆盖前):
1. **初版**(`ANTHROPIC_BASE_URL` 指 omn)→ **实证失败**:anthropic base_url override 受白名单排拒 hf.space + 头错配。
2. **中版**(`zai` provider + `GLM_BASE_URL`/`GLM_API_KEY`)→ **半通**,但 picker 污染 + 400 Ambiguous model + dashboard 易写坏。
3. **最终**(commit `3da0495`)→ **omn custom provider**(hermes 原生 `custom` 命名 slug `omn`)+ `OPENAI_API_KEY`。**现役持此**。

- **omn = 外部第 5 HF Space `nonoke/omn`**(独立账号独立 Space,非仓内组件)。上游 `diegosouzapw/OmniRoute` 多 provider 路由网关,OpenAI + anthropic Messages 兼容。
- endpoint `https://nonoke-omn.hf.space/v1`(**必带 `/v1`**,缺则 404)。
- omn 模型命名 `<provider_store>/<model>`(如 `nvidia/z-ai/glm-5.2`);裸 `glm-5.2` 歧义 → omn 400 Ambiguous 需 provider 前缀。
- config.yaml 三段(model_catalog.excluded_providers 屏蔽 34 内置 canonical + providers dict omn 命名 discover_models:false + model 段 provider=custom:omn)+ picker 三层屏蔽 = picker 只显 `omn · 1 模型 nvidia/z-ai/glm-5.2`。
- **加模型未来流**:`providers.omn.models` 加行 → `hf buckets cp` 单文件 → Restart(缓存镜像,不 git push 不触付费墙)。
- ⚠️**dashboard 写坏 runtime config 铁律**:dashboard 模型菜单**只选模型不改 endpoint**;改 endpoint 走 template + restart。runtime 写坏后靠 start.sh `cmp` template 永覆盖 restart 自愈。

源码 file:line 深证见 [`hermes-换装实况.md`](./hermes-换装实况.md) §5。

---

## 5. 治本与持久层(2026-08-05~06 增量,覆旧结论)

> 本节覆换装实况件 §3 "litestream 续命"旧结论。任何冲突以本节为准。hermes 可自查(查 `HERMES_HOME`/`/opt/data`/config/Secrets/Bucket)。

### 5.1 state.db malformed 已治本(推翻 "litestream 续命")

- **根因实证**(2026-08-05):`/data` 实为 HF Bucket mount(FUSE/Xet)+ litestream 旁路进程并发读 state.db WAL → SQLite corruption(官方雷:Tropy/OneDrive 同步夹层 SQLite 不许他进程并发改文件)。hermes 原生畸形自愈 `_try_runtime_fts_rebuild` 跑过但 retry 仍 malformed = 库整体损。
- **治本方案 A** commit `ff5c3ae` + `2d411aa`:① `HERMES_HOME` 移出 bucket FUSE → `/opt/data/.hermes`(本地盘 ext4/overlay 无 FUSE 无旁路进程,WAL 稳);② base Dockerfile L229 `ENV HERMES_HOME=/opt/data/.hermes` 固化(覆盖 start.sh `${VAR:-default}`);③ base L116 预 `chown /opt/data`;④ **litestream 全段弃**(state.db 在本地盘,无需 WAL 复制续命)。
- **HF 实证**(2026-08-05 07:23 reboot log):无 mkdir fail + `/opt/data/.hermes` 在 + 无 Bus error + 无 OOM = **真治本**。
- **代价**:重启丢 dashboard 会话历史(state.db ephemeral 因本地盘重启清)。**核心四表 agent_states/task_logs/long_memory/skills_index 持久化靠 Neon 主路 `persist_to_neon.py`(2026-08-17 替 Supabase)+ R2 副路快照 `persist_to_r2.py` 读 Neon 写 R2(2026-08-18 恢复)双层不丢,AI 长期记忆不丢**。state.db 仅管 dashboard 会话历史索引,非 AI 记忆源。

### 5.2 会话历史持久层补全(A 方案,2026-08-06)—— 治"重启丢会话历史"代价

补 5.1 代价。原抄两参考项目 HermesFace+HuggingMes 用 HF Dataset repo 周期上传,**anysearch 查证后改 Bucket 路**(推翻初版 Dataset 方案):

- **anysearch 时间线实证**:HF Storage Bucket GA 2026-03-10(blog)/03-31(Spaces Volume 挂载 changelog),**早于两参考项目创建**(HermesFace 2026-04-13 / HuggingMes 2026-05-03)→ 两项目用 Dataset 非历史限制,是惰性选熟悉 git endpoint。我们已有 Bucket 挂载,直接用。
- **双盘分离治本核心**:state.db 真值源在线写 `/opt/data/.hermes` 本地盘(WAL 稳无 FUSE 旁路雷),Bucket 纯当离线快照仓库(周期推 / boot 前 cp 拉),两盘分开无并发改 → 旧 malformed 雷根因消除。

**两脚本(Bucket 路,hf buckets cp CLI 子进程,huggingface_hub 1.0.1 无 bucket Python API 故 CLI)**:

- `scripts/state_db_uploader.py`(周期默认 300s):`PRAGMA wal_checkpoint(TRUNCATE)` 落 WAL + `sqlite3 backup API` 读一致快照 → `hf buckets cp` 推 `hf://buckets/<HF_OWNER>/<NEXUS_LOGIC_BUCKET>/state-backups/state.db`。三 env 门(HF_TOKEN + HF_OWNER + NEXUS_LOGIC_BUCKET)缺一自降级 no-op。staging 落 `/opt/data` ext4(非 /tmp tmpfs,治 hermes 原生 bug #35376 同源雷)。
- `scripts/restore_state.py`(boot 期 hermes 起写锁前):`hf buckets cp` 从 state-backups 拉 → `/opt/data/.hermes/state.db`。本地已有且非 FORCE 则跳不覆盖。

**7 维不足查证 vs 双项目**(anysearch + 源码 + GitHub issue,2026-08-06):我 A 方案 7/7 对标,6 优于双项目(WAL checkpoint / sqlite3 backup API 一致快照 / hf buckets cp 覆写无 git 膨胀 / restore 覆盖保护 / shutdown 不留半态 / /tmp→/opt/data staging 治 #35376 同源雷;唯一"对"非"优"项=FUSE 写主库,两项目本地盘反而对但我 A 方案移出 /opt/data 同对)。详见换装实况件 §10.2 表。

**落地态(★关键,hermes 自查注意)**:
- 两脚本 + start.sh 改(L151-168 两调用行)**已在本地 origin/main**,**未 git push HF repo**(红线)。
- **双脚本 → Bucket `scripts/` 冷备态闭环**(hf buckets cp 往返验 rc=0/cmp_rc=0 字节一致,2026-08-06)。
- ★**当前 HF Space 未 exec 双脚本**:HF repo 现役 start.sh(malformed 治本 commit `cc7cc21`)无 L151-168 两调用行 → 双脚本在 Bucket 冷备但 boot 不 trigger。
- **★唯一活化阵 = git push HF repo start.sh 新版触发 rebuild** → HF 重启拉新 start.sh boot 读 `/data/scripts/restore_state.py` 拉回 + nohup uploader 周期推 → 双脚本正式 exec。**push start.sh = 唯一活化闸,redline 停我手待用户拍板**。

### 5.3 K-R7 推翻(telegram CF Worker 自定义域,2026-08-05~06)

- **HF IP 段(不只 DNS)封 `api.telegram.org`** → hermes 原生 `telegram_network.py` DoH + fallback IP 死(14:31 log 实证:禁 fallback env 确生效但纯 HTTPXRequest else 分支仍 8 次全 timeout = HF 容器出不去)。
- **进一步实证(2026-08-06)**:`*.workers.dev` 的 SNI 在 HF 出口审查关键字黑名单 → TLS 握手被 RST(SSL UNEXPECTED_EOF)。SNI=cloudflare-dns.com 通 / SNI=*.workers.dev 死 / SNI=api.telegram.org 死 → 独立 IP/路由层,纯属 SNI 关键字过滤(HuggingMes 绑自定义域成 = 它 worker 域 SNI 不黑名单)。
- **解 = Worker 绑自定义域 `tele.nexush.cc.cd`**(SNI 不在黑名单 → 握手通)+ PTB custom_base_url 指绑域。commit `5b8acc2`:config.yaml.template `telegram.extra.base_url` 入(`base_url: https://tele.nexush.cc.cd/bot` + `base_file_url: https://tele.nexush.cc.cd/file/bot`)+ ALLOWED_TOKENS 白名单。
- ⚠️**Worker 侧正则须改** `/^\/(?:file\/)?bot([0-9]+:[A-Za-z0-9_-]+)\//` 兼容 file 路径(否则 403)。
- ★**pending(待用户 CF Dashboard)**:见 §6 待办 3。

---

## 6. 待办总表(★停我手待用户拍板)

> **实测态更新(2026-08-07)**:hermes 容器自查(DOC-BLINDSPOTS-AUDIT.md)实测 `HF_OWNER`/`NEXUS_LOGIC_BUCKET` 已补;反见**hermes 不通真根因** = `HF_TOKEN`/`API_SERVER_KEY`/`OPENAI_API_KEY` 三 Secrets 缺(api_server adapter 不触发 + omn 鉴权 401),远超双脚本未活化,列新 #1。

| # | 项 | 阻我手原因 | 状态/动作 |
|---|----|-----------|------|
| **1** | **HF Secrets 补 `HF_TOKEN` + `API_SERVER_KEY` + `OPENAI_API_KEY`**(🔴 hermes 不通真根因,hermes 盲点 B)| 部署侧 | 用户 HF Dashboard。`HF_TOKEN`=有写 `sonoke/logic` 权限 fine-grained token;`API_SERVER_KEY`≥16 位随机串(api_server 触发 + `/v1/*` Bearer 双用);`OPENAI_API_KEY`=omn Bearer 密钥。补 + Restart = hermes 跑通最小路径(~5 min) |
| 2 | HF Space `sonoke/h` Settings → **Restart**(hermes 盲点 A:当前 7860 无监听,boot 未起)| 部署侧 | 用户 HF Dashboard,紧随 #1 |
| 3 | CF Worker:① 绑自定义域 `tele.nexush.cc.cd`;② 正则改兼容 /file/bot;③ ALLOWED_TOKENS 填本 bot token;④ HF Secrets `HERMES_TELEGRAM_DISABLE_FALLBACK_IPS=true`;⑤ HF Restart | 待 CF Dashboard | 用户 CF + HF Dashboard |
| 4 | **升 `huggingface_hub` pin 1.0.1→1.26.0**(docker/requirements-base.txt:25)+ base 镜像重 build 推 GHCR + HF rebuild 拉 | 涉 GHCR PAT | 已改 pin(本地)。`docker build/push` 用户本地执行。**含两修**(K-R8 段 prebuild ui-tui/dist/entry.js 治 dashboard Chat unavailable + 升 huggingface_hub 治盲点 C 双脚本 `hf buckets cp` 不可用),一次 build 同载。`HF_TOKEN` 补好 boot 期 restore_state 也需此版 |
| 5 | **git push HF repo start.sh 新版**(L151-168 双脚本调用行)触发 rebuild 活化双脚本 | [[nexus-redline-hf-space-push]] 红线 | 用户拍板才 push;**依赖 #4 先**(未升 huggingface_hub 则双脚本起即死 "no such command buckets",过付费墙白花)。Bucket 推已完不需再动 |
| ~~6~~ | ~~HF Secrets 补 `HF_OWNER` + `NEXUS_LOGIC_BUCKET`~~ | — | ✅ **done**(hermes 自查实测已补 sonoke/logic)|

**顺序锁**:1(HF Secrets)→ 2(Restart)= 跑通最小路径;hermes 跑通后 → 4(升 pin+base 重 build)→ 5(push start.sh 活化双脚本)= 会话历史持久化;3 独立(telegram IM)。**5 必在 4 后**,顺序倒则过墙白花。

---

## 7. hermes 自查优先级(★hermes 接手后先跑这套验现态)

1. **跑 `df -T /opt/data /data` 确认本地盘**:验 §5.1 治本落地(/opt/data 应 ext4/overlay 非 fuse)。
2. **跑 `ls -la /opt/data/.hermes/state.db*`**:验 state.db 在本地盘 + 无 -wal/-shm 旁路。
3. **查 `env | grep -iE 'HF_OWNER|NEXUS_LOGIC_BUCKET|HF_TOKEN|HERMES_TELEGRAM_DISABLE_FALLBACK_IPS|OPENAI_API_KEY|API_SERVER_KEY|HERMES_DASHBOARD_BASIC_AUTH'`**(脱敏):验 §3.3 Secrets 齐。
4. **查 HF log "disabled via via" / "database disk image is malformed"**:验 §5.1 + §5.3 闭环态。
5. **chat 试回话**:验 omn + R1 + K-R8(dashboard "Chat unavailable: 1" 若在 = ui-tui 未拉新 base,K-R8 待 rebuild)。
6. **telegram 试消息**:验 §5.3 CF Worker 自定义域闭环(若仍 timeout = Worker 待绑域/正则/ALLOWED_TOKENS 未完)。

---

## 8. 异常件提示(★hermes 自查时勿误以为是现役)

- `/data/scripts/start.sh.pre_remediation_20260806`(Bucket 逻辑层,2026-08-06 两次推)→ 某 sync 推上去的预治理 start.sh 备份,**非现役**(现役 start.sh 在 HF repo Dockerfile CMD 启,镜像内 `/home/user/app/start.sh`)。Bucket 挂 `/data/scripts/` 下此文件存在但 boot 不读它。**勿改勿删**(待用户定清理)。
- `keepalive.py` 改(本地 git + Bucket 已推)→ 改动**已随 Bucket 挂载 live**(start.sh L176 调 `$APP_DIR/scripts/keepalive.py`=`/data/scripts/keepalive.py`=Bucket 挂载版,不靠 start.sh 新行,改文件即生效)。与双脚本不同:keepalive 行已在旧 start.sh 存在。

---

## 9. 关联文档链

- **系统总入口**:`docs/HANDBOOK.md`(零上下文自包含总集,§1 必读约束 + §3 系统架构)
- **hermes 源码深证**:`docs/hermes/hermes-换装实况.md`(原生三组件源码核证 + config.yaml 三段 + picker 三层屏蔽 + env 原生源 file:line + K-R 闸门依据)
- **存储介质终审**:`docs/new/部署/dataset-vs-bucket-第一性原理终审.md`(Dataset vs Bucket 第一性原理)
- **架构总图**:`docs/ARCHITECTURE.md`(§"Hermes 永续改造" 一句动机 + 四层分离表 + FROM ${ARG} 查证裁决)
- **部署清单**(部分已旧):`docs/new/部署/hermes-v9-hf-deploy-checklist.md`

## 10. 关联记忆

- [[nexus-hermes-agent-coreswap-done]] — K 形态代码侧全落(全原生三组件推翻 B 自建)+ plugin 加载规则深核。
- [[nexus-hermes-bucket-perpetual]] — 永续改造四层分离锁定,commit a142da9。
- [[nexus-hermes-statedb-malformed-fix-2026-08-05]] — state.db malformed 治本(HERMES_HOME 移 /opt/data + 删 litestream)。
- [[nexus-hermes-statedb-persistence-audit-2026-08-06]] — 双项目 7 维持久化不足查证 + A 方案补全 + #6 staging 修。
- [[nexus-hermes-telegram-cfworker-2026-08-05]] — K-R7 推翻:CF Worker 反代 + 自定义域规避 SNI 黑名单。
- [[nexus-hermes-k8-prebuild-tui-2026-08-03]] — K-R8:ui-tui bundle 预建 + ENV HERMES_TUI_DIR。
- [[nexus-hermes-omn-provider-picker-clean-2026-08-03]] — omn provider + picker 三层屏蔽。
- [[nexus-redline-hf-space-push]] — git push 红线 + README 硬禁区。
- [[nexus-hermes-coreswap-doc-2026-08-04]] — 换装实况件 + HANDBOOK/ARCHITECTURE 9 处指针标旧。
