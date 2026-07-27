# 部署手册

> 凭证就位后按此顺序落地。模板阶段无需执行。

## 前置：账号与套餐确认

| 项 | 要求 | 风险点 |
|----|------|--------|
| HF 账号 | ZeroGPU 免费 2 个 Gradio Space；Docker Space 需 PRO/Team | 3 个 Docker Space(langgraph/claude/codex)创建前确认套餐 |
| Cloudflare 账号 | R2 免费层 10GB + 100万 A类操作/月 | 超量计费，配 Lifecycle 清理 |
| Supabase 账号 | 免费 500MB DB + 2 个项目 | 用 service_role 做服务端，anon 仅客户端 |
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
     - LangGraph `AsyncPostgresSaver` 用这个，**不要**用 Session pooler 的 5432（async/连接复用冲突）。
2. SQL Editor 执行 `sql/00_schema.sql` 建表。
3. 启用 pgvector（如需向量搜索）：`sql/01_pgvector.sql`。

## 步骤 3：部署 Hermes（主控）

1. HF → New Space → 建议 Gradio SDK（ZeroGPU 免费层可跑）或 Docker；命名 `hermes`，私有。
2. Settings → Secrets 加：
   ```
   R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
   SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
   NEXUS_API_KEY  LANGGRAPH_URL  CLAUDE_URL  CODEX_URL
   GATEWAY_URL  (Worker 上线后)
   ```
3. push `spaces/hermes/` 内容（已含 README frontmatter）。
4. 验证：访问 Space → POST `/run` 返回 task_id；查 `task_logs` 有记录。

## 步骤 4：部署下游 Space

依次 langgraph / claude / codex，每步：
1. New Space（Docker SDK，私有）。
2. Secrets 同 Hermes 的 R2/Supabase 一套 + `NEXUS_API_KEY`。
3. push 对应 `spaces/<name>/` 目录。
4. 健康：GET `/health` 200。

## 步骤 5：Worker 网关

1. `npm create cloudflare@latest nexus-gateway`（Worker 模板）。
2. 把鉴权 key 设为 Worker Secret `NEXUS_API_KEY`，Space owner/URL 设为变量。
3. `wrangler deploy`，记录 `GATEWAY_URL`。
4. 更新 Hermes 的 `GATEWAY_URL` Secret。

## 步骤 6：端到端

```
curl -X POST <hermes_url>/run -H "Authorization: Bearer $NEXUS_API_KEY" \
  -d '{"prompt":"测试：让 langgraph 做一个三步规划"}'
```

期望：返回 task_id，`task_logs` 全链路 done，R2/Supabase 有产物。

## 保活（可选）

- Hermes 常驻：付费或用外部 cron 周期 ping。
- 下游 Space：Worker keep-alive 探测 `/health`，唤醒休眠实例。

## 回滚

- Space 出错 → Settings → Restart；代码回滚 `git push -f` 到上个 commit。
- 表结构变更 → Supabase 先备份 → 改 SQL → 跑 `02_*.sql` 增量脚本。
