# Cron Memory 演进决策 (2026-09-05)

## 现状
- hermes v0.21.0 (2026.8.31) @ sonoke/h, cron 零 job（历史监控类 job 已在 Space 重建/迁移中遗失）
- mem0: 进程内 OSS → Neon pgvector (hermes_mem0 表), 运行正常, cron agent 可调 mem0 工具
- v0.21 新免费模型 SKU (GLM-5.3-Flash / MiniMax M3 / Nemotron-3.5-Lightning): 额度过小, 对 nexus 无增量, 不接入

## v0.21 对 nexus 有价值的三个 cron 新能力
| 能力 | 作用 | 用法 |
|---|---|---|
| continuity=true | 下次 run 注入上次输出, 自动去重/延续判断 | 监控类 job 一律开启 |
| monitor 门控 | 每 tick 先跑脚本/URL 检测; 输出无变化则跳过 LLM, 零 token | 监控目标为稳定 URL/文件时启用 |
| cron + mem0 | job 内可用 mem0_search/mem0_add 持久记录运行结论 | prompt 约定: 查旧记录→无实质变化回 NO_CHANGE→记录新结论 |

## 行动决议
- 暂不重建监控类 cron（没有正在跑的对象）。新建监控类 job 时按上表三件套默认开启。
- 不复产独立 mem0 server / self-hosted dashboard 模式（免额外 Space 常驻, 与分层架构原则一致）。
- 主力模型链不变: nonoke-omn(glm-5.2) → Mistral → Groq。

## 历史文档处置
- docs/memgraph/mem0-server-auth.md / mem0-server-config.md / mcp-server-for-hermes.md: 旧自托管 server/MCP 方案已废弃, 保留作为冷备恢复参考, 仅供 archive 级查阅。
