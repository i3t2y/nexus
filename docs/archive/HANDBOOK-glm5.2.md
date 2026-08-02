# Nexus 接手手册（Handbook）

> 目的：任何人或 AI 零上下文读此一份文档，即可理解、部署、维护、二次开发 Nexus 系统。
> 配套源码在 `nexus/` 仓库根。文档另见 `ARCHITECTURE.md` / `COMMUNICATION.md` / `DEPLOYMENT.md` / `CREDENTIALS.md`，本手册是它们的浓缩 + 自包含总集。

---

## 0. 一句话定义

**Nexus（星枢）= 混合 Agent 系统**：用 Hugging Face Spaces 当免费/低成本计算单元，Cloudflare R2 存大文件，Supabase Postgres 存结构化状态。一个常驻 `hermes` 主控按 prompt 关键词把任务路由到 `langgraph`（编排）、`claude-code`（强推理）、`codex`（快速编码）三个下游 Space，Cloudflare Worker 做统一鉴权入口 + 保活探测。

```
请求 ──> [hermes /run] ──路由──> Worker网关 ──> 下游Space ──> R2/Supabase
                                              (langgraph/claude/codex)
 Dashboard(UI)  +  FastAPI(同进程7860)            回传结果写库
```

设计四原则：① 计算与存储分离（Space 重启不丢数据）② 唯一入口路由（下游 Space 不直接对外）③ 凭证外置（全走 Secrets/环境变量，不入库不入码）④ 模板先行（当前**模板阶段**，凭证未填，代码可直接部署，填 Secrets 即跑）。

---

## 1. 必读约束（踩坑预警，部署前必过一遍）

这些是 HF/Cloudflare/Supabase 平台的客观限制，代码已据此设计，改设计前先看懂这条。

| 约束 | 事实 | 本方案如何应对 |
|------|------|--------------|
| **HF Docker 付费墙** | 自 2024 起 Docker SDK Space 跑 compute 需 PRO/Team 套餐；个人免费号仅 2 个 ZeroGPU Gradio Space + CPU Basic 免费 | 4 个 Space 全用 Docker SDK。部署前确认账号有付费套餐，否则 langgraph/claude/codex 三个 Docker Space 创建不了。hermes 可改 Gradio + ZeroGPU 跑免费层 |
| **免费 Space 会休眠** | CPU Basic / ZeroGPU 闲置即睡，首调冷启动数十秒 | 外部监测网站定期 ping `/health`（主，已验证稳定）；Worker cron + hermes `keepalive.py` 内部互探（辅）。间隔随机化防风控特征 |
| **HF 出站端口限制** | 仅 80/443/8080 放行 | R2(S3 API 443)、Supabase(HTTPS 443) 均无碍 |
| **`/data` 已下线** | Space 内持久存储已废 | 跨重启持久必须走 R2/Supabase，不依赖本地磁盘 |
| **单进程监听 7860** | HF Space 要求单进程监听 7860 | hermes 用 `gr.mount_gradio_app(fastapi_app, demo, "/")` 把 Gradio Dashboard 挂到 FastAPI 同端口；FastAPI 路由 API + Gradio UI 一个进程 |
| **HF 私有 Space 可见性** | 私有 Space embed URL 仅 owner/collaborators 可访，外部 404 | 需 `HF_TOKEN` 或经 Worker 转发；调用带 `Authorization: Bearer <HF_TOKEN>` |

> **Hermes Agent 认知修正**：NousResearch 开源 hermes-agent 是 TUI/CLI 交互式 agent（`uv` 装本地），`hermes gateway start` 是 Telegram/Discord 等消息平台网关，**不监听 HTTP 端口、无 `--port`**。故本方案**不依赖原生 hermes CLI 当 Space HTTP 主控**，自建 Gradio+FastAPI 实现。部分参考文档提到 `hermes gateway start --port 7860`、`/learn`、`hermes skills install <name>` 等命令，官方 README（2026-07 查证）**均无**，前端方案不照搬。

---

## 2. 组件与文件地图

### 2.1 顶层目录

