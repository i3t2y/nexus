# Hermes Agent 换装方案落地文档

> 永续改造:spaces/hermes/ 内核从自建关键词 route 主控换装为 NousResearch Hermes Agent(github.com/NousResearch/hermes-agent),保四 Space 协同结构。本文是方案裁决 + 实装记录。

## 0. 定局依据(用户 2026-07-31 拍板)

用户 2026-07-31 明确:**hermes 就是 NousResearch Hermes Agent**。覆盖一切文档旧结论(HANDBOOK:36 / 模板:1653 此前"自建主控/弃用 Hermes Agent"皆错)。模板中"Hermes Agent"字样经辨伪实为自建主控代号(HERMES_HOME 撞名非同物)。

减法原则:参考 democra-ai/HermesFace + somratpro/HuggingMes 这俩跑全本 Hermes Agent 的项目(自带"可能封号"警告),nexus 只装核心子集,砍 browser/image_gen/voice/tts/messaging/matrix/cron/mcp_serve/[all] extras,不起 gateway/长连/密轮询/JupyterLab。

## 1. 关键核读修正(Plan agent 实证推翻先前结论)

1. **nemo-relay ≠ omniroute/Nous Portal**。hermes-agent core dep `nemo-relay` 是 NVIDIA Rust agent runtime binding,做 agent 运行时可观测性,与模型端点路由无关。先前"provider=nous→nous_api_mode 双形兼容"判断**错**。
2. **provider 是 plugin 架构**。`plugins/model-providers/<33个含 anthropic/nous/custom/openrouter>`,无中央 `anthropic_adapter.py`。接 omniroute 走 `anthropic` provider profile + `ANTHROPIC_BASE_URL` 指 omniroute 入口(暴露 anthropic Messages 兼容 API 最常见形态)。
3. **`run_conversation` 返 dict**(实证 `agent/turn_finalizer.py:574-607` finalize_turn 构造 result dict):`final_response`(str)/`messages`/`completed`(bool)/`interrupted`/`failed`/`total_tokens`/`input_tokens`/`output_tokens`/`turn_exit_reason`。**是 dict 不是 dataclass。**
4. **Hermes tool 系统是 plugin 架构**。注册发生在 `AIAgent(...)` 构造之前(插件加载阶段往 `tools.registry` 塞)。AIAgent 构造器无 `custom_tools` 参数,只接 `enabled_toolsets`/`disabled_toolsets`。`ctx.register_tool(name, toolset, schema, handler, is_async=True)` 公开支持外部注册,路 B 零源码改。
5. **user 插件目录 = `$HERMES_HOME/plugins/`**(`hermes_cli/plugins.py:1390`),import 名 `hermes_plugins.<slug>`(仅 plugin 自身目录在 search path,父 plugins/ 不在 → 相对 import)。standalone 插件须 config.yaml `plugins.enabled: [\name]` opt-in 才 `register()`。
6. **`HERMES_HOME` 是 state.db 唯一重定向开关**(无 HERMES_DB),`HERMES_HOME/config.yaml` 存配置。
7. **fastapi/uvicorn** 在 hermes core 是 `>=0.104,<1` / `>=0.24,<1` 区间(非硬钉),与 base 镜像固钉 `fastapi==0.115.6 / uvicorn==0.34.0` 兼容,R4 风险消除。

## 2. 路径裁决

### 决策 2:路 B = call_space 注册为 AIAgent custom tool,agent 智能决策调下游

**否决路 A**(保留现役关键词 route() 分发下游,agent 只做自己推理沦为摆设)——违反用户 Match1"混合架构 = Hermes 当主力执行,LangGraph 按需编排,Claude/Codex 作引擎被 Hermes 调"明确意。

