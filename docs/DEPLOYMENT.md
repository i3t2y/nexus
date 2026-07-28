# 部署手册

> 凭证就位后按此顺序落地。模板阶段无需执行。

## 前置：账号与套餐确认

| 项 | 要求 | 风险点 |
|----|------|--------|
| HF 账号 | ZeroGPU 免费 2 个 Gradio Space；Docker Space 需 PRO/Team | 3 个 Docker Space(langgraph/claude/codex)创建前确认套餐 |
| Cloudflare 账号 | R2 免费层 10GB + 100万 A类操作/月 | 超量计费，配 Lifecycle 清理 |
| Supabase 账号 | 免费 500MB DB + 2 个项目 + 1 周不活跃自动暂停（仅停 compute） | 用 service_role 做服务端，anon 仅客户端；防暂停靠 keepalive.py 周期写表 |
| GitHub 账号 | 私有库免费 | nexus 库私有 |
| GitHub | GHCR 推 nexus-base 镜像需 account;repo Actions 权限 Read and write(推送 packages)| 权限不足则 base workflow 失败,各 Space Dockerfile FROM 拉 base 失败 |

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
bash scripts/sync-spaces.sh          # 把 libs/ 复制进 spaces/*/libs/
bash scripts/sync-spaces.sh --check  # 仅校验一致性（不写），不一致退出 1
```

- **每次改根 `libs/` 后、每条 `git push` 前**必须重跑同步脚本，否则 Space 跑的是旧库。
- CI 闸门：`.github/workflows/sync-check.yml` 在 push 触动 `libs/` 或 `spaces/*/libs/` 时跑 `--check` + py_compile + tsc，挡旧库/语法错进 HF。
- 提交时 `spaces/*/libs/` 为同步产物，可提交（HF 直接读 repo 无构建后同步步骤）。

> 注：以上同步仅适用 3 个非 hermes 的下游 Space(hermes 除外:其逻辑层走 Bucket,见步骤 2.7 与 sync-logic-bucket.sh)。

## 步骤 2.6：构建 nexus-base 镜像并推 GHCR(hermes 永续改造前置,必做)

hermes 及后续切 Bucket 的 Space 的 Docker build 引用 `ghcr.io/<owner>/nexus-base:stable` 作为 base。**HF push 前镜像必须在 :stable 存在**,否则 HF build 拉不到 base 失败。

1. GitHub Create PAT(classic)勾 `write:packages`(或用 GITHUB_TOKEN,见 docker-base.yml workflow)。本地 `docker login ghcr.io -u <owner> -p <PAT>`。
2. 本地 build:
   ```bash
   docker build -t ghcr.io/<owner>/nexus-base:stable -f docker/nexus-base.Dockerfile docker/
   # 同时打版本标签作回退锚点
   docker tag ghcr.io/<owner>/nexus-base:stable ghcr.io/<owner>/nexus-base:v0.1
   docker push ghcr.io/<owner>/nexus-base:stable
   docker push ghcr.io/<owner>/nexus-base:v0.1
   ```
3. GitHub repo → Packages → nexus-base → Package settings 设 Public(否则 HF build 拉 GHCR 需配 secret+docker login,复杂,建议公开 Nexus base 无敏感代码)。
4. 或直接跑 `.github/workflows/docker-base.yml`(workflow_dispatch 手动触发),自动 build+push :stable(github.repository_owner 作 owner,GITHUB_TOKEN packages:write;需 repo Settings→Actions→Workflow permissions=Read and write)。
5. 验证:`docker pull ghcr.io/<owner>/nexus-base:stable` 成功(本地或他机)。

## 步骤 2.7：备份恢复（R2 → Supabase 反向闭环，事故后用）

`persist_to_r2.py` 周期把 Supabase 业务表快照到 R2（含 sha256 + R2 manifest）；`restore_from_r2.py` 做反向恢复：

```bash
# 只读：看 R2 内各表最新快照的 sha256/size/行数
python spaces/hermes/scripts/restore_from_r2.py --list
# 仅校验完整性（复算 sha256 比对 backup_snapshots 登记值，不写回）
python spaces/hermes/scripts/restore_from_r2.py --table agent_states --verify-only
# 恢复单表（service_role upsert on_conflict 覆盖，幂等可重跑）
python spaces/hermes/scripts/restore_from_r2.py --table agent_states
# 恢复全部业务表
python spaces/hermes/scripts/restore_from_r2.py --all
```

- **校验门**：restore 先复算 R2 对象 sha256 比对 `backup_snapshots.sha256`，不符即拒绝写回（防静默损坏覆盖好数据）。
- **安全域**：仅 `agent_states`/`long_memory`/`skills_index` 走 upsert 全量覆盖；`task_logs`/`space_health` 等代理键表恢复仅校验通过不写（避免自增主键冲突）。
- **空快照保护**：空快照跳过写回，防把表整体清空。

## 步骤 3：部署 Hermes（主控，永续改造首切）

**前提**：步骤 2.6 nexus-base 已推 GHCR :stable。

1. HF 建 Storage Bucket `nexus-logic`（私有，rw；`hf buckets create nexus-logic --private`）。
2. HF Space Settings:
   - Storage/Volume 配置：挂 Bucket `nexus-logic` 到 `/data` rw（仅 runtime，build 期不可见）。
   - Secrets 加（同前）：
     ```
     R2_ENDPOINT / R2_ACCESS_KEY_ID / R2_SECRET_ACCESS_KEY
     SUPABASE_URL / SUPABASE_SERVICE_ROLE_KEY
     NEXUS_API_KEY  HF_TOKEN  HF_OWNER
     LANGGRAPH_URL  CLAUDE_URL  CODEX_URL
     GATEWAY_URL  (Worker 上线后)
     ```
     生产全 Space `NEXUS_AUTH_MODE` 留空（fail-closed）。
3. **本地推逻辑进 Bucket**（用户手动，不涉 HF push）：
   ```bash
   HF_TOKEN=... HF_OWNER=<hf-name> bash scripts/sync-logic-bucket.sh
   # 验 Bucket UI 三目录 app/scripts/libs 在
   ```
4. Settings **Restart**（旧镜像验挂载通路）；start.sh wait-for-mount 见 `/data/app/main.py`（旧镜像 PYTHONPATH 不指 /data，报 import 错属正常，挂载已通即可）。
5. **git push HF repo**（用户手动，那 1 次关键 rebuild 过付费墙窗口）。ARG 默认值 ghcr..:stable 兜底，HF 拉 base 镜像成功。
6. rebuild 出新镜像（无 COPY 逻辑、依赖在 base、PYTHONPATH=/data/libs）、首启 wait-for-mount → uvicorn `app.main:app --app-dir /data` import 成功。
7. 验证：GET `/health` 200、POST `/run` 返回 task_id、`task_logs` 有产、Dashboard 可见。

**不可颠倒顺序**：GHCR base 优先 → Bucket 逻辑 → Volume 配+Restart 验挂载 → push HF rebuild → 启动 import。先 rebuild 后推 Bucket=空挂载 import 死锁;HF push 前无 GHCR base=rebuild 拉 base 失败。

## 步骤 4：部署下游 Space

依次 langgraph / claude / codex，每步：
1. New Space（Docker SDK，私有）。
2. Secrets 同 Hermes 的 R2/Supabase 一套 + `NEXUS_API_KEY` + `HF_TOKEN`（langgraph 还需 `SUPABASE_DB_URI`；claude 需 `ANTHROPIC_API_KEY`/`CLAUDE_MODEL`；codex 需 `OPENAI_API_KEY`/`OPENAI_BASE_URL`/`CODEX_MODEL`）。
3. push 对应 `spaces/<name>/` 目录。
4. 健康：GET `/health` 200（注意 build 前必须先跑 `sync-spaces.sh`，否则 `from storage import` 失败）。

> 注：hermes 已走步骤 3 Bucket 模式除外;3 下游 Space 暂仍走 git 副本 build context,build 前必跑 sync-spaces.sh,后续可选切 Bucket 共用 nexus-base。

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
- **hermes 逻辑回退**: `sync-logic-bucket.sh` 推旧版逻辑覆盖 Bucket + Restart(不涉 git push HF);若需镜像回退=baseda 旧版,GHCR `docker push ghcr.io/<owner>/nexus-base:v0.1` 重新覆盖 :stable(留前版 :vN 标签作回退锚点)+ README 一字符 git push 重建。
- **Dockerfile 墓碑不动原则**:hermes Dockerfile 首切后永不改;ARG 默认值写死 :stable,回退走 GHCR 覆盖 :stable(不破墓碑)。