```
nexus/
├── README.md                 # 项目简述 + 快速开始（入口）
├── .env.example              # 凭证模板（.env 已 gitignore，不入库）
├── .gitignore
├── docs/
│   ├── HANDBOOK.md           # ← 本文件，完整接手文档
│   ├── ARCHITECTURE.md       # 架构总览 + 增强机制段
│   ├── COMMUNICATION.md      # Space 间通信决策（Worker为主+直调回退）
│   ├── DEPLOYMENT.md         # 6 步部署手册
│   └── CREDENTIALS.md        # 凭证清单 + 安全红线
├── libs/                     # 跨 Space 共享库（根目录唯一真源）
│   ├── storage/              # R2+Supabase 统一封装
│   └── shared/               # gateway(HTTP调用) + checkpointer(LangGraph)
├── spaces/                   # 4 个 HF Space，各自独立 Docker build context
│   ├── hermes/               # 主控:Gradio Dashboard + FastAPI + 后台脚本
│   ├── langgraph/            # 编排(AsyncPostgresSaver)
│   ├── claude-code/          # 强推理(Anthropic API)
│   └── codex/                # 快速编码(OpenAI 兼容)
├── workers/gateway/          # Cloudflare Worker(鉴权+路由+保活)
├── sql/
│   ├── 00_schema.sql         # Supabase 基础表(幂等)
│   └── 01_pgvector.sql       # 向量扩展(可选)
└── scripts/
    └── sync-spaces.sh        # 把 libs/ 复制进各 Space 目录(build 前必跑)
```

### 2.2 共享库 `libs/`（根目录为唯一真源，build 前 `sync-spaces.sh` 复制进各 Space 的 `libs/`）

| 文件 | 导出 | 作用 |
|------|------|------|
| `libs/storage/storage.py` | `r2_client()`, `save_checkpoint()`, `load_checkpoint()`, `presigned_get()`, `supabase_client()`, `save_state()`, `load_state()`, `log_task()`, `remember()`, `recall()`, `dumps()` | R2(S3兼容 boto3) + Supabase(create_client) 惰性初始化，凭证全从环境变量读 |
| `libs/storage/__init__.py` | re-export 上列 | Space 内 `from storage import ...` |
| `libs/shared/gateway.py` | `call_space(space, path, payload)`, `ping(space)` | 调下游 Space：优先 Worker 网关，404/超时回退直调 hf.space。超时 90s(含冷启动) / 网关 60s |
| `libs/shared/checkpointer.py` | `build_checkpointer()`, `db_uri()` | LangGraph `AsyncPostgresSaver.from_conn_string()` 上下文管理器，调方负责 `.setup()` |
| `libs/shared/__init__.py` | — | 包标记 |

> **关键约束**：根 `libs/` 是唯一真源。改完根 libs 后、`git push` 前**必须**跑 `bash scripts/sync-spaces.sh`，否则各 Space build 出来跑的是旧库。各 Space 的 `libs/` 是同步产物（可提交，HF 直接读 repo 无构建后同步步骤）。

### 2.3 四个 Space

| Space | URL repo 名 | SDK | 端口 | 角色 |
|-------|-----------|-----|------|------|
| hermes | `hermes` | docker | 7860 | 主控大脑：Dashboard + FastAPI 路由 + 双写/保活/自愈后台 |
| langgraph | `langgraph` | docker | 7860 | 复杂工作流编排，AsyncPostgresSaver Checkpoint + R2 blob |
| claude-code | `claude-code` | docker | 7860 | 强推理，对接 Anthropic Messages API |
| codex | `codex` | docker | 7860 | 快速编码，对接 OpenAI 兼容 /chat/completions |

Space URL 统一格式：`https://{owner}-{repo}.hf.space`。

每个 Space 目录结构（以 hermes 为例，下游三个无 `scripts/` 与 `start.sh`）：
```
spaces/hermes/
├── README.md       # ← frontmatter 被 HF 当 Space config(title/sdk/app_port/tags...)
├── Dockerfile      # python:3.11-slim, EXPOSE 7860, PYTHONPATH=.../libs
├── requirements.txt
├── start.sh        # 仅 hermes:自愈循环 + 后台启动双写/保活
├── app/main.py     # 业务代码
├── libs/           # 由 sync-spaces.sh 复制(构建时已就位)
└── scripts/        # 仅 hermes
    ├── persist_to_r2.py     # Supabase→R2 双写快照(原子覆盖)
    ├── replay_packages.py  # 重启重装历史 pip 包
    └── keepalive.py         # 下游 Space 保活探测
```

