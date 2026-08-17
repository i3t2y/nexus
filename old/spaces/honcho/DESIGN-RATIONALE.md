# Nexus Honcho 自建 HF Docker Space — 设计论证与源码实证

> 日期: 2026-08-03
> 仓: github.com/plastic-labs/honcho v3.0.11 (commit 14538cfc, AGPL-3.0-only)
> 源码实证 clone: /tmp/honcho_check/honcho (只读参考)
> plan: H 段 (ExitPlanMode 批),与 hermes 换装 K 段并列独立
>
> 目标: 给 hermes 接管的用户记忆补一层(user-centric memory + social cognition),
> 叠加非替换,不动 nexus 现有 4 组件架构(hermes/claude-code/codex/langgraph)。
> 接管 user memory,替换 nexus `long_memory` 手搓胶水。

---

## 1. 形态决策: minimal Python launcher shim,不 fork honcho 源码

### 决策
不 fork,不 diff honcho 源码。build 期 `git clone --depth 1 --branch v3.0.11` + `uv sync --frozen`。
shim `launcher.py` 运行时替换 `app.router.lifespan_context`,压 deriver+api 一 uvicorn 进程。

### 源码实证(决策依据)
- `src/main.py` 模块级 `app = FastAPI(lifespan=lifespan)`,`lifespan` 是普通 `@asynccontextmanager`
  → `app.router.lifespan_context` 属性可运行时替换为自定义 lifespan。
  FastAPI 路由器把 lifespan 包成 `_LifespanContextManager`,赋值即替换。
- `src/deriver/queue_manager.py:1132 async def main()`: 纯 `async def` coroutine
  (`QueueManager().initialize()` polling loop),非独立 server/进程。
  → `asyncio.create_task(deriver_main())` 在 api 同 event loop 压一进程跑,无第二进程。
  (HF Space 单进程硬约束)
- `src/db.py:240 async def init_db()`: 跑 `CREATE SCHEMA` + `CREATE EXTENSION vector` + `alembic upgrade head`(命令同步)。
- fork 代价 = 永久背 upstream 升级债;shim = 0 行 honcho 源码 diff。升级 honcho = 改 ARG tag + rebuild。

### 镜像内实证(2026-08-03,本会话)
docker run --rm honcho-hf-local python -c:
- `from src.main import app` → `<class 'fastapi.applications.FastAPI'>` ✓
- `from src.deriver.queue_manager import main` → coroutine True ✓
- `from src.db import init_db` → coroutine True ✓
- `from src.startup import validate_embedding_schema` → callable True ✓
- ★ `honcho_app.router.lifespan_context = fake` 赋值后 `orig is not new = True` ✓✓
  → shim 核心假设坐实,无 fallback 重构造 FastAPI 需。
- FastAPI 版本: 0.131(镜像内实测支持属性替换)
- python: 3.11.15(FROM python:3.11-slim-bookworm 对齐 .python-version=3.11,省 uv 拉 python)

---

## 2. 引导顺序: init_db 必须先于 deriver task + orig lifespan

### 推导
shim 引导顺序:

```
1. await init_db()              # 建 25 表 + 两 vector 列(维度=EMBEDDING_VECTOR_DIMENSIONS)
2. create_task(deriver_main)    # deriver polling 立即上 loop(返 task 对象)
3. await orig_lifespan(app)     # 跑 validate_embedding_schema + init_cache + telemetry (yield 阻塞服务 loop)
4. finally: deriver_task.cancel()
```

### 源码实证(顺序刚性原因)
- `src/startup/embedding_validator.py:61 validate_embedding_schema`:
  启动期校验 `EMBEDDING_VECTOR_DIMENSIONS` vs pgvector 列维度,fail-closed(不符 raise 拒起)。
  honcho 自带 tenacity 重试 3 次(SQLAlchemyError),首启表未建则重试 3 次后 raise。
- 这些表由 `init_db()` 创建(alembic upgrade head)。
- 故 init_db 必须先于 orig_lifespan(其内 validate_embedding_schema)。
- deriver `queue_manager.main()` 内部也调 `validate_embedding_schema`,故 init_db 也必须先于 deriver task。
- init_db 无重试 → shim 加 `_provision_db_with_retry`(tenacity 已 deps)3 次 fixed 1s backoff 兜 transient 抖动。

---

## 3. env 名与 LLM routing 实证

### 关键纠错: 顶层 `LLM_OPENAI_BASE_URL` 存在但不被 feature 读
- `config.py:716` 顶层 `LLM_OPENAI_BASE_URL` **存在**(settings 字段)。
- 但各 feature(DERIVER/SUMMARY/EMBEDDING/DIALECTIC/DREAM)的 `resolve_model_config`(`config.py:467`)
  **只读 `__OVERRIDES__BASE_URL`,不回退顶层**。
