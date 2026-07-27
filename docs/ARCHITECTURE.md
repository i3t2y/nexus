# Nexus 架构总览

> 混合 Agent 系统：HF Spaces 计算 + Cloudflare R2 大文件 + Supabase Postgres 结构化状态。
> 目标：稳定、低成本、低暴露。

## 设计原则

1. **计算与存储分离** — HF Space 只当计算单元，所有持久状态走 R2 / Supabase。Space 重启不丢数据。
2. **主控路由** — Hermes 唯一入口，下游 Space 不直接对外。降低暴露面。
3. **凭证外置** — 所有密钥通过 Space Secrets / 环境变量注入，不入库不入代码。
4. **模板先行** — 凭证未到位前，全部文件可直接部署，填 `.env` 即跑。

## 组件分布

| Space | 角色 | SDK | 状态 |
|-------|------|-----|------|
| `hermes` | 主控大脑：Gradio Dashboard + FastAPI 路由（监听7860）+ 双写/保活/自愈后台 | Docker | 模板 |
| `langgraph` | 复杂工作流编排，含 Checkpointer | Docker | 模板 |
| `claude-code` | 复杂推理、代码生成 | Docker | 模板 |
| `codex` | 快速编码、补全 | Docker | 模板 |

Space 间通过 `https://{owner}-{space}.hf.space` 互调，鉴权用共享 `NEXUS_API_KEY`（见 [通信方案](./COMMUNICATION.md)）。

## 存储分层

```
        ┌──────────────────────────────────────┐
        │            HF Spaces (计算)            │
        │  hermes  langgraph  claude  codex     │
        └───────────────┬──────────────────────┘
                        │  (Secrets 注入凭证)
            ┌───────────┴───────────┐
            ▼                       ▼
   ┌─────────────────┐     ┌──────────────────────┐
   │  Cloudflare R2   │     │  Supabase Postgres    │
   │  大文件/Checkpoint │     │  结构化/状态/Memory   │
   │  /Skills/向量     │     │  + pgvector 向量搜索  │
   └─────────────────┘     └──────────────────────┘
```

| 层 | 用途 | 何时用 |
|----|------|--------|
| **R2** | Checkpoint blob、Skills 备份、向量文件、大对象 | >1MB 或二进制 |
| **Supabase** | agent_states、task_logs、long_memory、调度队列 | 结构化、需查询 |
| **HF Datasets** | 临时缓存、必须放 HF 的文件 | 仅兜底 |

## 数据流（典型任务）

1. 请求到 Hermes `/run`，写 `task_logs`（status=pending）。
2. Hermes 路由决策 → 调下游 Space（带 `thread_id`）。
3. 下游 Space 读 Supabase 取 state，执行，写 R2 存大产物，回传结果。
4. Hermes 收结果 → upsert `agent_states`、更新 `task_logs`（status=done）。
5. LangGraph 长流程用 `AsyncPostgresSaver` 做 Checkpoint（状态进 Postgres，blob 进 R2）。

## ⚠️ 关键约束（部署前必读）

### HF Spaces 付费墙

自 2024 年起，**Docker / Gradio Space 跑 compute 需付费套餐**：
- 个人免费账号可跑最多 **2 个 Gradio Space (ZeroGPU)**，CPU Basic 免费。
- **Docker SDK Space 原则上需 PRO / Team 付费套餐创建。**
- 本方案 4 个 Space 全用 Docker，**部署时需确认账号开通了对应套餐**，否则 langgraph/claude/codex 三个 Docker Space 无法创建。Hermes 可改 Gradio + ZeroGPU 跑在免费层。

### 免费 Space 会休眠