### 2.4 Worker（`workers/gateway/`）

Cloudflare Worker，单文件 `src/index.ts`：
- `GET /health` — 网关自身存活
- `POST /route`（需鉴权）— body `{space, path, body}`，按 `space` 转发到对应 Space `path`，透传鉴权 header，下游超时 60s
- `GET /probe`（需鉴权）— 探测全下游 Space `/health`
- `scheduled` cron — 定时 `probeAllSpaces()` 保活（wrangler.toml `crons`）

---

## 3. API 契约（端到端）

所有调用：JSON POST，Header `Authorization: Bearer <NEXUS_API_KEY>` + `Content-Type: application/json`。

### 3.1 hermes（对用户/Worker 的接口）

| 方法 | 路径 | body | return | 说明 |
|------|------|------|--------|------|
| GET | `/health` | — | `{status,space}` | 保活/唤醒 |
| POST | `/run` | `{prompt, force_space?}` | `{task_id, space, result}` | 主入口：路由→下游→回传 |
| GET | `/state/{thread_id}` | — | state(未实现桩) | 查状态 |
| UI | `/` | — | Gradio Dashboard | 任务路由 + R2文件管理 + 系统状态 |

路由规则（`main.py` `route()`，可改）：含规划/多步/工作流→langgraph；实现/重构/调试→claude；补全/快速/片段→codex；默认 langgraph。`force_space` 强制目标。

### 3.2 下游 Space 接口（hermes 调，经 Worker 为主）

| Space | 端点 | body | 模型 env |
|-------|------|------|---------|
| langgraph | `POST /execute` | `{thread_id, prompt}` | (LangGraph 状态机桩，真实接 LLM 自填) |
| claude-code | `POST /run` | `{thread_id, prompt}` | `ANTHROPIC_API_KEY` + `CLAUDE_MODEL` |
| codex | `POST /complete` | `{thread_id, prompt}` | `OPENAI_API_KEY` + `OPENAI_BASE_URL` + `CODEX_MODEL` |

全部下游 Space 也有 `GET /health`、统一 `auth(authorization)`（配了 `NEXUS_API_KEY` 则校验 `Bearer`，模板阶段未配则放行）。

### 3.3 调用链

```
用户 ─POST /run──> hermes
  hermes: 生成 thread_id, route() 决策 space
  hermes: log_task(thread_id,"hermes","route→<space>","pending") + save_state(...,"dispatched")
  hermes ─call_space(space, path, payload)──> [Worker /route 为主] ──> 下游Space endpoint
  下游: log_task(...,"running") → 执行(写R2/调LLM) → log_task(...,"done")
  hermes: log_task(...,"done") + save_state(...,"done",result)
  return {task_id, space, result}
```

call_space 逻辑（`libs/shared/gateway.py`）：有 `GATEWAY_URL` 先经 Worker `/route`；200-299 用，404 视为网关未配该路由转回退，其它非2xx `raise_for_status`；网关超时/连接错误静默转回退；回退走 `{SPACE}_URL` 直调。两者都无则抛错。

---

## 4. 数据存储契约

### 4.1 存储分层

| 层 | 用途 | 何时用 |
|----|------|--------|
| **R2**（S3兼容,boto3,region='auto'） | Checkpoint blob、Skills 备份、向量文件、大产物 | >1MB 或二进制 |
| **Supabase Postgres** | agent_states、task_logs、long_memory、调度队列 + pgvector | 结构化、需查询 |
| HF Datasets | 临时缓存、必须放 HF 的文件 | 仅兜底，低频读用降风控 |

### 4.2 R2 桶（部署前手动建）

| 桶名 | env | 用途 |
|------|-----|------|
| nexus-checkpoints | `R2_BUCKET` | LangGraph blob(`save_checkpoint`) |
| nexus-artifacts | `R2_BUCKET` | hermes Dashboard 文件管理 + 大产物 |
| nexus-backups | `R2_BUCKET` | Supabase→R2 快照(`persist_to_r2.py`) |
| nexus-skills | (代码未写env,表skills_index引用) | Skills 备份 |
| nexus-vectors | (向量文件) | 向量 |

公开读一律用 Presigned URL，不开 Public Access。每桶设 Lifecycle Rule：N 天清 `tmp/` 前缀。