- 故每 feature 须显式 `__MODEL_CONFIG__OVERRIDES__BASE_URL=https://nonoke-omn.hf.space/v1` 指 omniroute。
- 仅设顶层 = 各 feature base_url 落 default 指官方 OpenAI → 401。

### API key 单源
- `_default_embedding_api_key`(`config.py:484-489`): embedding transport=openai 时,
  api_key 自动回退 `LLM_OPENAI_API_KEY`(单 key 源)。
- openai transport 的每 feature(DERIVER/SUMMARY/EMBEDDING/DREAM/DIALECTIC)自动复用此 key。
- 不需每 feature 设 `__OVERRIDES__API_KEY`。anthropic/gemini transport 不读此 key
  (本部署默认全 openai transport 走 omn,故此一条够)。

### env 前缀全实证
`DB_` `AUTH_` `LLM_` `EMBEDDING_` `DERIVER_` `SUMMARY_`
`DIALECTIC_`(级别 minimal)`DREAM_`(DEDUCTION/INDUCTION_MODEL_CONFIG)
`CACHE_` `METRICS_` `TELEMETRY_` `SENTRY_` `VECTOR_STORE_`

### partial env override 自动 fill default
- `config.py:517 _fill_defaults_for_nested_field`:
  env override 中只设部分键(如 MODEL/TRANSPORT/OVERRIDES__BASE_URL 三键),其余键自动从默认 factory fill。
- 故每 feature 只设三键即可,余键继承 default。

### DIALECTIC 只设 minimal(hermes 走 minimal 一级)
- `config.py:1023 _merge_level_defaults`: minimal 即可。
- 其余 level 会 backfill 默认 model+openai transport,但 **base_url 不继承** → 指官方 OpenAI → 401。
- 扩他 level 再补同形态 `DIALECTIC_LEVELS__<level>__MODEL_CONFIG__*`。

---

## 4. 关省决策(省进程/端口/外部调用)

| Secret | 值 | 理由(源码实证) |
|---|---|---|
| `CACHE_ENABLED` | `false` | default 已 false(`config.py:1213`)。单进程单实例无多 worker 共享需求。`init_cache`(`main.py` lifespan + `queue_manager.main` 均 try/except 容错)关了仅 warning 不崩。省 Redis 进程。显设保险。 |
| `METRICS_ENABLED` | `false` | default 已 false(`config.py:1145`)。deriver `__main__.py:88 if ENABLED: start_http_server(9090)` → HF 单进程多端口对外无价值。关掉 zero-overhead。 |
| `TELEMETRY_ENABLED` | `false` | default 已 false(`config.py:1159`)。CloudEvents 外发关。 |
| `SENTRY_ENABLED` | `false` | default 已 false(`config.py:697`)。错误上报关。 |
| `DREAM_ENABLED` | `false` | 省心首版关(省了两 specialist LLM 调用,无 idle dream)。关后 deriver 仍跑(main dialectic/summary/reconciler),仅 dream 阶段跳。想用 dream 删此行 + 上面两 specialist env 在位。 |

### 镜像内 settings default 实证(2026-08-03)
docker run --rm honcho-hf-local python -c settings dump:
- AUTH_USE_AUTH = False ✓
- CACHE_ENABLED = False ✓
- METRICS_ENABLED = False ✓
- EMBEDDING_VECTOR_DIMENSIONS = 1536 ✓
→ .env.example 显设保险正确(default 已是目标值)。

---

## 5. AUTH 决策(HF 公网必开)

### 源码实证
- `security.py:211-212`: `AUTH_USE_AUTH=false` 时 `require_auth` 返回 admin JWTParams 全放行
  → 所有 `/v3/*` 路由裸奔读写 admin。HF 公网必开。
- `config.py:689`: `AUTH_USE_AUTH=true` 且 `AUTH_JWT_SECRET` 空 → raise 拒起(fail-closed)。
- `scripts/generate_jwt.py --admin` 生成 admin JWT,hermes 侧持 JWT 调。
- `/health` `/metrics` 无 auth 依赖 → 健康探活仍可达。

---

## 6. DB 决策: 新 Neon 项目 `sonoke-honcho`,pgvector

### 6.1 为何选 Neon(对比四方案)

honcho 需 PostgreSQL **带 pgvector extension**(不退化则 vector 列+ANN 查询必需)。选型对比:

| 方案 | 判 | 理由 |
|---|---|---|
| **★ Neon serverless Postgres(选)** | 采用 | (1) 原生支持 pgvector(`init_db` 首启 `CREATE EXTENSION vector` 自动开,无需 dashboard 预开)。(2) serverless scale-to-zero,HF Space 休眠期无 DB 计费(与 honcho 长连接 + 低频读写模式对齐)。(3) pooled endpoint(-pooler)匹 honcho `db.py:37-46` SQLAlchemy QueuePool(pool_size=10+max_overflow=20)。(4) **与 nexus 现役 Supabase 隔离** — 独立 Neon 项目 `sonoke-honcho` 不混 nexus Supabase 业务表(agent_states/task_logs/long_memory/skills_index),honcho 自管 25 表 migrations 不撞 nexus schema,故障域隔离。(5) sslmode=require 原生支持 psycopg3(Neon serverless 默认 TLS)。 |
| Supabase(复用 nexus 现) | 否决 | (1) 混库 — honcho 25 表 alembic migration 会与 nexus 11 表 schema 并存,命名冲突风险 + 迁移生命周期耦合(升级 honcho migrate 改 nexus Supabase 状态)。(2) Supabase 免费 tier 连接池上限 + session pooler vs transaction pooler 与 honcho QueuePool 模式需对齐核。(3) honcho 是**叠加非替换**,记忆层独立 DB 隔离符合"补层不触核心"红线精神。(4) 退役 honcho 不影响 nexus 业务表 — 独立项目随手弃。 |
| 自建 Postgres(Docker/VPS) | 否决 | (1) HF Space 单进程硬约束下无法同进程跑 Postgres,外挂 VPS 违"维持 HF 免费档"基调。(2) 自维护 pgvector extension install + 备份 + 升级 = 运维负担。(3) 无 scale-to-zero → 闲置仍计费(虽 VPS 统包价但资源独占无弹性)。 |
| 其他 serverless(Supabase 外) | 次选 | Neon 已满足所有硬需(pgvector+serverless+pooler+TLS),无横向差异收益,选成熟度最高 + honcho 官方 docker-compose.example 即 `pgvector/pgvector:pg15` 同函 PG 流 → Neon serverless PG 即等 pgvector-on-serverless,迁移零语义差。 |

### 6.2 隔离铁律(为何不混 nexus Supabase)

nexus 现役 Supabase 11 表(agent_states/task_logs/task_queue/long_memory/skills_index + RLS 7 表)是 **hermes 业务编排 + 四 Space 共享态表**。
honcho 25 表(alembic migrations,workspace/peer/session/message/conclusion/documents/message_embeddings 等)是 **user-centric memory + social cognition** 两完全正交数据域:
- 生命周期: nexus Supabase 表随 hermes 任务生命周期;honcho 表随 user/peer/session 长期记忆。
- 迁移自治: honcho 升级改 tag 触发其 alembic migrate,**不触 nexus Supabase 任何表**(独立项目则物理隔离,误操作炸不到)。
- 退役面: honcho 退役 = 删 Neon 项目 `sonoke-honcho` 一刀,nexus Supabase 零影响。
- 红线对齐: 用户拍 honcho"叠加非替换,不动 nexus 现有 4 组件架构" → 数据层同隔离是物理落地。

### 源码实证(6.3)
- `init_db()`(`db.py:240-254`): 自带 `CREATE EXTENSION IF NOT EXISTS vector`
  → 首启直连 Neon 自动开 extension,不需 dashboard 预开。
- `migrations/env.py:161`: 双保险(alembic 内亦 CREATE EXTENSION)。
- `db.py:232`: schema 默认 `public`,可不设。
- `db.py:37-46`: SQLAlchemy QueuePool pool_size=10 + max_overflow=20
  → 用 **pooled endpoint(-pooler)** 拼 Neon serverless。
- DSN 必带 `sslmode=require`(Neon serverless psycopg3 ssl)
  → SQLAlchemy 形式前缀 `postgresql+psycopg://`(非 `postgres://`)。
- 维度锁定: 首启 migrate 时 `EMBEDDING_VECTOR_DIMENSIONS` 必须匹配 omniroute embedding 模型 dim
  → migrate 建表后列维度固化。后续切模型若 dim 变 → `scripts/configure_embeddings.py` 重配
  或手动 ALTER 列,否则 `validate_embedding_schema` 拒起。

---

## 7. embedding 决策: 走 omniroute 默认 + R1 闸门实测

### 主线
- `_default_embedding_api_key`(`config.py:484-489`): embedding transport=openai 时
  api_key 自动回退 `LLM_OPENAI_API_KEY` → **单 key 源**。
- base_url 须显式 `EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL`(顶层无全局 base_url)。
- `EMBEDDING_VECTOR_DIMENSIONS` 必须匹配 embedding 模型实际输出 dim
  → 否则 `validate_embedding_schema`(`startup/embedding_validator.py:61`)fail-closed 拒起。
- text-embedding-3-small → 1536。

