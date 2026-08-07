# Hermes 换装实况(权威件 / 给 AI 看这份)

> **提炼日**: 2026-08-04 | **性质**: hermes Space 内核换装后**实装实况**权威记录,覆盖此前面所有"自建主控 / 弃用 Hermes Agent / Gradio+FastAPI 框壳"旧结论。
> **血统**: 源码级核证 `refs/hermes-agent` + 仓 `spaces/hermes/*` + memory 笔记 omn-provider 三源合一。
>
> **谁该读**: 任何 AI / 人接手 Nexus hermes,读完此一份即握 hermes 现役真态,不须倒读中间态文档。
>
> **关联文档链**: 系统总入口 `docs/HANDBOOK.md`(零上下文自包含总集)→ 本文(hermes 源码深度)→ `docs/new/部署/hermes-v9-hf-deploy-checklist.md`(部署清单,部分清单已旧见 §6 标注)。
> **永续架构动机 + 部署链骨架**:`docs/hermes/hermes-部署与永续架构动机.md`(WHY 永续来龙去脉 + 架构四层 + 现役部署实况 + K-R 闸门 + 待办总表;本文互补 = 本文放源码 file:line 深证,案卷放 why + 部署链骨架)。
>
> **旧结论已推翻**(勿再信):
> - `docs/HANDBOOK.md` §1 "Hermes Agent 认知修正"(自建 Gradio+FastAPI)→ **推翻**,见本文 §1。
> - `docs/ARCHITECTURE.md` L149-153 "Hermes Agent ≠ HTTP 服务(查证修正)" → **推翻**。
> - `docs/new/部署/hermes-agent-换装方案.md` 决策 3 "留 Gradio+FastAPI 框壳 + agent_server.py 包入" → **半推翻**(agent_server.py 已删,改全原生三组件,见 §2)。
> - `docs/new/部署/hermes-v9-hf-deploy-checklist.md` §Secrets `GLM_BASE_URL`/`GLM_API_KEY`/zai provider 路径 → **已旧**(改 omn custom provider + OPENAI_API_KEY,见 §5)。

---

## 0. 一句话定义(换装后)

**hermes Space = 把 NousResearch Hermes Agent 当内核跑在 HF Docker Space,全用 hermes 原生三组件(gateway + dashboard SPA + plugin),逻辑层经 HF Storage Bucket `/data` rw 挂载注入,模型经外部 OmniRoute(第 5 HF Space `nonoke/omn`)出。** 不自建主控、不自建框壳、不自建 Dashboard。

```
用户 ──7860──> hermes dashboard SPA(原生 React19,直监听 7860)
             │  同 async loop 起 gateway:
             │   ├─ api_server adapter:HTTP /v1/runs + /v1/chat/completions
             │   ├─ telegram polling (DoH 解 HF DNS 封)
             │   └─ discord polling
             │
             └─ agent loop(omn 推理)─ 判语义 ──> nexus plugin 三 tool
                                                  ├─ nexus_route_langgraph
                                                  ├─ nexus_call_claude
                                                  └─ nexus_call_codex
                                                  └─ bridge ─> libs/shared/gateway.call_space
                                                  └─ Worker / 下游 Space / R2 / Supabase
```

---

## 1. 定局(用户 2026-07-31 拍板,覆盖一切旧结论)

**hermes 终局 = NousResearch Hermes Agent**(github.com/NousResearch/hermes-agent),非自建主控、非自建框壳。

- 模板中"Hermes Agent"字样经辨伪实为**自建主控代号**(HERMES_HOME 撞名非同物),非指原生 hermes-agent。
- 减法原则:参考 democra-ai/HermesFace + somratpro/HuggingMes 俩跑全本 Hermes Agent 的项目(自带"可能封号"警告),nexus 只装核心子集,砍 extras:browser / image_gen / voice / tts / messaging / matrix / cron / mcp_serve / [all];不起 JupyterLab。
- 注:**保留** gateway(含 api_server adapter,作 HTTP 入口)+ telegram + discord polling(IM 适配,非密轮询)。

---

## 2. 全原生三组件(实证推翻先前自建框壳)

源码核证 `refs/hermes-agent` 后,hermes 原生三套能力齐备,自建全部废弃:

### 2.1 gateway(含 api_server adapter)—— **非自建 `agent_server.py`**