### 4.3 Supabase 表（`sql/00_schema.sql`，幂等 IF NOT EXISTS）

| 表 | 主键 | 用途 | 写入方 |
|----|------|------|--------|
| `agent_states` | thread_id(jsonb state) | Agent 状态 | `save_state`/`load_state` |
| `task_logs` | bigserial | 任务日志(thread_id,space_name,action,status) | `log_task` |
| `long_memory` | key(jsonb value) | 长期记忆 | `remember`/`recall` |
| `task_queue` | thread_id | 异步任务轮询队列(可选) | (未来) |
| `skills_index` | skill_name | Skill 元数据(内容存R2 nexus-skills) | (未来) |
| `backup_snapshots` | bigserial | Supabase→R2 快照登记(table_name,r2_key,row_count) | `persist_to_r2.py` |
| `space_health` | bigserial | 保活探测留痕(space,status,detail) | `keepalive.py` |

全部表 enable RLS，服务端用 `service_role` 绕过。`01_pgvector.sql` 加 `vector` 扩展 + `memory_vectors` 表 + HNSW 索引（向量搜索用，不需要可跳过）。

---

## 5. 凭证 / 环境变量（全集）

> `.env.example` 是模板。真值走 HF Space Secrets / Cloudflare Worker Secret 注入，**绝不提交 git**。`*.env` 已 gitignore。

### 5.1 各组件所需 env 速查

| env | hermes | langgraph | claude | codex | Worker | 来源 |
|-----|:------:|:---------:|:-----:|:-----:|:------:|------|
| R2_ENDPOINT | ✓ | ✓ | ✓ | ✓ | | R2→Manage API Tokens |
| R2_ACCESS_KEY_ID | ✓ | ✓ | ✓ | ✓ | | 同上 |
| R2_SECRET_ACCESS_KEY | ✓ | ✓ | ✓ | ✓ | | 同上(Secret) |
| R2_REGION | ✓ | ✓ | ✓ | ✓ | | 固定 `auto` |
| R2_BUCKET | | ✓ | | | | 桶名 |
| R2_BUCKET | ✓ | | | | | 桶名 |
| R2_BUCKET | ✓ | | | | | 桶名 |
| SUPABASE_URL | ✓ | ✓ | ✓ | ✓ | (可选) | Project→API |
| SUPABASE_SERVICE_ROLE_KEY | ✓ | ✓ | ✓ | ✓ | (可选) | 同上(服务端) |
| SUPABASE_ANON_KEY | ✓ | ✓ | ✓ | ✓ | | 同上(低权限) |
| SUPABASE_DB_URI | | ✓ | | | | Database直连串(port6543) |
| NEXUS_API_KEY | ✓ | ✓ | ✓ | ✓ | ✓ | 自己生成(`secrets.token_urlsafe(32)`),全系统同一把 |
| GATEWAY_URL | ✓ | | | | | Worker 部署后 |
| LANGGRAPH_URL | ✓ | | | | ✓ | Space URL |
| CLAUDE_URL | ✓ | | | | ✓ | Space URL |
| CODEX_URL | ✓ | | | | ✓ | Space URL |
| ANTHROPIC_API_KEY | | | ✓ | | | Anthropic |
| CLAUDE_MODEL | | | ✓ | | | 默认 `claude-3-5-sonnet-20241022` |
| OPENAI_API_KEY | | | | ✓ | | OpenAI/兼容 |
| OPENAI_BASE_URL | | | | ✓ | | 默认 `https://api.openai.com/v1` |
| CODEX_MODEL | | | | ✓ | | 默认 `gpt-4o-mini` |
| KEEPALIVE_ENABLED | ✓ | | | | | start.sh 用, `1`=起 keepalive |
| KEEPALIVE_INTERVAL_BASE | ✓ | | | | | 默认 600 |
| KEEPALIVE_INTERVAL_JITTER | ✓ | | | | | 默认 180 |
| SYNC_INTERVAL_SEC | ✓ | | | | | persist_to_r2 间隔,默认 300 |
| REPLAY_LOG | ✓ | | | | | replay_packages 日志,默认 `/app/installed_packages.log` |
| SPACE_OWNER (Worker var) | | | | | ✓ | HF 用户名, wrangler.toml [vars] |

