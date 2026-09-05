# 现 hermes/ 废件归档(old/hermes/)

本目录归档 **现仓 `hermes/`**(34f057d 整理搬到根之后)中被淘汰、但仍留作历史参考的组件。
与 `old/spaces/hermes/`(d96c408 之前的旧 hermes 整源码)区分:

- `old/spaces/hermes/` = 旧架构完整 hermes 源码(2026-08 重构前,reset 前 commit)
- `old/hermes/` = 现役 `hermes/` 中陆续淘汰的零散件(此目录)

## 归档件

### `mcp/nexus_worker_mcp.py`(2026-08-18 归,Gork 首席裁决 DEPRECATED)

旧架构 **Hermes→LangGraph Worker stdio MCP bridge**:hermes 经 `hermes mcp add` 挂此 stdio server,JSON-RPC 转发任务给 memgraph LangGraph worker。

Gork 首席架构裁决(commit 783548a)**废此路**:换装后 hermes `call_space` 直调 memgraph FastAPI 已替掉 MCP stdio 桥,仓内零引用。文件本身只 `import os/sys/json/requests`(裸 stdio JSON-RPC 循环),**不依赖 mcp pip 包**(base 缺 mcp 包是另一条债 `old/` 此件正交)。

移自:`hermes/mcp/nexus_worker_mcp.py`(单文件,无外部引用)。
移时 commit:`a522100` 之后,重构债批 1 文件层挪位。
关联裁决件:`docs/memgraph/mcp-server-for-hermes.md` + `hermes/skills/hf-space-deploy-via-github/references/mcp-server-for-hermes.md` 两镜像件顶注 + SKILL.md 项目 9 已标 DEPRECATED。

### `skills/mem0-backend-troubleshooting/`(2026-08-18 归,mem0 架构删收尾)

旧 **Mem0 挂 hermes 插件位** 时代的 troubleshooting skill(SKILL.md + references/17 件 + templates/3 件)。
2026-08 Mem0 从 hermes 插件位移除改独立 memgraph Space(选项 A 彻底删)后,此 skill 失活但仍在仓内待收尾。

移自:`hermes/skills/mem0-backend-troubleshooting/`(24 件之一)。
关联:`old/mem0/README.md`(mem0 旧激活链归档总说明)+ 现役 Mem0 = `/memgraph/` 独立 Space。