- hermes 原生 gateway 同 **async loop** 起 platform adapter:HTTP `/v1/runs`(api_server,env `API_SERVER_KEY` ≥16 触发)+ telegram + discord polling。
- **单库无双写**:IM 与 HTTP 同 loop,非拆两个进程/库。
- `spaces/hermes/app/agent_server.py`(早版本自建薄包装 `AIAgent + run_agent_once`)→ **已删**。

### 2.2 dashboard SPA —— **非自建 Gradio 三 Tab**

- hermes 原生 React19 Vite SPA(19 页 9119 行),`web_server.start_server --port 7860` 以 **in-proc daemon thread** 直监听。
- **in-proc daemon thread 非 subprocess**:避 cmdline 扫杀(子进程 cmdline 易被平台按模式扫杀;in-proc 同进程保活)。重挂 40+ 次实证推翻"必须 subprocess"臆断。
- 自建 Gradio 三 Tab(任务路由 / R2 文件 / 系统状态)→ **废弃**。FastAPI `gr.mount_gradio_app` 同端口共生手法 → 亦废。

### 2.3 两 plugin tab —— **manifest `tab` single dict 限制**

- `nexus-r2` plugin:R2 文件 CRUD tab + 三 tool(共 `nexus` toolset)。
- `nexus-ops` plugin:下游探测 + Supabase 业务表只读 tab,**无 tool**。
- **单 plugin 不可挂 2 tab**:hermes manifest `tab` 字段是 single dict(非 list),故两目录各 1 plugin 各 1 tab。
- 三 tool 桥 `libs/shared/gateway.call_space` 调下游 claude-code/codex/langgraph,结果回写 agent 记忆(handler 返 `tool_result()`/`tool_error()` JSON → agent tool result 自动回写)。

### 2.4 boot(main.py 极薄,非自建路由)

- **daemon thread1** `asyncio.run(start_gateway)`:起 gateway 含 api_server + IM。
- **daemon thread2** in-proc `web_server.start_server(host, port=7860, headless=True)`:起 dashboard SPA。
- **主线程** `while sleep` 监死,任一 daemon 死 → `SystemExit 1` 让 HF / supervisor 重启。

---

## 3. 永续架构(三条铁律)

1. **逻辑层进 HF Storage Bucket `/data` rw 挂载** —— 改逻辑只推 Bucket + Restart,不触 HF rebuild 付费墙。
2. **Dockerfile 永续墓碑** —— `ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable` + 仅 `COPY start.sh`(逻辑层在镜像外);首切后永不动。
3. **依赖进 GHCR base 镜像** —— hermes-agent + 蔓延依赖 + ~~litestream~~(已弃见 §10.1)+ 自编 libsqlite3 3.53.4(K-R6:≥3.51.3 防 fresh DB 强 DELETE 致 WAL 静默 off)+ web_dist 预建(K-R4)+ **ui-tui/dist/entry.js 预建**(K-R8:消 dashboard embedded-chat runtime `npm install` 死循环 → "Chat unavailable: 1";ENV `HERMES_TUI_DIR=/opt/hermes-agent/ui-tui`)+ messaging 子集(aiohttp/telegram/discord/brotlicffi)全在 base,逻辑层零 `pip install`。

- ~~state.db 经 litestream WAL→R2 复制(铁律 L8)续命;Supabase 四表经 `persist_to_r2.py` 快照(灾备,与 litestream 互补)。~~ → **已弃(2026-08-05 治本,见 §10.1)**:litestream 旁路进程并发读 WAL = state.db malformed 根因,全段删;state.db 移 `/opt/data` 本地盘 + 会话历史持久靠 §10.2 双脚本周期推 Bucket。Supabase+R2 四表灾备 `persist_to_r2.py` 保留。

---