### 5.2 生成 NEXUS_API_KEY

```bash
python -c "import secrets;print(secrets.token_urlsafe(32))"
```

全系统 Space 与 Worker 共用同一把。Worker 用 `npx wrangler secret put NEXUS_API_KEY` 注入。

### 5.3 安全红线

1. `SUPABASE_SERVICE_ROLE_KEY`、`R2_SECRET_ACCESS_KEY`、`NEXUS_API_KEY` 只走 Secrets，不入代码、不入 `.env`。
2. 提交前自查：`grep -rnE "(sk-|token|secret|password|api_key)" --include=*.py --include=*.env .` 不应命中真值。
3. 最低权限：anon key 能办到别用 service_role。
4. 建议每 90 天轮换 `NEXUS_API_KEY` 与 R2 token。

---

## 6. 增强机制（借自 HermesFace / HuggingMes，已转 R2+Supabase 版集成进 hermes）

| 点 | 来源 | 实现 | 文件 |
|----|------|------|------|
| Cloudflare Keep-Alive | HuggingMes | Worker `/probe`+cron；hermes `keepalive.py` 随机延时防特征 | `workers/gateway/`、`spaces/hermes/scripts/keepalive.py` |
| Ephemeral Package Replay | HuggingMes | 重启重装历史 pip 包 | `spaces/hermes/scripts/replay_packages.py`、`start.sh` |
| 自愈 Gateway | HuggingMes | `start.sh` while 循环重启崩溃 app（5s 重启） | `spaces/hermes/start.sh` |
| 原子备份/恢复 | HermesFace | R2 写 tmp→copy 原子替换(替代HF Dataset原子写),登记 backup_snapshots | `spaces/hermes/scripts/persist_to_r2.py` |
| Supabase→R2 双写同步 | 两者 | 周期快照 Supabase 表写 R2(默认5分钟) | 同上 |
| Dashboard 文件管理 | 两者 | Gradio Tab:R2 上传/读入/编辑/保存/删除/刷新 | `spaces/hermes/app/main.py` |

### hermes `start.sh` 启动流程

1. `replay_packages.py replay`（有日志则重装历史包，无则跳）
2. 若 `SUPABASE_URL` 非空 → 后台 `nohup persist_to_r2.py`（双写快照）
3. 若 `KEEPALIVE_ENABLED=1` → 后台 `nohup keepalive.py`（探测下游）
4. 主：`while true; uvicorn app.main:app :7860` 自愈循环

### 保活策略

- **主**：外部监测网站定期 ping 各 Space `/health`（用户已确认稳定可用）
- **辅**：Worker cron + hermes `keepalive.py` 内部互探，间隔随机化避免固定周期被风控识别

---

## 7. 端到端部署（接手即跑顺序）

### 前置：账号与套餐

| 项 | 要求 | 风险点 |
|----|------|--------|
| HF 账号 | ZeroGPU 免费2个Gradio；Docker 需 PRO/Team | 3个Docker Space 创建前确认套餐 |
| Cloudflare | R2 免费10GB+100万A类操作/月 | 超量计费,配 Lifecycle 清理 |
| Supabase | 免费500MB DB+2项目 | service_role 仅服务端 |
| GitHub | 私有库免费 | nexus 私有 |

### 步骤 1：Cloudflare R2

建桶 `nexus-checkpoints`/`artifacts`/`backups`/`skills`/`vectors`；Manage R2 API Tokens 生成 token 记录 `R2_ACCESS_KEY_ID`/`R2_SECRET_ACCESS_KEY`/`R2_ENDPOINT`；每桶设 Lifecycle 清 `tmp/`。

### 步骤 2：Supabase Postgres

建项目记录 URL/anon/service_role；SQL Editor 跑 `sql/00_schema.sql`，需要向量再跑 `01_pgvector.sql`；取**直连串**(Database→Connection string→URI, **port 6543 transaction pooler**) 给 langgraph，勿用 5432 session pooler。

### 步骤 2.5：构建前同步共享库（关键易漏）

```bash
bash scripts/sync-spaces.sh   # libs/ → spaces/*/libs/
```

每次改根 `libs/` 后、`git push` 前必跑。

### 步骤 3：部署 hermes

