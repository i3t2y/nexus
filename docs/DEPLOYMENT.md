# 部署手册

> 凭证就位后按此顺序落地。模板阶段无需执行。

## 前置：账号与套餐确认

| 项 | 要求 | 风险点 |
|----|------|--------|
| HF 账号 | ZeroGPU 免费 2 个 Gradio Space；Docker Space 需 PRO/Team | 3 个 Docker Space(langgraph/claude/codex)创建前确认套餐 |
| Cloudflare 账号 | R2 免费层 10GB + 100万 A类操作/月 | 超量计费，配 Lifecycle 清理 |
| Supabase 账号 | 免费 500MB DB + 2 个项目 + 1 周不活跃自动暂停（仅停 compute） | 用 service_role 做服务端，anon 仅客户端；防暂停靠 keepalive.py 周期写表 |
| GitHub 账号 | 私有库免费 | nexus 库私有 |

## 步骤 1：Cloudflare R2

1. 登录 Cloudflare → R2 → 创建 buckets（建议每组件一桶）：
   - `nexus-checkpoints`（LangGraph blob）
   - `nexus-skills`（Skills 备份）
   - `nexus-vectors`（向量文件）
   - `nexus-artifacts`（大产物）
2. R2 → Manage R2 API Tokens → 生成 token，记录：
   - `R2_ACCESS_KEY_ID`
   - `R2_SECRET_ACCESS_KEY`
   - `R2_ENDPOINT`（形如 `https://<account_id>.r2.cloudflarestorage.com`）
3. 每桶设 Lifecycle Rule：N 天后清理临时前缀（如 `tmp/`）。
4. 公开读：用 Presigned URL，不开 Public Access。

## 步骤 2：Supabase Postgres

1. 建项目，记录：
   - `SUPABASE_URL`（形如 `https://<id>.supabase.co`）
   - `SUPABASE_ANON_KEY`
   - `SUPABASE_SERVICE_ROLE_KEY`（仅服务端，不入客户端）
   - **直连 connection string**（Settings → Database → Connection string → URI）
     - 形如 `postgresql://postgres.<ref>:<pwd>@aws-0-<region>.pooler.supabase.com:6543/postgres`
     - LangGraph `AsyncPostgresSaver` 用这个。**走 6543 transaction pooler**，勿用 Session pooler 5432（连接数受限、不横向扩）。
     - `from_conn_string` 硬编码 `prepare_threshold=0`，6543 safe，无需额外兜底。
2. SQL Editor 执行 `sql/00_schema.sql` 建表。
3. 启用 pgvector（如需向量搜索）：`sql/01_pgvector.sql`。
4. **保活注意**：免费档 1 周不活跃自动暂停（仅停 compute，数据不丢可恢复）。`keepalive.py` 每轮写 `space_health` 表即刷活动；暂停若仍发生，去 Dashboard restore。

## 步骤 2.5：构建前同步共享库

各 Space 的 Docker build context 是 Space 目录本身；根 `libs/` 需先复制进每个 Space 才能被 Dockerfile `COPY libs ./libs`。

```bash
bash scripts/sync-spaces.sh   # 把 libs/ 复制进 spaces/*/libs/
```

- **每次改根 `libs/` 后、每条 `git push` 前**必须重跑此脚本，否则 Space 跑的是旧库。
- 提交时 `spaces/*/libs/` 为同步产物，可提交（HF 直接读 repo 无构建后同步步骤）。

## 步骤 3：部署 Hermes（主控）

1. HF → New Space → 建议 Gradio SDK（ZeroGPU 免费层可跑）或 Docker；命名 `hermes`，私有。
2. Settings → Secrets 加：
   ```
   R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
   NEXUS_API_KEY  HF_TOKEN
   LANGGRAPH_URL  CLAUDE_URL  CODEX_URL
   GATEWAY_URL  (Worker 上线后)
   ```
   生产全 Space `NEXUS_AUTH_MODE` 留空（fail-closed）；本地调试设 `dev` 免鉴权。