### 降级路(R1 不过则改)
若 omn `/v1/embeddings` 不可用(404/401/模型不在/维度非 1536):
1. 改 `EMBEDDING_MODEL_CONFIG__MODEL` 指独立供应商模型
2. 改 `EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL` 指该供应商
3. 加 `EMBEDDING_MODEL_CONFIG__OVERRIDES__API_KEY_ENV=EMBEDDING_API_KEY`
   (指向独立 env 变量名,`config.py:414 _resolve_secret`)
4. 加 HF Secret `EMBEDDING_API_KEY=<独立供应商 key>`
5. 改 `EMBEDDING_VECTOR_DIMENSIONS` 匹配新模型 dim
6. **deriver/summary/dialectic/dream 仍走 omniroute**(text gen 独立于 embedding),仅 embedding 降级

注: `__OVERRIDES__API_KEY_ENV` 指向 env 变量名(`config.py resolve_embedding_model_config:497 _resolve_secret(api_key, api_key_env)`)。
     omn 主线不设此(走 `_default_embedding_api_key` 回退)。

---

## 8. 公网端口决策: fastapi 直监听 7860

HF Space 硬约束。`uvicorn.run(app, host=0.0.0.0, port=7860)`(非官方 8000)。
README.md frontmatter `sdk: docker` `app_port: 7860`。

---

## 9. 永续路径决策: 不采 hermes GHCR-base+Bucket,直接进镜像

### 决策
honcho 是外部 clone 仓(非 nexus 自有),低频变,逻辑层极薄(launcher.py + Dockerfile + README+env 在 <250 行)
→ Bucket 永续路(honcho 逻辑层高频变更+多 Space 共享 base 才值得)对她过度工程。
build 期 `git clone` 上游 tag,升级改 tag 重 build 即可。

### 对比 hermes
hermes 走 GHCR-base+Bucket 因逻辑层高频变更+多 Space 共享 base。
honcho 单 Space 独享,外部仓低频变,Bucket 收益 < 工程成本。不采。

---

## 10. build 风险评估与实测结果(2026-08-03)

### 风险点
- 重依赖 lancedb(`sys_platform != "darwin" or platform_machine != "x86_64"` 条件依赖,linux amd64 装)
  + pyarrow + scikit-learn + turbopuffer:
  `uv sync --frozen` 无 buildkit cache mount 期可能慢/源码编译。
  → 本地 build 验暴露真实风险(HF CPU-basic 资源更受限,本地验完评估 HF 部署可行性)。

### 实测结果(build 段,无需凭据)
docker build -t honcho-hf-local . → **exit 0**
- 镜像 ~3.91GB(重依赖含 wheel 体积合理)
- 终态: `#17 naming to docker.io/library/honcho-hf-local:latest done`
- 重依赖全 wheel 直装,无源码编译,无超时

### build --from ARG 插值 bug 及修
- 第一次 build 失败: `variable expansion is not supported for --from`
- 原因: classic builder(默认驱动)不支持 `COPY --from=image:${ARG}` ARG 插值
- 修: `--from=ghcr.io/astral-sh/uv:0.9.24` 用字面版本号(非 ARG 插值)
- 第二次 build exit 0 ✓
- 详见 `build-verify-2026-08-03.md`

### HF build 风险评估结论
本地 build 通(3.91GB 合理,重依赖无超时/编译失败)→ HF CPU-basic 资源更受限,
HF 实际 build 时间待 HF 部署时观测。本地验完证明依赖装通可行,HF build 风险降。

---

## 11. 红线遵守

- ✅ 不动 nexus 现有 4 组件架构(hermes 换装另成 plan)
- ✅ 不动 `spaces/hermes/` 任何文件(只读参考模式)
- ✅ 不碰 `sonoke/h` Space README 禁区(建新 Space,非动现役)
- ✅ 不擅自 git push(GitHub + HF)— plan 列步,执行须用户显式同意
- ✅ 不硬编码敏感信息(全走 HF Secrets/env)
- ✅ local 测试用 docker build/run(不动 host pip/不 --break-system-packages)
- ✅ omniroute = 第 5 HF Space `nonoke/omn`,base_url `https://nonoke-omn.hf.space/v1`,OpenAI 兼容

---

## 12. 待办(凭据侧)

build 段(无需凭据)100% 通。run 段需凭据:
1. 用户建 Neon 项目 `sonoke-honcho`,取 pooled DSN → `DB_CONNECTION_URI`
2. 用户提供 omn Bearer key → `LLM_OPENAI_API_KEY`
3. 一把 docker run(H-7.1)验:
   - boot log "running alembic migrations against Neon..." → "migrations OK"
   - "deriver task created on api event loop" + "Starting queue manager"(无 crash)
   - `uvicorn Uvicorn running on http://0.0.0.0:7860`
   - `curl /health` 期 {"status":"ok"}