**路 B**:
- agent loop 默认调 omniroute 推理,当 prompt 语义含"规划/多步/工作流"时 agent 调 `nexus_route_langgraph` tool,含"实现/重构/调试"调 `nexus_call_claude`,含"补全/片段"调 `nexus_call_codex`。
- 结果回写 agent messages(用户意:结果回写 Hermes 记忆),handler 返 `tool_result()`/`tool_error()` JSON 字符串即 agent tool result 自动回写。
- 三 tool 在 `scripts/plugins/nexus/`(Hermes 插件),内部桥到现役 `libs/shared/gateway.call_space`。
- **`force_space` 入参保留作兜底**:收到 `force_space=langgraph` 跳过 agent 推理直接对应 tool 一把调(向后兼容老 dashboard)。

### 决策 3:留 Gradio+FastAPI 框壳守 7860 + agent 当执行内核包入

现 `app/main.py` 的 `gr.mount_gradio_app(api, demo, "/")` 同端口共生手法保留(HF 单进程硬约束)。Gradio Dashboard 三 Tab 留(任务路由/文件管理 R2/系统状态)。FastAPI 端点 `/health`/`/run`/`/enqueue`/`/dequeue`/`/state`/`/task` 留。重写 `/run` + `_do_run`:无 force 调 `agent_server.run_agent_once`,有 force 走老 route()+call_space 兜底。

### 决策 5/7:持久化 + 性能

- `HERMES_HOME=/data/.hermes`,state.db litestream WAL→R2 复用 `nexus-checkpoints` 桶(对象路径 `db/hermes-state.sqlite` 不与 langgraph `thread_id.json` 撞),sync 10s(铁律 L8)。
- `persist_to_r2.py` + `restore_from_r2.py` **保留不动**:现 4 表 Supabase→R2 快照机制作灾备(litestream 只管 state.db WAL,两套互补不重叠)。
- `max_iterations` 降 15-20(HF CPU-Basic 90 轮破 7860 超时);长链路走 task_queue 异步 + 轮询 GET /task。

## 3. 实装文件清单

| 文件 | 改动 | 阶段 |
|------|------|------|
| `docker/nexus-base.Dockerfile` | apt 段 + litestream v0.5.15 + uv + clone hermes-agent v2026.7.30 + editable --no-deps + anthropic==0.87.0 | A1 |
| `docker/requirements-base.txt` | 追加 hermes-agent core 传递依赖全 pin;3 处与 gradio/langchain 相斥的松 pin(Pillow/packaging/websockets) | A2 |
| `spaces/hermes/app/main.py` | `_do_run` 重写:force 兜底路 A + agent 路 B;import 加 `from .agent_server import run_agent_once` | B1 |
| `spaces/hermes/app/agent_server.py` | 新增:薄包装 `AIAgent(provider=anthropic, enabled_toolsets=["nexus"], disabled_toolsets=[...], max_iterations=15)` + `run_agent_once()` 串行化 + 返键固化 | B2 |
| `spaces/hermes/scripts/plugins/nexus/{plugin.yaml,__init__.py,tools.py}` | 新增:Hermes plugin(toolset="nexus"),三 async handler 桥 call_space | B3 |
| `spaces/hermes/scripts/litestream.yml` | 新增:监 /data/.hermes/state.db WAL→R2 nexus-checkpoints,sync 10s | B4 |
| `spaces/hermes/start.sh` | boot 段加 HERMES_HOME mkdir + 插件 stage + config.yaml seed + litestream restore/replicate + watchdog;wait_for_mount 加 litestream.yml 判断点 | C |
| `.env.example` | 补 HERMES_MODEL/ANTHROPIC_BASE_URL/HERMES_HOME/HERMES_AGENT_DIR | E1 |

不动:`scripts/{keepalive,persist_to_r2,restore_from_r2,replay_packages}.py`、`libs/{storage,shared}/*`、`sql/*`、`workers/gateway/*`。

## 4. 依赖冲突解决(实测三轮)

hermes-agent core deps 多处硬 pin 与 base 镜像固钉/传递约束相斥:

| 冲突 | 解决 | 理由 |
|------|------|------|
| `Pillow==12.2.0` vs gradio 5.9.1 `pillow<12,>=8` | 松 pin(注释掉),随 gradio 解 pillow<12 | vision/image_gen 工具集已 disabled,Pillow 缩图路径不触发 |
| `packaging==26.0` vs langchain-core 0.3.40 `packaging<25,>=23.2` | 松 pin `packaging>=23.2,<25` | hermes --no-deps 装其 packaging 约束运行时不强制;packaging API 自 14 稳定 |
| `websockets==15.0.1` vs gradio-client 1.5.2 `websockets<15,>=10` | 松 pin `websockets>=10.0,<15` | hermes 核心不 import websockets(仅 feishu 插件用,feishu 已禁);logging.py logger 名串非 import |

base 镜像 build :test 实测通(`naming to ghcr.io/i3t2y/nexus-base:test done`,2.17GB)。V1 验证:`litestream version` 0.5.15 + `from run_agent import AIAgent` OK。

## 5. 验证闸门状态(R 项)

| 闸门 | 状态 | 依据 |
|------|------|------|
| R9 custom tool 注册 API 公开 | ✅ 通 | V4 源码实证 `PluginContext.register_tool` 公开支持外部注册,路 B 落地 |
| R3 run_conversation 返 dict 键 | ✅ 通 | V4 源码实证 `turn_finalizer.py:574-607` finalize_turn result dict |
| R6 litestream WAL 不回退 DELETE | ✅ 通 | HF ext4 非 NFS;litestream replicate 起 pid 持续(V5 日志) |
| R7 nemo-relay Rust 编译 | ✅ 通 | base build 装预编译 wheel,无源码编译 |
| R4 uv pip vs base pip 覆盖 | ✅ 通 | fastapi/uvicorn hermes 区间与 base 固钉兼容 |
| R1 omniroute 协议 + 鉴权头 + 模型名 | ✅ 大部分通 | omniroute = 第 5 HF Space `nonoke/omn`(独立账号独立 Space 跑通,非仓内组件)。endpoint `https://nonoke-omn.hf.space`;暴露 anthropic Messages 兼容 API,glm-5.2/claude-sonnet-5 模型名透传接受(glm5.2 即本会话所用模型,经此 omniroute 出)。剩:HF Space Logs 抓一条 `POST /v1/messages` 200 锁死鉴权头(x-api-key vs Bearer) |
| R5 运行时 lazy_deps 懒装 | ⏸ 部署期验 | 预装 anthropic + disabled_toolsets 列全,日志 grep `pip install` 确认无(部署 V8) |

## 6. 部署侧闸门(V9,不能本地跑,需真凭据)

1. `bash scripts/sync-logic-bucket.sh` 推 Bucket(逻辑层 app/scripts/libs/sql)
2. GHCR:本地 `docker build -t ghcr.io/i3t2y/nexus-base:stable -f docker/nexus-base.Dockerfile docker/` + push + 打 `:vN`(用户手动 1 次)
3. HF Space Settings → Variables 填:`ANTHROPIC_BASE_URL=https://nonoke-omn.hf.space`+`ANTHROPIC_API_KEY`+`HERMES_MODEL=glm-5.2` + 现役 R2/Supabase/HF_TOKEN/NEXUS_API_KEY/下游 URL 全套
4. HF Restart(用缓存 `:stable`,不 git push 不 rebuild)
5. HF Logs 抓 boot:`bucket mounted OK` + `nexus plugin staged` + `config.yaml seeded` + `litestream restore` + `launching hermes on :7860` + `agent ready`
6. 外部 curl `/health` 200 + `/run`(无 force,agent 自推理)200 + task_id
7. R1 收尾:HF Space Logs 抓一条 `POST /v1/messages` 200 锁死鉴权头(x-api-key vs Bearer);401 → 退 Bearer。omniroute 协议基线已验(`nonoke/omn` Space 跑通 + 本会话 glm5.2 经此出)
8. 过夜不崩;R2 `nexus-checkpoints` 桶 `db/hermes-state.sqlite` 存在(litestream WAL 接力)
