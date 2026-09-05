# 现 memgraph/ 废件归档(old/memgraph/)

本目录归档 **现仓 `memgraph/`**(34f057d 整理搬到根之后)中被淘汰的组件。
与 `old/spaces/langgraph/`(d96c408 之前的旧 langgraph 源码)区分。

## 归档件

### `bucket/patches/`(2026-08-18 归,mem0 架构删收尾)

旧 **Mem0 pgvector 后端 Neon 兼容补丁** 集(5 件):

| 文件 | 内容 |
|------|------|
| `10_default_config.py` | 覆写 mem0 默认 config |
| `20_pgvector_ext.py` | Patch mem0 `pgvector.py` Neon 兼容(连接池 + collection 两修复) |
| `30_clear_db_overrides.py` | 清 mem0 DB 覆写 |
| `40_health_worker.py` | Neon 保活 health worker(mem0 查询触发 Neon 唤醒) |
| `README.md` | patches 目录说明 |

2026-08 Mem0 架构经多轮演进(插件位 → 独立 memgraph Space server 模式)后,这些补丁针对的旧 mem0 ossBackend 代码路径已不在现役链中,但文件仍在仓内待收尾。

移自:`memgraph/bucket/patches/`(整目录)。
关联:`old/hermes/skills/mem0-backend-troubleshooting/`(同批归,mem0 旧 troubleshooting skill)+ `old/mem0/README.md`(mem0 总归档说明)+ 现役 Mem0 = `/memgraph/` 独立 Space。
