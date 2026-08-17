> ⚠️ 本 AGENTS.md 归档版,内容基于 d96c408 旧结构(2026-08-17 接手前),
> origin/main 已重构 hermes 提根 + memgraph 新空间 + Supabase→Neon,
> 此文件路径/状态全过时。待按 memgraph 新结构重写。
> 原文留作旧结构文档溯源。

# Nexus — AGENTS.md

给所有 Agent 看的项目说明。做什么/怎么跑/关键路径/坑。

## 项目是什么

Nexus = 混合 Agent 系统。云端 HF Spaces(hermes 换装后原生三组件 + omn 模型路由 + 三下游 thin proxy)+ Supabase(7 表 RLS)+ R2 + Bucket 永续(GHCR base 镜像 + 逻辑层 `/data` rw + 墓碑 Dockerfile)。同步 HTTP 调度。

## 怎么跑

**本地开发**(用户 Ubuntu):
```
cd ~/AgentOS/projects/nexus   # = /home/laisi/nexus
```

**HF 部署**:
- 三件(Dockerfile+README.md+start.sh)**红线区不动**——真源在 `spaces/hermes/scripts/` + Bucket 逻辑层
- 改逻辑 → `scripts/sync-logic-bucket.sh` 推 Bucket + Restart(**不触付费墙**,不 git push HF)
- 改 base 镜像 → 用户本地 `docker build` + `docker push` GHCR(`:stable` 标)

## 关键路径

```
docs/ARCHITECTURE.md          ← 现役架构权威件
docs/hermes/hermes-换装实况.md ← hermes 换装后真态权威件(给 AI 看)
docs/新架构/                  ← 演进提案(4 件,已核证,目标蓝图非现状)
sql/                          ← Supabase schema(00/01/02/03)
  00_schema.sql               ← 7 表:agent_states/task_logs/long_memory/task_queue/skills_index/backup_snapshots/space_health
  01_pgvector.sql              ← pgvector 扩展 + memory_vectors 示例表(1536 维,空骨架)
  03_rls_policies.sql          ← 7 表 anon deny,service_role 垄断写
spaces/hermes/scripts/         ← hermes 逻辑层(config.yaml.template + real-start.sh)
docker/requirements-base.txt   ← base 镜像依赖(supabase==2.31.0 已升)
scripts/sync-logic-bucket.sh  ← 推 Bucket + HF Restart(不触 rebuild/付费墙)
```

## 现役状态(2026-08)

| 组件 | 状态 |
|------|------|
| hermes | 换装完成。原生三组件(gateway api_server `/v1/runs` + dashboard SPA + 两 plugin tab)。omn provider 自定义(custom→omn 命名)。禁 Discord。telegram CF Worker 反代(HF 出不去 *.workers.dev 待解)。 |
| 三下游 | claude-code/codex/langgraph thin proxy。透传 LLM,错向升 CLI 子进程(待)。 |
| Supabase | 7 表 + pgvector 示例。supabase-py 2.31.0 治 Invalid API key(已闭合)。RLS anon deny。 |
| long_memory + memory_vectors | schema 在,**无应用层写入/检索 = 空骨架**(结构化摘要层预留,无语义检索)。 |
| R2 | 文件/skills 快照/备份。 |
| Bucket 永续 | GHCR base 静态化 + 逻辑层 `/data` rw + 墓碑 Dockerfile。start.sh 瘦引导 commit c5698c8。 |

## 已知坑

- **导入陷阱**:`from shared.gateway import` 非 `from gateway`。`py_compile` 不暴露此 runtime import 错误。
- **无终端**:用户无 HF 容器 shell/SSH,不能 tail/pip/docker exec。靠 boot log/TUI 栏/代码预证推断。
- **env echo 脱敏**:`echo "X=${X:+...}${X:-✗}"` 漏因 `${X:-}` 非空吐真值。全程 if -n/+ 不用 :-。
- **HF DNS 封 IM 域**:api.telegram.org HF IP 段封(不只 DNS)。CF Worker *.workers.dev HF 出不去(待解,可能 HF DNS 黑名单或出站段封)。
- **state.db malformed**:HF /data bucket FUSE+litestream 并发读 WAL→ corruption。解=HERMES_HOME 移 /opt/data 本地盘+删 litestream。已闭合。
- **429 HF 限速**:超大 context 撞 HF ingress 限速非 omn/supabase/rebuild bug。解=/new 或 /compact。

## 演进裁决(已拍)

主选 **② 择增量**(演进态):
- **Mem0 语义记忆**:2026-08-16 选项A 彻底删(原路径 A 激活链全删:config.yaml memory 段 + real-start.sh 注入段 + mem0.json.template + sql/04_mem0_selfhost.sql + requirements-base mem0ai)。hermes 原生 plugins/memory/mem0/ 随 base bundle 休眠(无激活配置 is_available 不触发)。复活须恢复全套激活链 + base build 含 mem0ai。语义记忆暂为空缺口。
- **本地 AgentOS 统一目录**(纯运维,正交):← 本目录,骨架已建

暂缓:Upstash(免费层日 1万 cmd 硬限)/Neon(免费层 5min 挂起破业务连续性)/CNB(免费临时性)——待真有常驻 worker+云端编码+长任务异步需求再评(届时准备付费)。

## 红线

- 所有 git push(GitHub repo + HF Space)须经用户显式同意,禁擅自 push
- HF sonoke/h push 易触发封禁
- 三件(Dockerfile+README.md+start.sh)尽量不动,README.md 硬禁区(连 rebuild 注释都不动)
- 真值 ~/.env.sonoke 不泄漏 HF_TOKEN/PAT
- pip install 必须加 --break-system-packages
