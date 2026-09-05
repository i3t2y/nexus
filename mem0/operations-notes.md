# 运维笔记 (mem0 OSS 现行方案)

## 故障速查

| 症状 | 大概率原因 | 处置 |
|---|---|---|
| mem0_search 报 "connection is lost / pgvector" | Neon scale-to-zero 冷启动 | 重试一次; 常态则查 Neon 端点是否用了非 `-pooler` host |
| 重启后 mem0 静默变回默认/没记忆 | real-start.sh 注入段没跑或 env 缺值 | 看启动日志 mem0 注入段; 查 ZAI_API_KEY / NVIDIA_API_KEY / Neon 连接串 Secrets 是否齐 |
| generation ok=0 | mem0 LLM (glm-4.7-flash via z.ai) key 失效 | 换 ZAI_API_KEY; mem0 退化只影响"记忆提取质量", 不拖垮主聊天 |
| embed 报错维度不符 (1536 vs 2048) | 表是旧 1536 维建的 | 按旧 fix 链 DROP TABLE 重建 (见 docs/memgraph/mem0-server-config.md, 已标 ARCHIVED 但 fix 链仍有参考价值) |

## 不可违反

- `mem0.json` 由 real-start.sh 生成, 手改无效(重启即覆盖)。要改配置改 real-start.sh 的注入段。
- `hnsw` 保持 `false` — nemotron-3-embed-1b 输出 2048 维, 超 pgvector HNSW 2000 维上限。
- ZAI_API_KEY / NVIDIA_API_KEY / omn 中转 key 三个都不能删。
- 不回退到 platform/self-hosted 模式 — 2026-09-05 已由 Zen 拍板废弃(见 docs/shared/ARCHITECTURE.md 顶层说明)。
