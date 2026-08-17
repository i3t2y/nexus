<!-- Nexus Honcho 自建 HF Docker Space(2026-08-03)。
     user-centric memory provider 补层:给 hermes 接管用户记忆(叠加非替换,
     不动 nexus 现有 4 组件架构)。
     build 期 git clone honcho v3.0.11(plastic-labs/honcho,AGPL-3.0-only)+
     launcher.py shim 压 api+deriver 两逻辑进单 uvicorn 进程(HF 单进程硬约束)。
     真源/计划:plan H 段(`/home/laisi/.claude/plans/joyful-swimming-matsumoto.md` Honcho 自建方案)。 -->
---
title: Nexus Honcho
emoji: 🧠
colorFrom: blue
colorTo: green
sdk: docker
app_port: 7860
pinned: false
---

# Nexus Honcho

[Honcho](https://github.com/plastic-labs/honcho) v3.0.11(user-centric memory +
social cognition 平台)自建 HF Docker Space。给 nexus `hermes` Space 接管的用户
记忆补一层,叠加非替换,不动 nexus 现有 4 组件(hermes/claude-code/codex/langgraph)架构,
不碰 `sonoke/h` 禁区。

## 形态

- **minimal launcher shim**(不 fork honcho 源码):build 期 `git clone --branch v3.0.11`honcho
  到 `/app`,运行时 `app.router.lifespan_context` 替换 honcho 原生 lifespan。0 行 honcho 源码 diff,
  升级 honcho 仅改 `Dockerfile` ARG `HONCHO_TAG` 重 build。
- **api + deriver 两逻辑压单进程**(HF Space 单进程硬约束):api FastAPI 走 uvicorn,
  deriver worker(`src/deriver/queue_manager.py:main`)经 `asyncio.create_task` 同 event loop
  polling,不产新进程。
- **DB = Neon serverless Postgres + pgvector**(独立项目 `sonoke-honcho`,与 nexus Supabase 隔离):
  `init_db()` 首启自动 `CREATE EXTENSION vector` + `alembic upgrade head` 建 ~25 表 + 两 vector 列。
- **LLM/Embedding 走 omniroute**(第 5 HF Space `nonoke/omn`,OpenAI 兼容端点):
  `LLM_OPENAI_API_KEY` 单 key 源(openai transport 自动复用),每 feature base_url 显式
  `*_MODEL_CONFIG__OVERRIDES__BASE_URL` 指 omn。
- **关 cache/metrics/telemetry/sentry**:省 Redis 进程 + 省 9090 端口,zero-overhead。

## 验证(端到端)

### 本地 build + run
```bash
cd spaces/honcho
docker build -t honcho-hf-local .
docker run --rm -p 7860:7860 \
  -e DB_CONNECTION_URI="postgresql+psycopg://USER:PASS@ep-xxx-pooler.REGION.aws.neon.tech/DB?sslmode=require" \
  -e LLM_OPENAI_API_KEY="<omn bearer>" \
  -e AUTH_USE_AUTH=true -e AUTH_JWT_SECRET="<长随机>" \
  -e DERIVER_MODEL_CONFIG__TRANSPORT=openai \
  -e DERIVER_MODEL_CONFIG__OVERRIDES__BASE_URL=https://nonoke-omn.hf.space/v1 \
  -e EMBEDDING_MODEL_CONFIG__TRANSPORT=openai \
  -e EMBEDDING_MODEL_CONFIG__OVERRIDES__BASE_URL=https://nonoke-omn.hf.space/v1 \
  -e EMBEDDING_VECTOR_DIMENSIONS=1536 \
  honcho-hf-local
# 期 boot log: "running alembic migrations against Neon..." → "migrations OK"
#   → "deriver task created on api event loop" → uvicorn 0.0.0.0:7860
curl -sS http://localhost:7860/health   # 期 {"status":"ok"}
```

### 端到端 API 验(deploy 后)
```bash
# 1) 生成 admin JWT(需容器内 AUTH_JWT_SECRET env 在位)
docker run --rm -e AUTH_JWT_SECRET="<同德>" honcho-hf-local python scripts/generate_jwt.py --admin
# 2) 建 workspace
curl -X POST https://<owner>-honcho.hf.space/v3/workspaces \
  -H "Authorization: Bearer $JWT" -H "Content-Type: application/json" -d '{"name":"test-ws"}'
# 3) peer/session/message 触 deriver polling 派生 → GET /v3/sessions/{id}/messages
```

## R1 闸门(deploy 后第一实测)

验证 omniroute `/v1/embeddings` 端点 + 模型 + 维度匹配 `EMBEDDING_VECTOR_DIMENSIONS`:
```bash
curl -sS https://nonoke-omn.hf.space/v1/models -H "Authorization: Bearer $OMN_KEY" | jq '.data[].id' | grep -i embed
curl -sS https://nonoke-omn.hf.space/v1/embeddings \
  -H "Authorization: Bearer $OMN_KEY" -H "Content-Type: application/json" \
  -d '{"model":"text-embedding-3-small","input":"hello"}' | jq '.data[0].embedding | length'  # 期 1536
```
通过→走 omn 主线;不通过→降级独立 embedding 供应商(改 `EMBEDDING_MODEL_CONFIG__MODEL`/`__OVERRIDES__BASE_URL`/
`EMBEDDING_VECTOR_DIMENSIONS` + 加独立 `EMBEDDING_API_KEY`)。

## HF Secrets(最小集,见 `.env.example` 全列)

**起不来硬依赖**:`DB_CONNECTION_URI`(Neon DSN,必带 `sslmode=require`,用 pooled endpoint -pooler)、
`LLM_OPENAI_API_KEY`(omn Bearer)、`AUTH_USE_AUTH=true`、`AUTH_JWT_SECRET`。

**LLM routing**(每 feature base_url 指 omn):`DERIVER_*`/`SUMMARY_*`/
`DIALECTIC_LEVELS__minimal__*`/`DREAM_DEDUCTION_*`/`DREAM_INDUCTION_*` 各 `__MODEL_CONFIG__TRANSPORT=openai` +
`__MODEL_CONFIG__OVERRIDES__BASE_URL=https://nonoke-omn.hf.space/v1`。api key 复用 `LLM_OPENAI_API_KEY`(openai transport 自动回退)。

**Embedding**:`EMBEDDING_MODEL_CONFIG__*` 三键 + `EMBEDDING_VECTOR_DIMENSIONS=1536`。

**关省**:`CACHE_ENABLED=false`(default 已关,显设保险)、`METRICS_ENABLED=false`(同)、`TELEMETRY_ENABLED=false`、`SENTRY_ENABLED=false`。

## 红线遵守

- 不动 nexus 现有 4 组件架构(hermes 换装另成 plan)
- 不动 `spaces/hermes/` 任何文件(只读参考)
- 不碰 `sonoke/h` Space README 禁区(本 Space 新建,非动现役)
- 不擅自 git push(GitHub + HF)— 本仓 `spaces/honcho/` 文件本地写,push HF Space 须经用户显式同意
- 敏感信息全走 HF Secrets/env,不硬编码
- omniroute = 第 5 HF Space `nonoke/omn`,base_url `https://nonoke-omn.hf.space/v1`

## 升级 honcho

改 `Dockerfile` ARG `HONCHO_TAG`(如 `v3.0.12`)重 build。需重新核 shim 对齐新版源码
(`src/main.py` lifespan/`src/deriver/queue_manager.py:main`/`src/db.py:init_db`/`src/config.py` env 前缀),
避免换装漂移。pin tag 不 pin main(防 break)。
