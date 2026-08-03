# Hermes 换装实况(权威件 / 给 AI 看这份)

> **提炼日**: 2026-08-04 | **性质**: hermes Space 内核换装后**实装实况**权威记录,覆盖此前面所有"自建主控 / 弃用 Hermes Agent / Gradio+FastAPI 框壳"旧结论。
> **血统**: 源码级核证 `refs/hermes-agent` + 仓 `spaces/hermes/*` + memory 笔记 omn-provider 三源合一。
>
> **谁该读**: 任何 AI / 人接手 Nexus hermes,读完此一份即握 hermes 现役真态,不须倒读中间态文档。
>
> **关联文档链**: 系统总入口 `docs/HANDBOOK.md`(零上下文自包含总集)→ 本文(hermes 深度)→ `docs/new/部署/hermes-v9-hf-deploy-checklist.md`(部署清单,部分清单已旧见 §6 标注)。
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
3. **依赖进 GHCR base 镜像** —— hermes-agent + 蔓延依赖 + litestream + 自编 libsqlite3 3.53.4(K-R6:≥3.51.3 防 fresh DB 强 DELETE 致 litestream 静默 off)+ web_dist 预建(K-R4)+ **ui-tui/dist/entry.js 预建**(K-R8:消 dashboard embedded-chat runtime `npm install` 死循环 → "Chat unavailable: 1";ENV `HERMES_TUI_DIR=/opt/hermes-agent/ui-tui`)+ messaging 子集(aiohttp/telegram/discord/brotlicffi)全在 base,逻辑层零 `pip install`。

- state.db 经 litestream WAL→R2 复制(铁律 L8)续命;Supabase 四表经 `persist_to_r2.py` 快照(灾备,与 litestream 互补)。

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
| **K-R7** HF DNS 封 IM 域 | ✅ | telegram 靠 hermes 原生 `telegram_network.py:22` DoH + fallback IP 零改源主路;discord 仅 `DISCORD_PROXY` 兜底 |
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
