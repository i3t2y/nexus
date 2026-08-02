# Hermes Space V9 HF 部署清单(本地验 V5-V8 全过后)

> 本地 K 形态代码侧 + base 镜像闸门 + V5-V8 验全过(见 commit `8432594`)。
> 此清单 = HF Space 真部署执行步骤(需真凭据,凭据不入 git,经 HF Space Secrets UI 注)。
> 凭据侧:V9 期用户填真实值(HF Space `sonoke/h` + bucket `sonoke/logic`,替旧虚拟 `i3t2y/hermes` 占位)。
> sync-logic-bucket 需 HF_TOKEN(有写 sonoke/logic 权)+ HF_OWNER=sonoke + NEXUS_LOGIC_BUCKET=logic 三 env。

## 前置:本地已完成(我执行)

- [x] commit `8432594`(K 形态主代码 + base 镜像改 + V5-V8 验)
- [x] `ghcr.io/i3t2y/nexus-base:stable` 本地 build + push GHCR(2.84GB,含 K-R6 sqlite 3.53.4 + K-R4 web_dist + messaging 子集)
- [x] git push 分支 `feat/hermes-coreswap-nousresearch` 到 GitHub
- [ ] HF Space `sonoke/h` Settings Sources 同步分支改指 `feat/hermes-coreswap-nousresearch`(K 形态在此分支非 main)+ README 改一字符触发 rebuild 拉 :stable(下步)

## 步骤 1:HF Space Secrets 注入(HF Space Settings → Variables and secrets)

HF Space `sonoke/h` → Settings → Repository secrets(New secret 逐条加,值不入 git):

### 必填(缺则 boot 崩或功能缺)