HF New Space(docker,私有,命名 `hermes`)；Settings→Secrets 加 5.1 表里 hermes 徽标✓的全部；push `spaces/hermes/`；验证 GET `/health` 200、POST `/run` 返 task_id、查 `task_logs` 有记录。

### 步骤 4：部署下游 Space

依次 langgraph/claude-code/codex：New Space(docker,私有)；Secrets 按表加；push 对应 `spaces/<name>/`；GET `/health` 200。

### 步骤 5：Worker 网关

```bash
cd workers/gateway && npm install
npx wrangler secret put NEXUS_API_KEY      # 与各 Space 同一把
# 编辑 wrangler.toml 的 SPACE_OWNER (<你的HF用户名>)
npx wrangler deploy
```

记下输出 URL 填 hermes 的 `GATEWAY_URL` Secret，再回填到 .env。

### 步骤 6：端到端

```bash
curl -X POST <hermes_url>/run -H "Authorization: Bearer $NEXUS_API_KEY" \
  -d '{"prompt":"测试:让 langgraph 做一个三步规划"}'
```

期望：返 task_id，`task_logs` 全链 done，R2/Supabase 有产物。

### 保活（可选）

外部监测网站 ping 各 Space `/health`；或 Worker cron（wrangler.toml `crons`，建议低频别太高）；或 hermes `KEEPALIVE_ENABLED=1`。

---

## 8. 技术栈版本锚点（已对照官方文档 2026-07）

| 组件 | 库 | 版本(requirements) | 关键 API |
|------|----|----|----|
| LangGraph Checkpoint | `langgraph-checkpoint-postgres` | 2.0.10 | `AsyncPostgresSaver.from_conn_string(uri)` + `await cp.setup()` |
| LangGraph 本体 | `langgraph` | 0.2.74 | `StateGraph`, `compile(checkpointer=cp)`, `ainvoke(...,config={...,"thread_id":...})` |
| Supabase | `supabase` | 2.13.0 | `create_client(url,key,options=None)`, `upsert(json,on_conflict=,ignore_duplicates=)`, `table().select/insert/upsert/eq/maybe_single().execute()` |
| R2 (S3 兼容) | `boto3` | 1.36.0 | `endpoint_url=,region_name='auto'`, `put_object/get_object/copy_object/delete_object/list_objects_v2/upload_fileobj/generate_presigned_url` |
| HTTP 调用 | `httpx` | 0.28.1 | `AsyncClient(timeout=)`, `AbortSignal.timeout(60_000)`(Worker) |
| FastAPI | `fastapi` | 0.115.6 | 挂 Gradio: `gr.mount_gradio_app(fastapi_app, demo, path="/")` |
| Gradio | `gradio` | 5.9.1 | `gr.Blocks`/`Tab`/`Dataframe`/`File`/`TextArea` |
| Uvicorn | `uvicorn[standard]` | 0.34.0 | `uvicorn.run("app.main:app",host,port)` |
| Worker | `wrangler` | ^3.99.0 | `secret put` / `deploy` / `cron triggers` |

Anthropic API：`POST https://api.anthropic.com/v1/messages`，header `x-api-key` + `anthropic-version: 2023-06-01`。
OpenAI 兼容：`POST {OPENAI_BASE_URL}/chat/completions`，header `Authorization: Bearer {OPENAI_API_KEY}`。

> 模型 ID 默认值(代码里) `claude-3-5-sonnet-20241022` / `gpt-4o-mini` 为部署期占位；最新一轮 Claude 模型为 Claude 5 家族：Fable 5=`claude-fable-5`、Opus 5=`claude-opus-5`、Sonnet 5=`claude-sonnet-5`、Haiku 4.5=`claude-haiku-4-5-20251001`。构建 AI 应用默认用最新最强。部署时按需改 env `CLAUDE_MODEL`/`CODEX_MODEL` 覆盖。

---

## 9. 二次开发指引

### 改共享库
改 `libs/` → 跑 `sync-spaces.sh` → 各 Space 重新部署。勿直接改 `spaces/*/libs/`（会被下次 sync 覆盖）。

### 加下游 Space
1. 复制 `spaces/codex/` 结构改 `app/main.py` 端点
2. 加 `route()` 关键词 + `_target_path()` 映射（hermes `main.py`）
3. `libs/shared/gateway.py` `_SPACE_URLS` 加 URL env
4. Worker `src/index.ts` `SPACE_REPOS` 加 repo 名
5. `DEPLOYMENT.md` 步骤 4 加该 Space