3. push `spaces/hermes/` 内容（已含 README frontmatter）。
4. 验证：访问 Space → POST `/run` 返回 task_id；查 `task_logs` 有记录。

## 步骤 4：部署下游 Space

依次 langgraph / claude / codex，每步：
1. New Space（Docker SDK，私有）。
2. Secrets 同 Hermes 的 R2/Supabase 一套 + `NEXUS_API_KEY` + `HF_TOKEN`（langgraph 还需 `SUPABASE_DB_URI`；claude 需 `ANTHROPIC_API_KEY`/`CLAUDE_MODEL`；codex 需 `OPENAI_API_KEY`/`OPENAI_BASE_URL`/`CODEX_MODEL`）。
3. push 对应 `spaces/<name>/` 目录。
4. 健康：GET `/health` 200（注意 build 前必须先跑 `sync-spaces.sh`，否则 `from storage import` 失败）。

## 步骤 5：Worker 网关

1. `cd workers/gateway && npm install`。
2. Worker Secret 注入：
   - `npx wrangler secret put NEXUS_API_KEY`（与各 Space 同一把）
   - `npx wrangler secret put HF_TOKEN`（下游若为私有 Space，HF 层访问必需；公开 Space 可跳）
3. 编辑 `wrangler.toml` 的 `SPACE_OWNER`（HF 用户名），按需取消注释 `LANGGRAPH_URL`/`CLAUDE_URL`/`CODEX_URL` 显式覆盖。
4. `npx wrangler deploy`，记录 `GATEWAY_URL`。
5. 更新 Hermes 的 `GATEWAY_URL` Secret。

鉴权链路：Worker 入站用 `Authorization: Bearer NEXUS_API_KEY`（网关层无 HF 冲突）；出站到下游 Space 改 `X-Nexus-Key: Bearer NEXUS_API_KEY` + `Authorization: Bearer HF_TOKEN`（HF 层）。

## 步骤 6：端到端

直接调 hermes（私有 Space，需 HF 层 + app 层双鉴权）：
```
curl -X POST <hermes_url>/run \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "X-Nexus-Key: Bearer $NEXUS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"prompt":"测试：让 langgraph 做一个三步规划","force_space":"langgraph"}'
```

或经 Worker 网关（入站用 `Authorization: Bearer NEXUS_API_KEY`，无 HF 冲突）：
```
curl -X POST <gateway_url>/route \
  -H "Authorization: Bearer $NEXUS_API_KEY" \
  -H "Content-Type: application/json" \
  -d '{"space":"claude","path":"/run","body":{"thread_id":"t1","prompt":"hi"}}'
```

异步 + 幂等（防重复扣费）：
```
curl -X POST <hermes_url>/enqueue \
  -H "Authorization: Bearer $HF_TOKEN" \
  -H "X-Nexus-Key: Bearer $NEXUS_API_KEY" \
  -H "Idempotency-Key: order-12345" \
  -d '{"prompt":"批量审查 5 个 pull request"}'
# 轮询消费 + 查状态：
curl -X POST <hermes_url>/dequeue -H "X-Nexus-Key: Bearer $NEXUS_API_KEY" -H "Authorization: Bearer $HF_TOKEN"
curl <hermes_url>/task/<task_id> -H "X-Nexus-Key: Bearer $NEXUS_API_KEY" -H "Authorization: Bearer $HF_TOKEN"
```

期望：返回 task_id，`task_logs` 全链路 done，R2/Supabase 有产物。

## 保活（可选）

- Hermes 常驻：付费或用外部 cron 周期 ping。
- 下游 Space：Worker keep-alive 探测 `/health`，唤醒休眠实例。

## 回滚

- Space 出错 → Settings → Restart；代码回滚 `git push -f` 到上个 commit。
- 表结构变更 → Supabase 先备份 → 改 SQL → 跑 `02_*.sql` 增量脚本。
