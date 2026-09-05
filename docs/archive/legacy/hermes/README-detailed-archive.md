<!-- 存档:原 HF Space sonoke/h README 详细版(K 形态技术说明)。
     2026-08-02 用户将 HF README 改为最简 frontmatter 防官方风控扫内容,
     此详细版下沉至 docs/hermes/ 保留技术说明,不再作 Space 元数据。
     HF 现役 README 仅保留 frontmatter(sdk=docker + app_port=7860),正文清空。
     两份同步:GitHub spaces/hermes/README.md 也降简版与本存档分离。 -->

---
title: Hermes (Nexus 主控)
emoji: 🧠
colorFrom: indigo
colorTo: purple
sdk: docker
app_port: 7860
pinned: true
short_description: Hermes Agent (NousResearch) on omniroute — Nexus 主控内核
tags:
  - agents
  - llm
  - router
  - nexus
  - hermes-agent
---

# Hermes — Nexus 主控内核

Nexus 唯一入口。换装 **NousResearch Hermes Agent** 作内核(github.com/NousResearch/hermes-agent),全原生三组件(K 形态,实证推翻前自建框壳):

- **gateway(含 api_server adapter)** —— hermes 原生 gateway 同 async loop 起 platform adapter:HTTP `/v1/runs`(api_server,env `API_SERVER_KEY`≥16 触发)+ telegram + discord polling。**非自建** `agent_server.py` /run 框壳(已废)。
- **dashboard SPA** —— hermes 原生 React19 Vite SPA(19 页),`web_server.start_server --port 7860` in-proc daemon thread 直监听(非 subprocess 避 cmdline 扫杀)。**非自建** Gradio 三 Tab(已废)。
- **两 plugin tab** —— `nexus-r2`(R2 文件 CRUD tab + 三 tool 共 `nexus` toolset)+ `nexus-ops`(下游/业务表只读 tab,无 tool)。manifest `tab` single dict → 单 plugin 不可 2 tab,故两目录各 1 tab。
- 三 tool 桥 `libs/shared/gateway.call_space` 调下游 claude-code/codex/langgraph,结果回写 agent 记忆。

**boot**(main.py 极薄,非自建路由):daemon thread1 `asyncio.run(start_gateway)` 起 gateway 含 api_server + IM;daemon thread2 in-proc `web_server.start_server(host,port=7860,headless=True)` 起 dashboard SPA;主线程 while sleep 监死,任一 daemon 死 → SystemExit 1 让 HF/supervisor 重启。

## 永续架构(三条铁律)

1. **逻辑层进 HF Storage Bucket `/data` rw 挂载** —— 改逻辑只推 Bucket + Restart,不触 HF rebuild 付费墙
2. **Dockerfile 永续墓碑** —— `ARG BASE_IMAGE=ghcr.io/i3t2y/nexus-base:stable` + 仅 `COPY start.sh`(逻辑层在镜像外)
3. **依赖进 GHCR base 镜像** —— hermes-agent + 蔓延依赖 + litestream + K-R6 自编 libsqlite3 3.53.4(≥3.51.3 防 fresh DB 强 DELETE 致 litestream 静默 off)+ K-R4 web_dist 预建 + K-R8 ui-tui/dist/entry.js 预建(ENV `HERMES_TUI_DIR=/opt/hermes-agent/ui-tui`,消 dashboard embedded-chat runtime npm install 死循环 → "Chat unavailable")+ messaging 子集(aiohttp/telegram/discord/brotlicffi)全在 base,逻辑层零 `pip install`

state.db 经 litestream WAL→R2 复制(铁律 L8)续命;Supabase 四表经 `persist_to_r2.py` 快照(灾备,与 litestream 互补)。

## 端点(hermes 原生 api_server,非自建路由)

| 方法 | 路径 | 说明 |
|------|------|------|
| GET  | `/v1/health` | 存活探测(api_server adapter) |
| POST | `/v1/runs` | HTTP 任务入口,body `{"input":...}`(字段 `input` 非 `prompt`),返 `{"run_id","status":"started"}` |
| GET  | `/v1/runs/{id}` | 查 run 状态/usage/messages |
| GET  | `/v1/runs/{id}/events` | SSE 流:run.started → assistant.delta → **assistant.completed(content=最终文)** → run.completed(无 final_response,取 assistant.completed.content) |
| POST | `/v1/chat/completions` | OpenAI 兼容 |
| *    | `/api/plugins/nexus-r2/*` | R2 文件 CRUD(nexus-r2 plugin_api.py) |
| *    | `/api/plugins/nexus-ops/*` | 下游探测 + Supabase 业务表只读(nexus-ops plugin_api.py) |

`API_SERVER_KEY` 一键双用:触发 api_server 启用 + `/v1/*` Bearer 鉴权。
dashboard SPA 7860 直监听;OAuth 闸门 K-R5(公网 0.0.0.0 须 auth provider,loopback 127.0.0.1 免)。

## Secrets

见 `docs/new/部署/hermes-v9-hf-deploy-checklist.md`。全 HF Space Secrets 注入,不入 git(铁律 L4):
- `ANTHROPIC_BASE_URL`=`https://nonoke-omn.hf.space` + `ANTHROPIC_API_KEY` + `HERMES_MODEL=glm-5.2`(omniroute)
- `API_SERVER_KEY`(≥16 随机)+ `R2_*` + `SUPABASE_*` + `SUPABASE_DB_URI`(`?sslmode=require`)
- `SPACE_AUTHOR_NAME=sonoke` + `NEXUS_LOGIC_BUCKET=logic` + `HF_TOKEN`(bootstrap 拉 `sonoke/logic` bucket)
- IM(可选):`TELEGRAM_BOT_TOKEN`/`DISCORD_BOT_TOKEN`/`DISCORD_PROXY`/`TELEGRAM_PROXY`(K-R7 HF DNS 封靠 hermes 原生 DoH 自解 telegram)