4. R1 闸门(H-6)容器内 curl omn /v1/embeddings:
   - 列模型确认 text-embedding-3-small 在列
   - POST embeddings 确认返回 1536 维向量
   - 通过 → omn 主线;不通过 → 降级独立供应商(见 §7)
5. HF Docker Space 部署(H-7.3,用户建新 Space + 显式同意 push 三件)
6. (后续,另 plan)hermes memory setup honcho 对接

---

## 13. 文件清单(`spaces/honcho/`,本地未 push)

| 文件 | 用途 |
|---|---|
| `Dockerfile` | HF build 入口,FROM python:3.11-slim-bookworm + git clone honcho v3.0.11 + uv sync + COPY launcher |
| `launcher.py` | shim 核心: provision_db(init_db+retry)→ create_task deriver → orig_lifespan yield + finally cancel deriver + uvicorn 7860 |
| `README.md` | HF Space frontmatter sdk:docker app_port:7860 + 端到端验步 + R1 闸门 + Secrets 摘 + 升级 honcho 改 ARG 重核 |
| `.env.example` | 全 env 名实证(每 feature __OVERRIDES__BASE_URL 指 omn + EMBEDDING_VECTOR_DIMENSIONS=1536 fail-closed + 降级路) |
| `build-verify-2026-08-03.md` | 本地 build exit 0 + 镜像内 shim 全假设实证记录 |
| `DESIGN-RATIONALE.md` | 本文件,设计论证与源码实证汇总 |

只读参考源(不入仓):
- `/tmp/honcho_check/honcho/src/main.py`(模块级 app+lifespan,shim import 目标)
- `/tmp/honcho_check/honcho/src/deriver/queue_manager.py`(L1132 main() 纯 coroutine,create_task 目标)
- `/tmp/honcho_check/honcho/src/config.py`(env 前缀 + resolve_model_config + _fill_defaults + _default_embedding_api_key 实证)
- `/tmp/honcho_check/honcho/src/startup/embedding_validator.py`(validate_embedding_schema fail-closed)
- `/tmp/honcho_check/honcho/src/db.py`(init_db + QueuePool + CREATE EXTENSION vector)

---

## §NIM NVIDIA NIM 免费模型替代占位(2026-08-03)

占位 `DERIVER/...MODEL=gpt-5.4-mini` + `EMBEDDING_MODEL_CONFIG__MODEL=text-embedding-3-small` 换 NVIDIA NIM(integrate.api.nvidia.com)免费模型。omniroute 上游 = NIM key pool(`NIM_KEYS` env,逗号分隔,模型名透传到 NIM,见 `docs/new/Nexus集群永续架构最强模板.md:1398`)。故 **model id 用 NIM 原生名(含 provider 前缀),base_url 仍指 omn 不动**。

### N1 honcho openai 后端 NIM 兼容核证(源码级)

- `src/llm/backends/openai.py:73 _uses_max_completion_tokens`:仅 `gpt-5*`/`o-series` 用 `max_completion_tokens`,其余所有(NIM llama/qwen/mistral)用 `max_tokens`——**NIM 兼容**。
- `openai.py:209-211` tiktoken `encoding_for_model` 失败回退 `cl100k_base` 不崩——NIM 模型名未注册 tiktoken 走 fallback。
- `src/config.py:30 _EMBEDDING_KNOWN_REJECTING_MODELS = frozenset({"text-embedding-ada-002"})`:**NIM 嵌入模型全不在 reject 列表**。
- `src/config.py:773 resolve_send_dimensions`:模型在 reject 命名集合 → `False`;否则若 `EMBEDDING.VECTOR_DIMENSIONS` 显式设 → `True`。`.env.example` 设了 `EMBEDDING_VECTOR_DIMENSIONS=1024` → `send_dimensions=True` → honcho 传 `dimensions=1024` 给 NIM embeddings API。**若 NIM embedding API 不接受 `dimensions` 参数 → 400**(R1 实测必须确认)。
- `src/embedding_client.py:172-175,264` send_dimensions 控制 dimensions 参数传不传;`L218-222 _validate_embedding_dimensions` 校验返回维度==EMBEDDING.VECTOR_DIMENSIONS,fail-closed。

### N2 102 模型清单(integrate.api.nvidia.com/v1/models 已抓全,/tmp/nim_models.json)

无认证 `/v1/models` 返 102 模型。`z-ai/glm-5.2` 在列(omn 透传实证,nexus hermes 即经此出)。摘要分类:

