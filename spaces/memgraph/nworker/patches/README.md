# patches/
# =======
# 独立 patch 脚本，由 start.sh 在启动时自动遍历执行 (按文件名字母序)。
# 每个脚本是一个自包含的 Python 文件，操作 /app 下的 mem0 server 代码。
#
# 以后新增 patch 只需在此目录加文件，不需要改三文件 (Dockerfile/README/start.sh)。
#
# 命名约定: NN_描述.py (NN=两位序号，控制执行顺序)
#
# 已知问题 → patch 对应关系:
#   10_default_config.py     — patch DEFAULT_CONFIG (NIM embedder + 智谱 LLM)
#                               Issue #4910/#4984: server_state initialize_state
#                               从 Neon settings 表读 config_overrides 覆盖 DEFAULT_CONFIG
#   20_pgvector_ext.py       — patch pgvector.py 跳过 CREATE EXTENSION
#                               Neon neondb_owner 无 superuser, pgvector 不是 trusted extension
#   30_clear_db_overrides.py — DELETE FROM settings WHERE key='config_overrides'
#                               清除 DB 中旧 config_overrides, 防止覆盖 patch 过的 DEFAULT_CONFIG
#   40_health_worker.py      — 注入 /health 保活端点 + 挂载 LangGraph worker router
