"""Nexus 下游桥接插件 —— 把现役 call_space 注册为 Hermes Agent 的 custom tool。

永续改造:spaces/hermes/ 内核换装 NousResearch Hermes Agent 后,agent 智能决策
调下游 Space 不再靠自建关键词 route(),而是 agent loop 自行判断 prompt 语义选调
nexus_call_claude(实现/重构/调试)/ nexus_call_codex(补全/片段)/
nexus_route_langgraph(规划/多步/工作流)三 tool。

三 tool 桥到现役 libs/shared/gateway.call_space(PYTHONPATH=/data/libs 同进程 import):
  - claude  → POST /run
  - codex   → POST /complete
  - langgraph → POST /execute
handler 返 tool_result()/tool_error() JSON 字符串(hermes agent tool result 约定),
agent 自动回写 messages/记忆(用户 Match1 意:结果回写 Hermes 记忆),无需手动注入。

这是 kind: standalone 插件:不在 bundled 自加载白名单,须 HERMES_HOME/config.yaml
plugins.enabled: [nexus] opt-in 才加载(hermes_cli/plugins.py:1469-1488)。
agents 启动序列:先插件 register(塞 tools.registry) → 再 AIAgent(enabled_toolsets=["nexus"])
构造时 get_tool_definitions 才把 nexus toolset 喂给 agent loop。
"""
from __future__ import annotations

# 注:hermes 插件加载器(_load_directory_module)把 user 插件 import 为
# hermes_plugins.<slug>(~/.hermes/plugins/nexus → hermes_plugins.nexus),
# 仅把 nexus/ 自身列入 submodule_search_locations,父 plugins/ 不在 sys.path。
# 故用相对 import(同包内),非 spotify 式 `from plugins.spotify...`(那样只对
# bundled 插件成立,因 repo/plugins 父目录在 hermes 运行 sys.path)。
from .tools import (
    NEXUS_CALL_CLAUDE_SCHEMA,
    NEXUS_CALL_CODEX_SCHEMA,
    NEXUS_ROUTE_LANGGRAPH_SCHEMA,
    _handle_nexus_call_claude,
    _handle_nexus_call_codex,
    _handle_nexus_route_langgraph,
)

# (tool_name, schema, handler) —— toolset 统一 "nexus";is_async=True 走 _run_async 自动桥
_TOOLS = (
    ("nexus_call_claude", NEXUS_CALL_CLAUDE_SCHEMA, _handle_nexus_call_claude),
    ("nexus_call_codex", NEXUS_CALL_CODEX_SCHEMA, _handle_nexus_call_codex),
    ("nexus_route_langgraph", NEXUS_ROUTE_LANGGRAPH_SCHEMA, _handle_nexus_route_langgraph),
)


def register(ctx) -> None:
    """Plugin loader 调一次:注册三 nexus tool 进 toolset="nexus"。

    is_async=True 让 hermes 用 _run_async(model_tools.py) 自动桥 async handler,
    handler 内 await call_space 即可续下游 HTTP。
    """
    for name, schema, handler in _TOOLS:
        ctx.register_tool(
            name=name,
            toolset="nexus",
            schema=schema,
            handler=handler,
            is_async=True,
        )