**文本生成候选(NIM 免费档,经 omn 透传)**:
- `nvidia/llama-3.1-nemotron-nano-8b-v1`(8B,小快省 — N3 初选,后推翻:中文不支持 + HF CPU-basic 假设推翻,降备2)
- `nvidia/llama-3.1-nemotron-70b-instruct` (70B 大,质量高 CPU-basic 慢慎)
- `meta/llama-3.1-8b-instruct`、`meta/llama-3.3-70b-instruct`
- `qwen/qwen2.5-7b-instruct`、`qwen/qwen2.5-coder-32b-instruct`
- `mistralai/mistral-nemo-12b-instruct`
- `deepseek-ai/deepseek-r1`、`deepseek-ai/deepseek-r1-distill-qwen-1.5b`
- `z-ai/glm-5.2`(omn 透实证在列)

**嵌入候选(12 个)+ 维度实证(HF config.json hidden_size 代理;gated model R1 实测)**:

| model id | dim | max tokens | 状态 | 来源 |
|---|---|---|---|---|
| `baai/bge-m3` | 1024 | 8192 | 候选(N3 初选后推翻:R1 实测 dimensions 截断 400 拒) | HF config.json(XLM-Roberta,多语言 dense+sparse+colbert,public ungated) |
| `snowflake/arctic-embed-l` | 1024 | 512 | 备选(短上下文) | HF config.json(BERT,public) |
| `nvidia/llama-nemotron-embed-1b-v2` | 2048 | 131072 | 备选(长上下文) | HF config.json(LlamaBidirectional,public ungated) |
| `nvidia/nv-embed-v1` | 4096 | ? | R1 实测 | HF gated 401 无法读 |
| `nvidia/nv-embedqa-e5-v5` | 1024 | ? | **嵌入选定**(N3, R1 实测 omn /v1/embeddings 200 维 1024 中文通) | HF gated 401;omn 透传实测定 |
| `nvidia/embed-qa-4` | ? | ? | R1 实测 | HF gated 401 |
| `nvidia/llama-3.2-nv-embedqa-1b-v1` | ? | ? | R1 实测 | 新版 |
| `nvidia/llama-3.2-nemoretriever-1b-vlm-embed-v1` | ? | ? | R1 实测 | VLM 多模态 |
| `nvidia/llama-nemotron-embed-vl-1b-v2` | ? | ? | R1 实测 | VLM |
| `nvidia/nemotron-3-embed-1b` | ? | ? | R1 实测 | 新 |
| `nvidia/nv-embedcode-7b-v1` | ? | ? | R1 实测 | 代码嵌入 |
| `snowflake/arctic-embed-l` | 1024 | 512 | (重复确认) | 已列 |

### N3 选型落定(2026-08-03 R1 实测 + 用户主选重判决)

**文本生成全 7 feature(DERIVER/SUMMARY/DIALECTIC minimal/DREAM deduction+induction)主选**: `deepseek-ai/deepseek-v4-flash`(284B/13B active MoE)。用户拍主选重判(续记忆 `nexus-honcho-model-selection-2026-08-03` 未决项1"回家再议")。`.env.example` + `run-local.sh` 全 7 feature 已落 v4-flash。

选定理由(记忆核证源):
- **中文母语**:Chinese-SimpleQA 评证(DeepSeek 中国实验室);honcho=用户记忆 provider,hermes 用户场景中文为主故 V4 Flash 胜 30b 中文弱 + 8b 中文不支持。
- **tool calling 原生无 thinking 限制**:arxiv DeepSeek-V4 技报 + DeepSeek API docs agent/codex 适配实证(arxiv 2606.19348)。**故不需 `THINKING_EFFORT` 配置**——30b nemotron-3 tool calling 须 detailed thinking off(NVIDIA NIM docs 1.10.0 function-calling.html),V4 Flash 无此限,降配置复杂度。
- **structured JSON 原生**:DeepSeek API docs change-log V4-Flash-0731 三 reason mode + Codex 适配 + Tools;build.nvidia.com model card structured/tool/agent 实证。
- **活跃无弃用风险**:build.nvidia.com 4-23 上线常青 2M API calls/30d;2M API calls/30d;v3 系入 Alibaba 2026-10-10 弃用表但 **v4 系未入**,旁证活跃。
- 13B active 省 + 快(p50 TTFT 394ms,184 tok/s,284B MoE)。
- omn NIM id `deepseek-ai/deepseek-v4-flash`(继 [[nexus-hermes-v9-sonoke-deploy]] glm-5.2 经 omn 通实证后第二 deepseek 直名透传)。

**档位**:
- 主 `deepseek-ai/deepseek-v4-flash`(.env/run-local 全 7 feature 落)。
- 备1 `nvidia/nemotron-3-nano-30b-a3b`(30B/3.5B active,省 active;tool calling 须 `THINKING_EFFORT` 显设 detailed thinking off,V4 Flash 撞限速降级时切此需补配)。
- 备2 `nvidia/llama-3.1-nemotron-nano-8b-v1`(8B 纯英文限速兜底 ~40 RPM,中文不支持致命)。