## 4. 端点(hermes 原生 api_server,非自建路由)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/v1/health` | 存活探测(api_server adapter) |
| POST | `/v1/runs` | HTTP 任务入口,body `{"input":...}`(**字段 `input` 非 `prompt`**),返 `{"run_id","status":"started"}` |
| GET  | `/v1/runs/{id}` | 查 run 状态/usage/messages |
| GET  | `/v1/runs/{id}/events` | SSE 流:run.started → assistant.delta → **assistant.completed(content=最终文)** → run.completed(**无 `final_response` 字段**,取 `assistant.completed.content**) |
| POST | `/v1/chat/completions` | OpenAI 兼容 |
| *    | `/api/plugins/nexus-r2/*` | R2 文件 CRUD(nexus-r2 plugin_api.py) |
| *    | `/api/plugins/nexus-ops/*` | 下游探测 + Supabase 业务表只读(nexus-ops plugin_api.py) |

- `API_SERVER_KEY` 一键双用:触发 api_server 启用 + `/v1/*` Bearer 鉴权。
- dashboard SPA 7860 直监听;OAuth 闸门 K-R5(公网 0.0.0.0 须 auth provider,loopback 127.0.0.1 免)→ 走 hermes 原生 `BasicAuthProvider`(env `HERMES_DASHBOARD_BASIC_AUTH_{USERNAME,PASSWORD,SECRET}`;缺则 `list_providers()` 空 → gate `SystemExit` fail-closed 拒起)。

---

## 5. 模型 provider 配置(omn custom provider,8-3 落定)

**换装路径演进**(三段,后覆盖前):
1. **初版**:omniroute 暴露 anthropic Messages 兼容 → hermes `anthropic` provider + `ANTHROPIC_BASE_URL` 指 omn → **实证失败**:anthropic base_url override 受白名单排拒 hf.space + 头错配(x-api-key vs Bearer)。
2. **中版(v9 checklist)**:改 `zai` provider + `GLM_BASE_URL`/`GLM_API_KEY`(glm-5.2 静态路由 glm→zai)→ **半通**,但 picker 污染 + 400 Ambiguous model + dashboard 易写坏。
3. **最终(8-3 commit `3da0495`)**:改 **omn custom provider**(hermes 原生 `custom` provider 命名 slug `omn`)+ `OPENAI_API_KEY`。**现役持此**。

### 5.1 omn = 外部第 5 HF Space `nonoke/omn`(独立账号独立 Space,非仓内组件)

- 上游 `diegosouzapw/OmniRoute`:多 provider 路由网关,OpenAI 兼容 `/v1` 接口 + anthropic Messages 兼容。
- endpoint `https://nonoke-omn.hf.space/v1`(**必带 `/v1**:OpenAI SDK 拼 `/chat/completions` 落 omniroute `/v1/chat/completions`,缺 `/v1` 落 404)。
- omn 模型命名 `<provider_store>/<model>`(如 `nvidia/z-ai/glm-5.2`);裸 `glm-5.2` 歧义 → omn 400 Ambiguous 需 provider 前缀(omn aggregator 多 provider 路由)。

### 5.2 config.yaml 模板(`spaces/hermes/scripts/config.yaml.template`,Bucket 直传已落)

核心三段(commit `3da0495`):

```yaml
# ── ① model_catalog.excluded_providers:屏蔽全内置 canonical 商店(picker 只留 omn)──
# 34 canonical slugs 全列(auth.py PROVIDER_REGISTRY + models.py:1112 CANONICAL_PROVIDERS)。
# 命中 model_switch.py:2453/2272/2167 三处削。auto/* 非内置 = omn /v1/models 活探产物,
# 关 discover_models 即净(非 excluded 职责)。openrouter 同屏蔽。
model_catalog:
  excluded_providers:
    - nous
    - fireworks
    - openrouter
    - moa
    - novita
    - lmstudio
    - anthropic
    - openai-codex
    - openai-api
    - alibaba
    - xai-oauth
    - xiaomi
    - tencent-tokenhub
    - nvidia
    - copilot
    - copilot-acp
    - huggingface
    - gemini
    - vertex
    - deepseek
    - xai
    - zai
    - kimi-coding
    - kimi-coding-cn
    - stepfun
    - minimax
    - minimax-oauth
    - minimax-cn
    - ollama-cloud
    - arcee
    - gmi
    - kilocode
    - opencode-zen
    - opencode-go

# ── ② providers dict:omn 命名 provider(v12+ 正路,非 legacy custom_providers 列表)──
# custom_providers 列表经 config_migrations.py _migrate_to_12 转 providers dict 并删列表,
# 故直接写 providers dict(省 boot migration)。slug=key omn。
# discover_models: false 关活探 omn /v1/models —— 该端点返 200+ 模型含 auto/* 全灌 picker
#  (model_switch.py:2678/2845 discover 默认 True → 2696 should_probe 探 /v1/models)。关后短路。
# api_key 显式 ${OPENAI_API_KEY}(runtime_provider.py host 门控 omn host 不命中 openai/azure
#  → fallback no-key → omn 401;显式 api_key 命中 cfg_api_key 路径 → 真 key 发 omn)。
providers:
  omn:
    api: https://nonoke-omn.hf.space/v1
    name: omn
    api_key: ${OPENAI_API_KEY}
    transport: chat_completions
    discover_models: false
    models:
      nvidia/z-ai/glm-5.2:
        context_length: 128000

# ── ③ model 段:provider=custom:omn(替 bare custom)+ default=omn raw id ──
# ★ default=nvidia/z-ai/glm-5.2:omn /v1/models 返的真 raw id(非自造前缀,非裸 glm-5.2)。
# ★ provider=custom:omn:runtime 解析 custom:<slug> 路由进 providers.omn
#   (test_custom_provider_session_persistence _runtime_model_config 实证)。
#   → dashboard 显示「omn」替「Custom endpoint」(bare custom 硬编码 "Custom endpoint",
#     named slug 显示该 name,model_switch.py:2872-2875)。
model:
  default: nvidia/z-ai/glm-5.2
  provider: custom:omn
  base_url: https://nonoke-omn.hf.space/v1
  api_key: ${OPENAI_API_KEY}
```

### 5.3 picker 三层屏蔽机制(实证)

1. **omn 全表 + auto/* 污染**:`discover_models: false` 短路 `should_probe` → 只显显式 `models:` 列的。根除 auto/* + 全表。
2. **内置 canonical 商店**:`model_catalog.excluded_providers` 命中 model_switch.py 三处削。34 slugs 全列。
3. **加模型未来流**:`providers.omn.models` 加行 → `hf buckets cp` 单文件 → Restart(缓存镜像,不 git push 不触付费墙)。

### 5.4 dashboard 写坏 runtime config 教训(⚠️ 铁律)

- **元凶**:dashboard「模型菜单里重新添加刷新」操作把 runtime `/data/.hermes/config.yaml` 写坏:`model.provider: custom:omn` 被 strip 成 `omn` + `model.base_url` 清空 + `moa:` 块自动 auto-init。
- **后果**:`agent init failed: No usable credentials found for provider 'zai'` — runtime model 名含 glm 前缀 + provider 被裁 → fallback 命中内置 zai alias(auth.py:1908 glm→zai)→ 缺 GLM_API_KEY → fail。
- **铁律**:dashboard 模型菜单**只选模型不改 endpoint**;改 endpoint 走 template + restart。runtime 写坏后靠 start.sh `cmp` template 永覆盖(template≠runtime → cp override)restart 自愈。

---

## 6. Secrets(换装后实测清单)

全 HF Space Secrets 注入,不入 git(铁律 L4)。

### 必填

| Secret | 值 | 说明 |
|--------|-----|------|
| `OPENAI_API_KEY` | `<omn 真 Bearer key>` | omn custom provider 鉴权;config.yaml `${OPENAI_API_KEY}` 展开。**非** `GLM_API_KEY`/`ANTHROPIC_API_KEY`(旧路径已弃) |
| `API_SERVER_KEY` | `<≥16 字符随机串>` | api_server adapter 真触发器 + `/v1/*` Bearer 鉴权(双用) |
| `R2_ENDPOINT` / `R2_ACCESS_KEY_ID` / `R2_SECRET_ACCESS_KEY` / `R2_REGION` | R2 token | state.db litestream 接力 + R2 文件 CRUD |
| `SUPABASE_URL` / `SUPABASE_SERVICE_ROLE_KEY` / `SUPABASE_ANON_KEY` | Supabase | 业务表 + persist 灾备 |
| `SUPABASE_DB_URI` | `postgresql://...?sslmode=require` | langgraph AsyncPostgresSaver(port 6543) |
| `NEXUS_API_KEY` | 同系统一把 | 下游鉴权 `X-Nexus-Key`(非 `Authorization`,后者留 HF 层) |
| `HF_TOKEN` | 有写 `sonoke/logic` 权限 | bootstrap 拉 Bucket + 私有 Space HF 层 |
| `SPACE_AUTHOR_NAME` | `sonoke` | hermes agent 知 owner |
| `NEXUS_LOGIC_BUCKET` | `logic` | Bucket 名 |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` / `_PASSWORD` / `_SECRET` | 三件 | K-R5 公网 basic auth gate(`BasicAuthProvider` 原生;缺则 fail-closed);env 优先 |
| `PORT` | `7860` | dashboard 直监听 |
| `NEXUS_AUTH_MODE` | 留空 | 生产 fail-closed(缺 NEXUS_API_KEY 拒);本地 dev 才设 `dev` |

### 可选(IM)

| Secret | 说明 |
|--------|------|
| `TELEGRAM_BOT_TOKEN` | telegram polling(K-R7:HF DNS 封 IM 域,靠 hermes 原生 `telegram_network.py` DoH + fallback IP 自解,零改源) |
| `DISCORD_BOT_TOKEN` / `DISCORD_PROXY` | discord polling(discord 硬编无 base_url 开关,仅 `DISCORD_PROXY` 兜底) |
| `TELEGRAM_PROXY` | telegram 兜底代理 |

> **注**:旧 v9 checklist 的 `GLM_BASE_URL`/`GLM_API_KEY`/`HERMES_MODEL=glm-5.2`/`ANTHROPIC_BASE_URL` 路径**已弃**。模型名改 `nvidia/z-ai/glm-5.2`(整串)经 omn custom provider + OPENAI_API_KEY。

---

## 7. K-R 闸门状态

| 闸门 | 状态 | 依据 |
|------|------|------|
| **K-R1** omn 协议 + 401 | ✅ 落闸 | omn(nonoke-omn)`/v1/chat/completions` + `/v1/messages` + `/v1/embeddings` 全 401 非 404;hermes custom provider 源码 `runtime_provider.py:462` 默认 `api_mode=chat_completions` → OpenAI SDK;base_url 带 `/v1` 关键(缺 404) |
| **K-R5** dashboard auth gate | ✅ 路径定 | hermes 原生 `BasicAuthProvider`(env USERNAME/PASSWORD/SECRET);缺则 fail-closed。非 OAuth |
| **K-R6** litestream WAL | ✅ | base 自编 libsqlite3 3.53.4(≥3.51.3)防 fresh DB 强 DELETE 致 litestream 静默 off;`database.journal_mode: wal` |
| **K-R7** HF DNS 封 IM 域 | ✅ 代码侧(待 CF 部署) | ~~`telegram_network.py:22` DoH + fallback IP 零改源主路~~→ **§10.3 推翻**:HF IP 段也封,DoH+fallback IP 死;改 CF Worker 反代 `tele.nexush.cc.cd` 自定义域(SNI 黑名单规避)+ ALLOWED_TOKENS;pending CF Dashboard 部署。discord 仅 `DISCORD_PROXY` 兜底 |
| **K-R8** ui-tui bundle | ✅ 代码侧 | base 镜像 prebuild `ui-tui/dist/entry.js`(3.66MB)+ ENV `HERMES_TUI_DIR`(commit `5e15160`);本地验 build exit0 + 2.97GB <10GB。**待用户重 build :stable 推 GHCR + HF rebuild 拉** |
| **omn provider + picker 屏蔽** | ✅ 代码侧 | commit `3da0495`:provider custom→omn + picker 三层屏蔽 + Bucket 单文件直传(hf buckets cp)回拉校验一致 |

---

## 8. 落地状态与 pending

### 已落(代码侧,本地 + Bucket)

- 全原生三组件代码落 commit(commit `8432594` K 形态 + `5e15160` K-R8 + `3da0495` omn provider)+ Bucket 单文件直传(config.yaml.template + runtime config 修复)。
- 本地验:base build exit0 + 镜像内 shim 全假设实证 + py_compile OK + Bucket 回拉校验一致。
- `spaces/hermes/app/agent_server.py` **已删**(自建框架废弃)。

### 待用户手行(部署侧,非代码侧)

1. **HF Space `sonoke/h` Restart**(缓存镜像,不 git push 不触付费墙):start.sh `cmp` 永覆盖逻辑生 runtime config 拉新模板 → picker 只显 `omn · 1 模型 nvidia/z-ai/glm-5.2` + agent init 不再 fail zai。
2. **HF Secrets `OPENAI_API_KEY` = omn 真密钥**(401 修复前提;hf sdk 不暴露 secrets key 清单,需 dashboard 看或实跑对话判:401=未设/错值,成功回话=OK)。
3. **K-R8 base 镜像重 build**:`docker build -t ghcr.io/i3t2y/nexus-base:stable -f docker/nexus-base.Dockerfile docker/` + push GHCR(含 prebuild ui-tui/dist/entry.js)→ HF rebuild 拉 :stable(消 dashboard "Chat unavailable: 1")。

---

## 9. 关联记忆

- [[nexus-hermes-agent-coreswap-done]] — K 形态代码侧全落(全原生三组件推翻 B 自建)+ plugin 加载规则深核。
- [[nexus-hermes-native-triple-components]] — 源码实证:hermes 原生 api_server adapter / dashboard SPA / plugin 双注,自建全废弃。
- [[nexus-hermes-env-native-audit-2026-08-02]] — env 原生源核证 12+ file:line,纠 8 错向。
- [[nexus-4comp-strict-audit-2026-08-02]] — 4 组件源码级严核 + cross-cuts + hermes-agent v0.19 deep。
- [[nexus-hermes-omn-provider-picker-clean-2026-08-03]] — omn provider + picker 三层屏蔽 + Bucket 直传 + dashboard 写坏教训。
- [[nexus-hermes-model-config-official-docs-2026-08-03]] — 官方 docs 实证:model.default / custom_providers 命名 / glm-5.2 死锁根因。
- [[nexus-hermes-r1-omniroute-protocol-audit-2026-08-03]] — R1 omn 协议 + base_url /v1 + 401 修。
- [[nexus-redline-hf-space-push]] — git push 红线 + Bucket 推非 Space push。

---

## 10. 现态增量(2026-08-06,本节覆 §3/§6/§7 中旧结论)

> 本节是 2026-08-06 后增补,任何冲突以本节为准。提取自 memory 8-5/8-6 笔记 + git/Bucket 实时核。
> **hermes 自查优先用本节**:以下痛点 hermes 可自查(查 `HERMES_HOME`/`/opt/data`/config/Secrets/Bucket)。

### 10.1 state.db malformed 已治本(推翻 §3 "litestream 续命")

- **根因实证**(2026-08-05):`/data` 实为 HF Bucket mount(FUSE/Xet) + litestream 旁路进程并发读 state.db WAL → SQLite corruption(官方雷:Tropy/OneDrive 同步夹层 SQLite 不许他进程并发改文件)。hermes 原生畸形自愈 `_try_runtime_fts_rebuild` 跑过但 retry 仍 malformed = 库整体损。
- **治本方案 A** commit `ff5c3ae` + `2d411aa`:① `HERMES_HOME` 移出 bucket FUSE → `/opt/data/.hermes`(本地盘 ext4/overlay 无 FUSE 无旁路进程,WAL 稳);② base Dockerfile L229 `ENV HERMES_HOME=/opt/data/.hermes` 固化(覆盖 start.sh `${VAR:-default}`);③ base L116 预 `chown /opt/data`;④ **litestream 全段弃**(state.db 在本地盘,无需 WAL 复制续命)。
- **HF 实证**(2026-08-05 07:23 reboot log):无 mkdir fail + `/opt/data/.hermes` 在 + 无 Bus error + 无 OOM = **真治本**。
- **代价**:重启丢 dashboard 会话历史(transient state.db ephemeral 因本地盘重启清)。**核心四表 agent_states/task_logs/long_memory/skills_index 在 Supabase+R2 双写(`persist_to_r2.py`)不丢,AI 长期记忆不丢**。state.db 仅管 dashboard 会话历史索引,非 AI 记忆源。
- ⚠️**推翻 §3 铁律 L8**:"state.db 经 litestream WAL→R2 复制续命" → **已弃**(治本后 litestream 删)。

### 10.2 会话历史持久层补全(A 方案,2026-08-06)—— 治"重启丢会话历史"代价

补 10.1 代价(state.db ephemeral 重启丢)。原抄 两参考项目 HermesFace+HuggingMes 用 HF Dataset repo 周期上传,**anysearch 查证后改 Bucket 路**(推翻初版 Dataset 方案):

- **anysearch 时间线实证**:HF Storage Bucket GA 2026-03-10(blog)/03-31(Spaces Volume 挂载 changelog),**早于两参考项目创建**(HermesFace 2026-04-13 / HuggingMes 2026-05-03)→ 两项目用 Dataset 非历史限制,是惰性选熟悉 git endpoint。我们已有 Bucket 挂载,直接用。
- **双盘分离治本核心**:state.db 真值源在线写 `/opt/data/.hermes` 本地盘(WAL 稳无 FUSE 旁路雷),Bucket 纯当离线快照仓库(周期推 / boot 前 cp 拉),两盘分开无并发改 → 旧 malformed 雷根因消除。

**两脚本(Bucket 路,hf buckets cp CLI 子进程,huggingface_hub 1.0.1 无 bucket Python API 故 CLI)**:

- `scripts/state_db_uploader.py`(周期默认 300s):`PRAGMA wal_checkpoint(TRUNCATE)` 落 WAL + `sqlite3 backup API` 读一致快照 → tmp → `hf buckets cp` 推 `hf://buckets/<HF_OWNER>/<NEXUS_LOGIC_BUCKET>/state-backups/state.db`。**三 env 门**:HF_TOKEN + HF_OWNER + NEXUS_LOGIC_BUCKET 缺一自降级 no-op(WARN 不阻断 boot)。
- `scripts/restore_state.py`(boot 期 hermes 起写锁前):`hf buckets cp` 从 state-backups 拉 → `/opt/data/.hermes/state.db`。本地已有且非 FORCE 则跳不覆盖。

**7 维不足查证 vs 双项目**(anysearch + 源码 + GitHub issue,2026-08-06):
| # | 维度 | 双项目 | 我 A 方案 |
|---|------|--------|-----------|
| 1 | WAL checkpoint | ❌ 排 WAL 无 checkpoint 丢写 | ✅ wal_checkpoint(TRUNCATE) |
| 2 | 快照一致 | ❌ shutil.copy2 撕裂态 | ✅ sqlite3 backup API |
| 3 | history 膨胀 | ❌ upload_folder 144 commit/天 git 累积 | ✅ hf buckets cp 覆写无累积 |
| 4 | restore 覆盖保护 | ❌ snapshot_download 无脑 unlink | ✅ if exists not FORCE: skip |
| 5 | shutdown 截断 | ❌ 无 SIGTERM hook 可半推库 | ✅ cp 失败不留半态 |
| 6 | /tmp staging | ❌ tmpfs 小静默截半(同 hermes bug #35376) | ✅ `dir=_STAGING_DIR=/opt/data` 已补(空腹 NamedTemporaryFile dir=) |
| 7 | FUSE 写主库 | ✅ 两项目本地盘对(反而非不足) | ✅ /opt/data 移出 |

**落地态**(★关键,hermes 自查注意):
- 两脚本 + start.sh 改 **本地 origin/main 未 git push HF repo**(红线,见 [[nexus-redline-hf-space-push]])。
- **双脚本 → Bucket `scripts/` 冷备态闭环**(hf buckets cp 往返验 rc=0/cmp_rc=0 字节一致,2026-08-06)。
- **start.sh 留本地未推** = 用户拆分推送决策(推 start.sh 触发 HF rebuild 过付费墙窗口,合并后续 operator 指令活化排移防分散消耗)。
- ★**当前 HF Space 未 exec 双脚本**:HF repo 现役 start.sh(malformed 治本 commit `cc7cc21`)**无 L151-168 两调用行**(restore_state.py + uploader 还原卫士)→ 双脚本在 Bucket 冷备但 boot 不 trigger。
- **★唯一活化阵 = git push HF repo start.sh 新版触发 rebuild** → HF 重启拉新 start.sh boot 读 `/data/scripts/restore_state.py`(L151)拉回 state.db + nohup uploader(L163-165)周期推 Bucket → 双脚本正式 exec。**push start.sh = 唯一活化闸,redline 停我手待用户拍板**。

### 10.3 K-R7 推翻(telegram CF Worker,2026-08-05~06)—— 覆 §7 K-R7

- **HF IP 段(不只 DNS)封 `api.telegram.org`** → hermes 原生 `telegram_network.py` DoH + fallback IP 死(14:31 log 实证:禁 fallback env 确生效但纯 HTTPXRequest else 分支仍 8 次全 timeout = HF 容器出不去 worker)。
- commit `5b8cc2`:CF Worker `tele.nexush.workers.dev` 反代 + ALLOWED_TOKENS 白名单;config.yaml.template `telegram.extra.base_url` 入(PTB 默认 base_url 含 /bot 同格式)。
- **进一步实证(2026-08-06 自定义域闭环)**:`*.workers.dev` 的 SNI 在 HF 出口审查设备关键字黑名单 → TLS 握手被 RST(SSL UNEXPECTED_EOF)。hermes 实测同 CF IP + SNI=cloudflare-dns.com 通 / SNI=*.workers.dev 死 / SNI=api.telegram.org 死 → 独立 IP/路由层,纯属 SNI 关键字过滤(HuggingMes 绑自定义域成 = 它 worker 域 SNI 不黑名单)。
- **解 = Worker 绑自定义域 `tele.nexush.cc.cd`**(SNI 不在黑名单 → 握手通) + PTB custom_base_url 指绑域。config.yaml.template L180-181 已入(`base_url: https://tele.nexush.cc.cd/bot` + `base_file_url: https://tele.nexush.cc.cd/file/bot`)。
- ⚠️**Worker 侧正则须改** `/^\/(?:file\/)?bot([0-9]+:[A-Za-z0-9_-]+)\//` 兼容 file 路径(否则 403)。
- ★**pending(待用户 CF Dashboard)**:① CF Worker 绑自定义域 tele.nexush.cc.cd;② Worker 正则改兼容 /file/bot;③ ALLOWED_TOKENS 白名单填本 bot token;④ HF Secrets `HERMES_TELEGRAM_DISABLE_FALLBACK_IPS=true`(禁原生 fallback IP,SNI 封下也超时,禁了消 boot 重连死循环);⑤ HF Restart。

### 10.4 异常件提示(★hermes 自查时勿误以为是现役)

- `/data/scripts/start.sh.pre_remediation_20260806`(Bucket 逻辑层,2026-08-06 10:54/13:19 两次推)→ 某 sync 推上去的预治理 start.sh 备份,**非现役**(现役 start.sh 在 HF repo Dockerfile CMD 启)。Bucket Volume 挂 /data/scripts/ 下此文件存在但 boot 不读它(Dockerfile CMD 指 `/home/user/app/start.sh` 镜像内 ENV HOME 路径,非 /data/scripts/)。**勿改勿删**(待用户定清理)。
- `keepalive.py` 改(本地 git M 72 行)+ Bucket 已推(11:227)→ 同两脚本冷备态,改动在 Bucket 挂载 `/data/scripts/keepalive.py` 但现役 start.sh L173-177 `nohup keepalive.py` 已调它(此行未改)。keepalive 改是否激活取决于git push or 既 Bucket 又 start 不触 bump → 看 keepalive sys.path 路径逻辑是否碰 Bucket 版(boot L176 调 `$APP_DIR/scripts/keepalive.py`=`/data/scripts/keepalive.py`=Bucket 挂载版)→ **keepalive 改已随 Bucket 挂载 live**(不需推 start.sh,因 start.sh 调的是 /data/scripts/ 下此文件随 Volume 挂载已更新)。注意此与两脚本不同:keepalive 行已在旧 start.sh 存在,改文件即生效(不靠 start.sh 新行)。

### 10.5 hermes 自查建议优先级

1. **跑 `df -T /opt/data /data` 确认本地盘**:验 §10.1 治本落地(/opt/data 应 ext4/overlay 非 fuse)。
2. **跑 `ls -la /opt/data/.hermes/state.db*`**:验 state.db 在本地盘 + 无 -wal/-shm 旁路(litestream 弃应有无 wal,有则查谁起 checkpoint)。
3. **查 `env | grep -iE 'HF_OWNER|NEXUS_LOGIC_BUCKET|HF_TOKEN|HERMES_TELEGRAM_DISABLE_FALLBACK_IPS|OPENAI_API_KEY|API_SERVER_KEY|HERMES_DASHBOARD_BASIC_AUTH'`**(脱敏):验 §6 Secrets 齐。
4. **查 HF log "disabled via via" / "database disk image is malformed"**:验 §10.1 + §10.3 闭环态。
5. **chat 试回话**:验 omn + R1 + K-R8(dashboard "Chat unavailable: 1" 若在 = ui-tui 未拉新 base,K-R8 待 rebuild)。
6. **telegram 试消息**:验 §10.3 CF Worker 自定义域闭环(若仍 timeout = Worker 待绑域/正则/ALLOWED_TOKENS 未完)。

### 10.6 当前待办总表(★停我手待用户拍板的)

| # | 项 | 阻我手原因 | 动作 |
|---|----|-----------|------|
| 1 | **git push HF repo start.sh 新版**(L151-168 两调用行)触发 rebuild 活化双脚本 | [[nexus-redline-hf-space-push]] 红线 | 用户拍板才 push;Bucket 推已完不需再动 |
| 2 | HF Secrets 补 HF_OWNER + NEXUS_LOGIC_BUCKET | 部署侧 | 用户 HF Dashboard |
| 3 | CF Worker §10.3 ①~④ | 待 CF Dashboard | 用户 CF Dashboard |
| 4 | K-R8 base 镜像重 build 推 GHCR + HF rebuild 拉 | 涉 GHCR PAT | 用户本地 docker build/push |