### 改路由决策
`spaces/hermes/app/main.py` 的 `route()`：当前关键词启发式，可后续接模型分类或规则表。

### 替换下游 LLM 桩
langgraph `node_*` 是桩返回。真实接入在 `node_understand`/`node_plan`/`node_output` 里调 LLM，参考 claude-code `main.py` 的 httpx 调法。

### 改保活频率
`wrangler.toml` `crons`（Worker）、`KEEPALIVE_INTERVAL_BASE`/`JITTER`（hermes）。别太高频避免风控。

---

## 10. 运维 / 排障 / 回滚

| 症状 | 排查 |
|------|------|
| Space 起不来 | HF Space logs；查 README frontmatter `sdk:docker`/`app_port:7860`；Dockerfile `PYTHONPATH=.../libs` 是否对 |
| `R2_ENDPOINT 未设置` | Space Secrets 漏 R2_*,或 sync 没跑导致 `from storage import` 失败 |
| `ASYNC/连接复用冲突` | `SUPABASE_DB_URI` 用了 5432，改 6543 transaction pooler |
| 下游 502/down | 先 `GET /health`；休眠则冷启动等数十秒；Worker 转发超时调 `AbortSignal.timeout` |
| Dashboard 文件操作报错 | `R2_BUCKET` 桶是否建；Secret 是否注入 |
| hermes 崩溃不重启 | `start.sh` while 循环 5s 重启；查 `logs/persist.log`/`keepalive.log` |
| 401 unauthorized | `NEXUS_API_KEY` 各组件不一致，或 header 格式错(需 `Bearer <key>`) |

### 回滚
- Space 出错：Settings→Restart；代码回滚 `git push -f` 到上个 commit
- 表结构变更：先备份 → 改 SQL → 跑 `02_*.sql` 增量脚本
- Supabase→R2 备份恢复：查 `backup_snapshots` 表按 `table_name+created_at` 找 `r2_key`，R2 拉快照 JSON 重建

---

## 11. 文件清单速查（全 60+ 文件）

按职能分组，改哪类看哪行：

- **架构/通信/部署/凭证文档** → `docs/*.md`
- **本接手手册** → `docs/HANDBOOK.md`（本文件）
- **共享存储封装** → `libs/storage/storage.py`
- **共享 HTTP 调用** → `libs/shared/gateway.py`
- **LangGraph Checkpoint 适配** → `libs/shared/checkpointer.py`
- **hermes 主控(Dashboard+路由+文件管理)** → `spaces/hermes/app/main.py`
- **hermes 启动自愈+后台** → `spaces/hermes/start.sh`
- **hermes 双写快照** → `spaces/hermes/scripts/persist_to_r2.py`
- **hermes 保活探测** → `spaces/hermes/scripts/keepalive.py`
- **hermes 包重放** → `spaces/hermes/scripts/replay_packages.py`
- **下游三个 Space 业务** → `spaces/{langgraph,claude-code,codex}/app/main.py`
- **Worker(鉴权+路由+cron保活)** → `workers/gateway/src/index.ts` + `wrangler.toml`
- **SQL 建表** → `sql/00_schema.sql` + `sql/01_pgvector.sql`
- **同步脚本** → `scripts/sync-spaces.sh`
- **凭证模板** → `.env.example`
- **各 Space 镜像/依赖/README config** → `spaces/*/Dockerfile`+`requirements.txt`+`README.md(frontmatter)`

---

## 12. 当前状态与下一步

**当前**：模板阶段。全部代码直接可部署，31 个 Python 文件全编译通过，Worker `tsc --noEmit` 通过，`sync-spaces.sh` 校验 4 个 Space libs 与根一致无 pycache 残留。凭证未真填，不含任何密钥。

**演进路径**：
```
阶段0 模板(当前) → 阶段1 凭证就位单Space跑通 → 阶段2 全链路 → 阶段3 保活/监控完善
```

**接手第一步建议**：通读本手册→读 `docs/ARCHITECTURE.md`(架构纵深)→按 §7 部署步骤断点验证→改功能看 §9。遇到平台行为疑问先查官方文档不硬编码假设。