前序 § 此处初选 8b 基于「HF CPU-basic 推理 latency 友」假设已**推翻**(见 .env.example 顶注:honcho 经 omn 远程调 NIM,GPU 推理在 integrate.api.nvidia.com 非 HF CPU 本地跑,约束 = omn 面板激活 + ~40 RPM + API 超时非 HF RAM/CPU)。

**嵌入(R1 实测定 seule)**: `nvidia/nv-embedqa-e5-v5`,dim=1024。**前序 § 初选 `baai/bge-m3` 已推翻** — R1 实测(见 N4):omn `/v1/embeddings` 对 bge-m3 + `dimensions` 截断参数 HTTP 400 拒绝(non-matryoshka 固定维度不支持截断);选 embedqa 因 omn 透返原生 1024 Chinese 通(经 omn /v1/embeddings HTTP 200 实证,见 .env.example:56,L88-103)。

**`EMBEDDING_VECTOR_DIMENSIONS=1024`**(原 1536=text-embedding-3-small,改 embedqa 原生 1024)+ `EMBEDDING_MODEL_CONFIG__DIMENSIONS_MODE=never`(强制不传 dimensions,embedqa 非 matryoshka 拒截断)。launcher `_configure_embeddings` ALTER 列 1536→1024 匹配。

### N4 R1 闸门 `dimensions` 参数风险(已实测闭环)

前序 § 此处预置 bge-m3 + dimensions 传 1024 作 R1 待测项。**R1 实测结果**(2026-08-03):

- `nv-embedqa-e5-v5` 经 omn `/v1/embeddings` 返 HTTP **200** + 1024 维向量,中文通 → **选定**。
- bge-m3 + `dimensions` 截断 → HTTP **400** `KeyError: data`(非 matryoshka 拒截断)→ bge-m3 路废。
- `nv-embedqa-e5-v5` + `dimensions` 截断 → 同 400 拒(固定 1024 非 matryoshka)→ **必须 `DIMENSIONS_MODE=never`** 强制不传 dimensions,honcho 取 omn 原生 1024 与 `EMBEDDING_VECTOR_DIMENSIONS=1024` 校验过门(`embedding_validator.py:61` fail-closed 过门)。

`resolve_send_dimensions`(config.py:773):模型不在 `_EMBEDDING_KNOWN_REJECTING_MODELS`(config.py:30,仅 `text-embedding-ada-002`)+ VECTOR_DIMENSIONS 显设 → auto 路默认 send_dimensions=True 传 dimensions=N。embedqa 不在 reject 故 auto 路会传 dimensions 致 400——**显设 `DIMENSIONS_MODE=never` 覆盖 auto 路**解(零改源,resolve_send_dimensions never→send_dimensions=False)。注:`_EMBEDDING_KNOWN_REJECTING_MODELS` 仅含 ada-002,加 embedqa 需改源(违背 shim 零 diff),故走 env 覆盖非改 reject 集。

### N5 实测命令(R1 已跑通,留作复检)

```bash
# 1) 列 omn 透传模型(确认 nv-embedqa-e5-v5 + 30b/v4flash 在列)
curl -sS https://nonoke-omn.hf.space/v1/models -H "Authorization: Bearer $OMN_KEY" | jq '.data[].id' | grep -E 'embedqa-e5-v5|nemotron-3-nano-30b|v4-flash'
# 2) 嵌入测(核心:确认 embedqa 1024 返 + dimensions never 不传)
curl -sS https://nonoke-omn.hf.space/v1/embeddings \
  -H "Authorization: Bearer $OMN_KEY" -H "Content-Type: application/json" \
  -d '{"model":"nvidia/nv-embedqa-e5-v5","input":"hello"}' | jq '.data[0].embedding | length'
# 期 1024(dimensions 不传经 DIMENSIONS_MODE=never;若传 dimensions 则 400 KeyError data)
# 3) 文本生成测(v4-flash 主选)
curl -sS https://nonoke-omn.hf.space/v1/chat/completions \
  -H "Authorization: Bearer $OMN_KEY" -H "Content-Type: application/json" \
  -d '{"model":"deepseek-ai/deepseek-v4-flash","max_tokens":32,"messages":[{"role":"user","content":"ping"}]}'
```

---

## §N6 H-7.1 首跑实证 + 双根因 diag(2026-08-03)

H-7.1 首跑 docker build+run 实证。镜像已建 honcho-hf-local,docker run 启动。

### 实证通过项(shim 全核坐实)

