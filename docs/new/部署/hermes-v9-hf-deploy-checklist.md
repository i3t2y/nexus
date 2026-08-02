# Hermes Space V9 HF 部署清单(本地验 V5-V8 全过后)

> 本地 K 形态代码侧 + base 镜像闸门 + V5-V8 验全过(见 commit `8432594`)。
> 此清单 = HF Space 真部署执行步骤(需真凭据,凭据不入 git,经 HF Space Secrets UI 注)。
> 凭据侧我环境无 HF_TOKEN/HF_OWNER,此步**用户手填 UI + 自跑 sync + Restart**。

## 前置:本地已完成(我执行)

- [x] commit `8432594`(K 形态主代码 + base 镜像改 + V5-V8 验)
- [x] `ghcr.io/i3t2y/nexus-base:stable` 本地 build + push GHCR(2.84GB,含 K-R6 sqlite 3.53.4 + K-R4 web_dist + messaging 子集)
- [x] git push 分支 `feat/hermes-coreswap-nousresearch` 到 GitHub
- [ ] HF Space `i3t2y/hermes` Settings README 改一字符触发 rebuild 拉 :stable(下步)

## 步骤 1:HF Space Secrets 注入(HF Space Settings → Variables and secrets)

HF Space `i3t2y/hermes` → Settings → Repository secrets(New secret 逐条加,值不入 git):

### 必填(缺则 boot 崩或功能缺)

| Secret 名 | 值 | 必填原因 |
|---|---|---|
| `ANTHROPIC_BASE_URL` | `https://nonoke-omn.hf.space` | omniroute 第5 Space 入口,hermes provider=anthropic profile 指 |
| `ANTHROPIC_API_KEY` | `<omniroute 真 32char key>` | omniroute 鉴权;hermes 调 glm-5.2 经此 |
| `HERMES_MODEL` | `glm-5.2` | omniroute 透传模型 id |
| `API_SERVER_KEY` | `<≥16 字符随机串>` | api_server adapter 真触发器 + /v1/* Bearer 鉴权(同 key 双用) |
| `NEXUS_AUTH_MODE` | 留空 | 生产 fail-closed(缺 NEXUS_API_KEY 拒);本地 dev 才设 dev |
| `PORT` | `7860` | HF 要求监听端口;dashboard 直监听非反代 |
| `DASHBOARD_BIND_HOST` | `0.0.0.0` | HF 公网绑须 auth provider(K-R5);若先不配 OAuth 暂用 127.0.0.1 但外网不可达 |
| `R2_ENDPOINT` | `https://<account_id>.r2.cloudflarestorage.com` | litestream WAL→R2 + R2 文件 CRUD |
| `R2_ACCESS_KEY_ID` | `<R2 key>` | R2 鉴权 |
| `R2_SECRET_ACCESS_KEY` | `<R2 secret>` | R2 鉴权 |
| `R2_CHECKPOINT_BUCKET` | `nexus-checkpoints` | litestream state.db WAL 副本所在桶 |
| `R2_ARTIFACTS_BUCKET` | `nexus-artifacts` | nexus-r2 plugin dashboard 文件 CRUD 桶 |
| `SUPABASE_URL` | `https://<id>.supabase.co` | nexus-ops plugin 业务表只读 + persist 四表 |
| `SUPABASE_SERVICE_ROLE_KEY` | `<service_role key>` | hermes 主入口写权(其余 Space 用 anon_key+RLS,见 03_rls_policies.sql) |
| `SUPABASE_DB_URI` | `postgresql://postgres.<ref>:<pwd>@aws-0-<region>.pooler.supabase.com:6543/postgres` | langgraph AsyncPostgresSaver 6543 transaction pooler;checkpointer.db_uri() 自动补 sslmode=require |
| `HF_TOKEN` | `<HF token w/ write>` | sync-logic-bucket 拉/推 Bucket + bootstrap fallback |
| `SPACE_AUTHOR_NAME` | `i3t2y` | bootstrap fallback 用(HF_OWNER 同义) |

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
export HF_TOKEN=<your HF token>
export HF_OWNER=i3t2y  # 或 SPACE_AUTHOR_NAME
bash scripts/sync-logic-bucket.sh
```

推 `spaces/hermes/{app,scripts,libs,sql,start.sh}` 进 HF Bucket `i3t2y/nexus-logic`。
HF Space 容器挂此 Bucket rw `/data`,逻辑层从挂载读(改逻辑只推 Bucket+Restart 不触 rebuild)。

## 步骤 3:HF Space README 改一字符触发 rebuild 拉 :stable

```bash
# 本地改 spaces/hermes/README.md 末尾加一空格或改一字符 → git push
git -C /home/laisi/nexus add spaces/hermes/README.md
git -C /home/laisi/nexus commit -m "deploy: 触发 HF rebuild 拉 nexus-base:stable"
git -C /home/laisi/nexus push origin feat/hermes-coreswap-nousresearch
```

HF Space `i3t2y/hermes` README.md 改动 → HF 触发 rebuild。
Dockerfile `ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable` → 拉 GHCR :stable(已 push)+ `COPY start.sh` → 镜像建(用缓存 base 不重装 deps)。

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
curl -fsS https://i3t2y-hermes.hf.space/health
# 期望 {"status":"ok","platform":"hermes-agent","version":"0.19.1"}(api_server /v1/health)
# 或 dashboard 7860 SPA HTML(/health 路由同壳)
```

## 步骤 5:实测验证

```bash
# 5a. /v1/runs HTTP 任务入口(deep 偏差2:body input 非 prompt)
KEY=<步骤1 填的 API_SERVER_KEY>
curl -s -X POST https://i3t2y-hermes.hf.space/v1/runs \
  -H "Authorization: Bearer $KEY" -H "Content-Type: application/json" \
  -d '{"input":"reply with exactly: pong"}'
# 期望 {"run_id":"run_<hex>","status":"started"}

# 5b. deep 偏差3:run.completed 无 final_response,取 assistant.completed.content
RID=<5a 返回的 run_id>
curl -s -N https://i3t2y-hermes.hf.space/v1/runs/$RID/events \
  -H "Authorization: Bearer $KEY"
# 期望 SSE 流:run.started → assistant.delta... → assistant.completed(content=final) → run.completed(usage 无 final_response)
# 取 assistant.completed 事件 content 字段 = 最终文本(非 run.completed)

# 5c. IM 通路(若填了 TELEGRAM_BOT_TOKEN):
# Telegram bot 私聊发 "规划一个多步工作流" → agent 智能调 nexus_route_langgraph tool → call_space 调下游 langgraph
# 流式 edit_message 回。K-R7 HF DNS 封靠 hermes 原生 DoH 自解(零配置),polling 应通。

# 5d. 过夜不崩 + R2 持久:
# 次日查 HF Logs 仍运行;Cloudflare R2 nexus-checkpoints 桶 `db/hermes-state.sqlite` WAL 副本存在
```

## K-R5 OAuth 闸门(若 DASHBOARD_BIND_HOST=0.0.0.0)

dashboard 公网绑 7860 须 auth provider(`web_server.py:16954` /login /auth/*):
- 默认 NousPortal OAuth(需 register app + hf.space HTTPS callback 域)
- 或自带 password provider(`hermes_cli/dashboard_auth/` 注册自定义)
- 暂绕 = 先 `DASHBOARD_BIND_HOST=127.0.0.1`(外网不可达 dashboard,仅 api_server /v1/* 经 HF 路由可达 — 但 HF 7860 公网域指 dashboard 非 api_server 8642;此 local-tested-only)
- 生产须配 OAuth

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