免费硬件（CPU Basic / ZeroGPU）闲置后会"睡"。首调冷启动慢（数十秒）。
- 常驻需求 → Hermes 用 keep-alive 或升级付费。
- 休眠具体时长随政策变，以 [官方文档](https://huggingface.co/docs/hub/spaces-overview) 为准，不硬编码进代码。

### 网络

HF Space 出站仅 80 / 443 / 8080。R2(S3 API)、Supabase(HTTPS) 均走 443，无碍。

### 持久化

Space 内 `/data` 持久存储**已下线**。跨重启持久必须用 R2 / Supabase，不依赖本地磁盘。

### 鉴权 header 冲突（私有 Space 必读）

私有 HF Space 的 HF Gateway 用 `Authorization: Bearer <HF_TOKEN>` 鉴权**这层**。若 Worker/直调也用 `Authorization` 传 `NEXUS_API_KEY`，会**覆盖** HF 层 token → HF 层 401，请求进不到 Space app。

解法：下游 Space 鉴权改用自定义 header **`X-Nexus-Key: Bearer <NEXUS_API_KEY>`**（各 Space `auth()` 读它，回退 `Authorization` 兼容）；`Authorization` 留给 HF 层（私有 Space 注入 `HF_TOKEN`）。只在调独立的 Cloudflare Worker 网关层（无 HF 层）时，入站鉴权仍用 `Authorization`。

### auth fail-closed

`auth()` 缺 `NEXUS_API_KEY` 时拒绝（500 配置错误），不"忘配即放行"。本地免鉴权显式设 `NEXUS_AUTH_MODE=dev`。模板默认走生产语义，降低误留 open 的风险。

### Worker path 白名单（防 SSRF）

`workers/gateway/src/index.ts` `/route` 对下游 `path` 做白名单（`/execute` `/run` `/complete` `/health`），挡住任意 path 透传到任意 URL/端点。`/` 起头校验 + `..` 检测防回溯与绝对 URL。

### 幂等键（防双扣费 / 双执行）

`task_queue.idempotency_key` UNIQUE 列。`POST /enqueue` 接 `Idempotency-Key` header；同键重复入队命中已有，不重复执行。LLM POST 本身非幂等——上自动重试必先配幂等键，否则 5xx/超时已执行场景会双扣费/双执行。`/dequeue` 单消费者模板（两步 select+update 非原子锁，多消费者需改 Postgres `FOR UPDATE SKIP LOCKED` 直连）。

### lifespan 池化（langgraph）

`langgraph app/main.py` 用 FastAPI `lifespan` 启动建一次 `AsyncPostgresSaver` + `await cp.setup()` + 编译 graph，存 `app.state`；请求复用全局 checkpointer+graph，**不再每请求 setup**（违背文档"setup() 仅启动一次"+ 每次新连接开销量）。`AsyncPostgresSaver` 由 `build_checkpointer()`（`libs/shared/checkpointer.py`）构造，`from_conn_string` 内已设 `prepare_threshold=0`/`autocommit`/`row_factory`，6543 安全。

### Hermes Agent ≠ HTTP 服务（查证修正）

Nous Research 开源 Hermes Agent（`github.com/NousResearch/hermes-agent`，`curl install.sh | bash` 安装）是 **TUI/CLI 交互式 agent**，基于 `uv` 装在 `~/.hermes/`。`hermes gateway start` 指**消息平台网关**（Telegram/Discord 等），**不监听 HTTP 端口**，**无 `--port` 参数**。

故本方案 **不依赖原生 hermes CLI 作 Space 的 HTTP 主控**——HF Space 要求单进程监听 7860。Hermes Space 用自建 **Gradio Dashboard + FastAPI 路由** 同进程实现（监听 7860）。原生 hermes CLI 可选作**本地增强层**（自学习 Skills 等），非架构硬依赖。

> 注：部分参考文档提到 `hermes gateway start --port 7860`、`/learn`、`/goal`、`hermes skills install <name>`，官方 README（2026-07 查证）**均无**。前端方案不照搬，避免采坑。

## 技术栈版本锚点

| 组件 | 库 | 安装 |
|------|----|----|
| LangGraph Checkpoint | `langgraph-checkpoint-postgres` | `pip install langgraph-checkpoint-postgres` |
| Supabase | `supabase` | `pip install supabase` |
| R2 (S3 兼容) | `boto3` | `pip install boto3` |
| HTTP 调用 | `httpx` | `pip install httpx` |

API 已对照官方文档（2026-07）：
- `AsyncPostgresSaver.from_conn_string(uri)` + `await checkpointer.setup()`。底层 **psycopg3**（非 asyncpg），`from_conn_string` 硬编码 `prepare_threshold=0`（禁 server-side prepared statement）+ `autocommit=True` + `row_factory=dict_row`，故 Supabase 6543 transaction pooler 直连安全，无需额外兜底。
- `supabase.create_client(url, key, options=None)`，`upsert(json, on_conflict=, ignore_duplicates=)`

## 增强机制（Hermes Space）

借自 HermesFace / HuggingMes 两项目的最高价值点，已改成 R2+Supabase 版集成进 Hermes Space：

| 点 | 来源 | 实现 | 文件 |
|----|------|------|------|
| Cloudflare Keep-Alive | HuggingMes | Worker `/probe` + cron triggers；Hermes 端 `keepalive.py`（随机延时避免规律节奏） | `workers/gateway/`、`spaces/hermes/scripts/keepalive.py` |
| Supabase 自身保活 | 查证补充 | `keepalive.py` 每轮 `space_health` insert = 轻量写 DB，防免费档 1 周不活跃自动暂停（查证 pricing.ts） | `spaces/hermes/scripts/keepalive.py` |
| Ephemeral Package Replay | HuggingMes | 重启重装历史 pip 包 | `spaces/hermes/scripts/replay_packages.py`、`start.sh` |
| 自愈 Gateway | HuggingMes | `start.sh` while 循环重启崩溃 app | `spaces/hermes/start.sh` |
| 原子备份/恢复 | HermesFace | R2 写 tmp→copy 原子替换（替代 HF Dataset 原子写） | `spaces/hermes/scripts/persist_to_r2.py` |
| Supabase→R2 双写同步 | 两者 | 周期把 Supabase 表快照写 R2（5分钟） | 同上 |
| Dashboard 文件管理 | 两者 | Gradio Tab：R2 文件 上传/读入/编辑/保存/删除/刷新 | `spaces/hermes/app/main.py` |

Hermes Space 目录：
```
spaces/hermes/
├── README.md  Dockerfile  requirements.txt  start.sh
├── app/main.py        # Gradio Dashboard + FastAPI 路由（同进程7860）
├── libs/              # 同步的共享库（storage/gateway）
└── scripts/
    ├── persist_to_r2.py      # Supabase→R2 双写快照（原子覆盖）
    ├── replay_packages.py    # 重启重装包
    └── keepalive.py          # 下游 Space 保活探测
```

保活策略（用户已确认外部监测网站稳定可用）：
- **Hermes/下游 Space**：主=外部监测网站 ping `/health`（已验证稳定）；辅=Worker cron + Hermes `keepalive.py` 内部互探，间隔随机化避免固定周期形成规律节奏。
- **Supabase**：免费档 1 周不活跃自动暂停（仅停 compute，数据不丢可恢复）。`keepalive.py` 每轮写 `space_health` 表即刷"上次活动"。

## 演进路径

```
阶段0 模板 (当前) → 阶段1 凭证就位单 Space 跑通 → 阶段2 全链路 → 阶段3 保活/监控
```

详见 [DEPLOYMENT.md](./DEPLOYMENT.md)、[CREDENTIALS.md](./CREDENTIALS.md)。