1. **shim lifespan 替换实证通过**:`importing honcho app` → `lifespan injected → launcher's lifespan(provision+deriver+orig)`。`app.router.lifespan_context` 运行时替换工作,真进 launcher 版(非镜像内静态 `is not` 验,此是真 lifespan 执行)
2. **tenacity 重试实证通过**:`attempt 1/3` → `attempt 2/3` → `attempt 3/3`,launcher `_provision_db_with_retry` 3 次固定 backoff 1s 工作正常
3. **fail-closed 拒裸奔实证通过**:`RuntimeError: init_db failed after 3 attempts` + `Application startup failed. Exiting`。DB 不通即退,不降级空跑
4. **uvicorn 7860 进程起**:`Started server process [1]` + `Waiting for application startup`

shim + Dockerfile + 重试 + fail-closed 全实证正确,本侧代码无 bug。

### 失败根因(双,均 Neon 侧非本侧代码)

**根因 1:DSN 字面 `DB` 占位未换真值**

日志最关键一行(首次 attempt):
```
hostaddr: '54.92.227.85': connection failed: ERROR: database "DB" does not exist
```
Neon 连上了(IP 54.92.227.85 通),但 `database "DB" does not exist`——DSN 里数据库名仍是占位字面 `DB`。`.env.example` 写 `USER:PASS@.../DB?sslmode=require`,docker run 注入 DB_CONNECTION_URI 时用了占位未改真数据库名。

**解**:docker run 用 Neon 真 DSN(真 USER:PASS + 真 data库名如 `neondb`/`sonoke-honcho`/`main`)。需用户给 Neon 控制台真值。

**根因 2:`DB_CONNECT_TIMEOUT_SECONDS` 默认 2s < Neon 冷启 wake 5-30s**

源码实证:
- `src/db.py:19-24 connect_args`:`"connect_timeout": settings.DB.CONNECT_TIMEOUT_SECONDS`
- `src/config.py:689-690 DBSettings.CONNECT_TIMEOUT_SECONDS: Annotated[int, Field(default=2, gt=0, le=60)] = 2`

**每连接尝试只等 2 秒**。Neon scale-to-zero 免费档 idle 后冷启 wake 一般 5-30s,2s 必然不够 → `connection timeout expired`(日志 3.215.191.145/3.227.144.24/54.92.227.85 多 IP 轮询各 timeout expired)。

**解**:`DB_CONNECT_TIMEOUT_SECONDS=30` env(honcho 自带,调 30s 给 Neon cold start wake 窗口)。上限 60(Field le=60)。**不需改源码,不需 DSN query param**——honcho 自身 connect_args 走此 env。

### IPv6 `Network is unreachable` 忽略

日志 IPv6 hostaddr(2600:1f10:...)报 Network is unreachable——本机/容器无 IPv6 出站,psycopg 自动 fallback IPv4。属正常,非问题。

### 下次 run 完整 env 集合(H-7.1 重跑)

```bash
docker run --rm -p 7860:7860 \
  -e DB_CONNECTION_URI="postgresql+psycopg://<真USER>:<真PASS>@ep-wild-field-auxqpshl-pooler.c-10.us-east-1.aws.neon.tech/<真DB名>?sslmode=require" \
  -e DB_CONNECT_TIMEOUT_SECONDS=30 \
  -e LLM_OPENAI_API_KEY="<omn bearer>" \
  -e AUTH_USE_AUTH=true -e AUTH_JWT_SECRET="test-secret-xxxx" \
  -e DERIVER_MODEL_CONFIG__TRANSPORT=openai \
  -e DERIVER_MODEL_CONFIG__MODEL=deepseek-ai/deepseek-v4-flash \
  -e DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://nonoke-omn.hf.space/v1 \
  -e EMBEDDING_MODEL_CONFIG__TRANSPORT=openai \
  -e EMBEDDING_MODEL_CONFIG__MODEL=nvidia/nv-embedqa-e5-v5 \
  -e EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=https://nonoke-omn.hf.space/v1 \
  -e EMBEDDING_MODEL_CONFIG__DIMENSIONS_MODE=never \
  -e EMBEDDING_VECTOR_DIMENSIONS=1024 \
  -e CACHE_ENABLED=false -e METRICS_ENABLED=false \
  honcho-hf-local
# 期(migrations OK 后):
#   "migrations OK" → "deriver task created on api event loop" →
#   uvicorn "Uvicorn running on http://0.0.0.0:7860"
# curl -sS http://localhost:7860/health  期 {"status":"ok"}
```

注意 host 日志显示 `ep-wild-field-auxqpshl-pooler`(非 .env.example 占位 ep-xxx),用户 Neon 真 endpoint 是此值,DSN 须用真值。