| Secret 名 | 值 | 必填原因 |
|---|---|---|
| `GLM_BASE_URL` | `https://nonoke-omn.hf.space/v1` | omniroute 第5 Space 入口;zai provider base_url override,**必带 /v1**(OpenAI client 拼 /chat/completions 落 omniroute /v1/chat/completions,缺 /v1 落 404)。env 等价于 config.yaml `model.base_url` |
| `GLM_API_KEY` | `<omniroute 真 Bearer key>` | omniroute 鉴权(= zai api_key,Bearer 头);hermes 调 glm-5.2 经此。**非 ANTHROPIC_API_KEY** — glm-5.2 走 hermes 原生 `zai` provider(OpenAI 协议),非 anthropic(anthropic base_url override 受白名单排拒 hf.space + 头错配 x-api-key vs Bearer) |
| `HERMES_MODEL` | `glm-5.2` | 原生仅 cron scheduler 读(cron/scheduler.py:3158)];交互/oneshot 默认模型靠 config.yaml `model.default`(本模板已设同值 glm-5.2)。glm 名含 → 静态路由(models.py:1278 glm→zai)→ provider=zai → 查 GLM_API_KEY → 缺则报 `No usable credentials found for provider 'zai'` |
| `API_SERVER_KEY` | `<≥16 字符随机串>` | api_server adapter 真触发器 + /v1/* Bearer 鉴权(同 key 双用) |
| `NEXUS_AUTH_MODE` | 留空 | 生产 fail-closed(缺 NEXUS_API_KEY 拒);本地 dev 才设 dev |
| `PORT` | `7860` | HF 要求监听端口;dashboard 直监听非反代 |
| `DASHBOARD_BIND_HOST` | `0.0.0.0` | HF 公网绑须 auth provider;此值=非 loopback → auth gate 开 → BasicAuthProvider 接管 /login 密码表 |
| `HERMES_DASHBOARD_BASIC_AUTH_USERNAME` | `<用户名,如 admin>` | dashboard 密码闸门(原生 BasicAuthProvider,bundled 自动加载);缺三件任一 → list_providers() 空 → gate SystemExit fail-closed 拒起 |
| `HERMES_DASHBOARD_BASIC_AUTH_PASSWORD` | `<密码,或配 _PASSWORD_HASH 用 scrypt 哈希>` | 同上;明文内存哈希或预先 `python -c "from plugins.dashboard_auth.basic import hash_password; print(hash_password('PW'))"` 算 `_PASSWORD_HASH` |
| `HERMES_DASHBOARD_BASIC_AUTH_SECRET` | `<固定 ≥32 字节 base64/hex 随机>` | HMAC cookie 签名;**须固定**(默认随机重启失效 session 让用户重登;设固定让 session 跨重启保活) |
| `R2_ENDPOINT` | `https://<account_id>.r2.cloudflarestorage.com` | litestream WAL→R2 + R2 文件 CRUD |
| `R2_ACCESS_KEY_ID` | `<R2 key>` | R2 鉴权 |
| `R2_SECRET_ACCESS_KEY` | `<R2 secret>` | R2 鉴权 |
| `R2_CHECKPOINT_BUCKET` | `nexus-checkpoints` | litestream state.db WAL 副本所在桶 |
| `R2_ARTIFACTS_BUCKET` | `nexus-artifacts` | nexus-r2 plugin dashboard 文件 CRUD 桶 |
| `SUPABASE_URL` | `https://<id>.supabase.co` | nexus-ops plugin 业务表只读 + persist 四表 |
| `SUPABASE_SERVICE_ROLE_KEY` | `<service_role key>` | hermes 主入口写权(其余 Space 用 anon_key+RLS,见 03_rls_policies.sql) |
| `SUPABASE_DB_URI` | `postgresql://postgres:<pwd>@db.sitqowffcgnaxbvmpzbf.supabase.co:6543/postgres?sslmode=require` | langgraph AsyncPostgresSaver 6543 transaction pooler;**选 `?sslmode=require` 非 `?pgbouncer=true`** — checkpointer.db_uri() 对已带 sslmode 尊重保留原样返回最干净;`pgbouncer` 参数对 langgraph psycopg3 冗余(from_conn_string 内部硬编码 prepare_threshold=0 已解 pooler 冲突,checkpointer.py:17-21)。密码占位 `<pwd>` 填真值不入 git(铁律 L4) |
| `HF_TOKEN` | `<HF token w/ write>` | sync-logic-bucket 拉/推 Bucket + bootstrap fallback |
| **`SPACE_AUTHOR_NAME`** | **(禁用,勿填)** | HF 保留字(`SPACE_*` 前缀 HF 系统独占,填进 Secrets 触发 `configuration error: Reserved environment variables`)。代码已改用 `HF_OWNER` 单源,start.sh:55 fallback 删除。owner 靠 `HF_OWNER=sonoke` 一条够 |
| `NEXUS_LOGIC_BUCKET` | `logic` | bootstrap fallback 拉 bucket 名(须与真实 bucket `sonoke/logic` 名部分一致) |
| `HF_OWNER` | `sonoke` | sync-logic-bucket.sh 推 bucket 的 namespace;bootstrap owner fallback 同义 |

### 下游 Space URL(hermes 经 call_space tool 调下游)

| Secret 名 | 值 |
|---|---|
| `CLAUDE_URL` | `https://i3t2y-claude-code.hf.space` |
| `CODEX_URL` | `https://i3t2y-codex.hf.space` |
| `LANGGRAPH_URL` | `https://i3t2y-langgraph.hf.space` |
| `NEXUS_API_KEY` | `<下游验 key>` | 下游 Space 验 hermes 调用(若下游开鉴权) |

### IM(可选,缺则 telegram/discord `failed to connect` 非致命但 bot 不通)

| Secret 名 | 值 | 备注 |
|---|---|---|
| `TELEGRAM_BOT_TOKEN` | `<bot token>` | telegram polling;hermes 原生 `telegram_network.py` DoH+fallback IP 自解 HF DNS 封(K-R7 主路零配置) |
| `DISCORD_BOT_TOKEN` | `<bot token>` | discord REST/WS;硬编 `discord.com`/`gateway.discord.gg` 无 base_url 开关,DFS 封靠 `DISCORD_PROXY` |
| `DISCORD_PROXY` | `<HTTP/SOCKS proxy url>` | discord DNS/IP 封兜底(无则缺;hermes 自带 DoH 仅解 telegram 不解 discord) |
| `TELEGRAM_PROXY` | `<HTTP/SOCKS proxy url>` | telegram IP 封 fallback(主路 DoH 已解 DNS 封;此供 IP 封) |

## 步骤 2:sync-logic-bucket 推逻辑层进 HF Storage Bucket

本地跑(需 HF_TOKEN + HF_OWNER env 注):

```bash
cd /home/laisi/nexus
# 路1(推荐 防混淆):用 ~/.env.sonoke 模板(600 权,git 不扫)
source ~/.env.sonoke   # 含 HF_TOKEN + HF_OWNER=sonoke + NEXUS_LOGIC_BUCKET=logic
# 路2(inline 临时):手 export 三维
# export HF_TOKEN=<你的 hf_ token> HF_OWNER=sonoke NEXUS_LOGIC_BUCKET=logic
bash scripts/sync-logic-bucket.sh --dry-run  # 先预览
bash scripts/sync-logic-bucket.sh            # 真推
bash scripts/sync-logic-bucket.sh --verify    # 拉 bucket 验内容
unset HF_TOKEN HF_OWNER NEXUS_LOGIC_BUCKET    # 用完清防泄漏
```

推 `spaces/hermes/{app,scripts,libs}` 进 HF Bucket `sonoke/logic`(非旧占位 `i3t2y/nexus-logic`)。
HF Space 容器挂此 Bucket rw `/data`,逻辑层从挂载读(改逻辑只推 Bucket+Restart 不触 rebuild)。
`.env.sonoke` 模板防混淆(在 `~` 不在 repo + 600 权 + git 不扫,见记忆)。

## 步骤 3:HF Space README 改一字符触发 rebuild 拉 :stable

```bash
# 本地改 spaces/hermes/README.md 末尾加一空格或改一字符 → git push
git -C /home/laisi/nexus add spaces/hermes/README.md
git -C /home/laisi/nexus commit -m "deploy: 触发 HF rebuild 拉 nexus-base:stable"
git -C /home/laisi/nexus push origin feat/hermes-coreswap-nousresearch
```

HF Space `sonoke/h` 同步 `feat/hermes-coreswap-nousresearch` 分支 GitHub → push 触发 HF rebuild(GitHub repo 同步建)。
Dockerfile `ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable` → 拉 GHCR :stable(已 push,public 可拉已验 exit 0)+ `COPY start.sh` → 镜像建(用缓存 base 不重装 deps)。

## 步骤 4:HF Space Restart + 外部 cron 保活

HF Space 建/Restart 后:
- HF Logs 抓 boot 期望行:
  - `[start] bucket mounted OK`
  - `[start] nexus-r2 plugin staged` + `nexus-ops plugin staged`
  - `[start] config.yaml seeded`
  - `[hermes-boot] spawned gateway + dashboard`
  - `HERMES_BACKEND_READY port=7860` + `Hermes backend listening on 0.0.0.0:7860`
  - **无** `aiohttp not installed`(已修)
  - **无** `No adapter could be created`(IM 无 token 时 telegram/discord failed 但 api_server 起成)
  - **无** `is_sqlite_wal_reset_vulnerable`(K-R6 3.53.4 ≥3.51.3)

外部 cron 保活(HF Space 48h 休眠,自探不防本 Space 休眠):

```bash
# 任意外部 cron 服务(crontab/UptimeRobot/cron-job.org)每 5-10min ping:
curl -fsS https://sonoke-h.hf.space/health
# 期望 {"status":"ok","platform":"hermes-agent","version":"0.19.1"}(api_server /v1/health)
# 或 dashboard 7860 SPA HTML(/health 路由同壳)
```

## 步骤 5:实测验证

```bash
# 5a. /v1/runs HTTP 任务入口(deep 偏差2:body input 非 prompt)
KEY=<步骤1 填的 API_SERVER_KEY>
curl -s -X POST https://sonoke-h.hf.space/v1/runs \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"input":"reply with exactly: pong"}'
# 期望 {"run_id":"run_<hex>","status":"started"}

# 5b. deep 偏差3:run.completed 无 final_response,取 assistant.completed.content
RID=<5a 返回的 run_id>
curl -s -N https://sonoke-h.hf.space/v1/runs/$RID/events \
  -H "Authorization: Bearer $KEY"
# 期望 SSE 流:run.started → assistant.delta... → assistant.completed(content=final) → run.completed(usage 无 final_response)
# 取 assistant.completed 事件 content 字段 = 最终文本(非 run.completed)

# 5c. IM 通路(若填了 TELEGRAM_BOT_TOKEN):
# Telegram bot 私聊发 "规划一个多步工作流" → agent 智能调 nexus_route_langgraph tool → call_space 调下游 langgraph
# 流式 edit_message 回。K-R7 HF DNS 封靠 hermes 原生 DoH 自解(零配置),polling 应通。

# 5d. 过夜不崩 + R2 持久:
# 次日查 HF Logs 仍运行;Cloudflare R2 nexus-checkpoints 桶 `db/hermes-state.sqlite` WAL 副本存在
```

## K-R5 闸门(2026-08-02 二轮修正,与原生 BasicAuthProvider 对齐)

dashboard 公网绑 7860(`DASHBOARD_BIND_HOST=0.0.0.0`)非 loopback →
`should_require_auth=True`(web_server.py:442)→ `auth_required=True`(:17099)→
gate 开 → `list_providers()` 查 auth provider → 无则 `SystemExit("Refusing to bind
dashboard to {host} — the auth gate engages on non-loopback binds, but no auth
providers are registered")` fail-closed 拒起(:17193)。

**终局 = hermes 原生 BasicAuthProvider**(非 OAuth/NousPortal/register app —
此前误判 OAuth 错向已证伪):
- 插件 `plugins/dashboard_auth/basic/`(kind: backend,bundled,自动加载无需
  `plugins.enabled`,plugins.py:1450 `manifest.source=="bundled" and kind=="backend"`)。
- 触发 = env `HERMES_DASHBOARD_BASIC_AUTH_{USERNAME,PASSWORD,SECRET}`(五 env 全在
  basic/__init__.py 读:USERNAME:408/PASSWORD:414,451/PASSWORD_HASH:411/SECRET:372/
  TTL_SECONDS:417;`requires_env` plugin.yaml:6 只声明 USERNAME,其余可选)。
- 配齐 → basic plugin 注册 → /login 密码表单(scrypt 哈希 + HMAC stateless cookie,
  无 OAuth/IDP/DB)。缺 USERNAME 或 PASSWORD → list_providers() 空 → gate SystemExit 拒起。
- **secret 须设固定**(默认随机重启失效 session;设固定 base64/hex ≥32 字节让 session 跨
  重启保活)。secret 即步骤1 Secrets 表 `HERMES_DASHBOARD_BASIC_AUTH_SECRET` 那行。
- env 优先于 config.yaml `dashboard.basic_auth.*`(.env.example 段全走 env,HF Secrets 注)。

CORS:base 镜像 patch_web_server.py 仅改一处 `allow_origin_regex→allow_origins=["*"]`
(web_server.py:345,v0.19.1 + main 845031a 行号一致零漂),解 HF iframe embed
(sonoke-h.hf.space 在 huggingface.co iframe 内 fetch 跨域);鉴权走 BasicAuthProvider
cookie 非 CORS credential,无冲突。`--insecure` flag 已 DEPRECATED NO-OP 不用。

dev 免 gate = `DASHBOARD_BIND_HOST=127.0.0.1`(loopback → should_require_auth=False →
gate 关,免 auth provider,本地测可裸跑;HF 生产公网须 0.0.0.0 + 配齐 basic env)。

## K-R7 HF DNS 封实测

HF 部署后判仅 DNS 封还是 IP 封:
```bash
# HF Space terminal 内跑(docker exec 或 HF Logs console):
python -c "import socket; print(socket.getaddrinfo('api.telegram.org', 443))"
# 返 IP(非 NXDOMAIN)= DNS 解通 → HF 未封 telegram,hermes 原生直接通
# NXDOMAIN/拒绝 = DNS 封 → hermes 原生 telegram_network.py DoH 自取 fallback IP 自动解(零配置)
# DoH 仍不通 = IP 封 → 填 TELEGRAM_PROXY
```

discord 同测 `gateway.discord.gg` / `discord.com`;硬编无 base_url 开关,IP 封须 `DISCORD_PROXY`。

## 回滚

```bash
# GHCR :stable 回上一版(若有旧 :vN tag):
docker tag ghcr.io/i3t2y/nexus-base:<旧vN> ghcr.io/i3t2y/nexus-base:stable
docker push ghcr.io/i3t2y/nexus-base:stable
# HF Space Restart(用缓存镜像,不 rebuild)
```

## 本地 V5-V8 验已通(此清单前的本地实证)

| 闸门 | 实证 |
|---|---|
| dashboard SPA 7860 直监听(K-R2) | `/`→原生 Hermes Agent Dashboard HTML,非反代 |
| api_server adapter 8642(K1-1) | `/v1/health`→`{status:ok,version:0.19.1}` |
| `/v1/runs` body input(deep2) | `POST {"input":...}`→`{run_id,status:started}` |
| API_SERVER_KEY≥16 鉴权 | 无 Bearer→`Invalid gateway API key` |
| 两 plugin API mount | `/api/plugins/nexus-r2|ops`→Unauthorized 非 404 |
| 两 plugin 各 1 tab(deep4) | manifest single dict `/nexus-r2 after:files`+`/nexus-ops after:sessions` |
| K-R6 SQLite 3.53.4≥3.51.3 | `is_sqlite_wal_reset_vulnerable()=False`+`journal_mode:wal`+state.db 10 表 |
| gateway 双 thread 不崩 | IM 无 token `failed to connect` 非致命 |
| 风控自查 | 无自建 Gradio/uvicorn 壳 + 无 runtime pip install |
| litestream | 0.5.15 起无 R2 凭据 fail-open |

仅 deep 偏差3(assistant.completed.content)待 HF 真 provider 填后验(本地无 LLM 凭据 run 必 failed)。
