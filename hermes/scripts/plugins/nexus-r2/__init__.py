"""Nexus R2 plugin —— 三 downstream bridge tool + R2 dashboard tab。

K 阶段(实证推翻 B 段自建):Hermes Agent(NousResearch)内核换装后,agent 智能决策
调下游 Space 不再靠自建关键词 route(),而是 agent loop 自行判断 prompt 语义选调
nexus_call_claude(实现/重构/调试)/ nexus_call_codex(补全/片段)/
nexus_route_langgraph(规划/多步/工作流)三 tool。

三 tool 桥到现役 libs/shared/gateway.call_space(PYTHONPATH=/data/libs 同进程 import):
  - claude    → POST /run
  - codex     → POST /complete
  - langgraph → POST /execute
handler 返 tool_result()/tool_error() JSON 字符串(hermes agent tool result 约定),
agent 自动回写 messages/记忆(用户 Match1 意:结果回写 Hermes 记忆),无需手动注入。

本 plugin 同时注 dashboard/(manifest+plugin_api+dist) 一 R2 文件 CRUD tab,
经 web_server._discover_dashboard_plugins 自动 mount /api/plugins/nexus-r2/。

kind: backend 自动加载,免 plugins.enabled opt-in(同 spotify);但 toolset="nexus"
须经 config.yaml `platform_toolsets.<api_server/telegram/discord>` 显式列才透传
到各平台 agent(K-R3 闸门:plugin 注册 code import ≠ toolset 启用平台注入)。
"""
from __future__ import annotations

# 注:hermes 插件加载器(_load_directory_module)把 user 插件 import 为
# hermes_plugins.<slug>(~/.hermes/plugins/nexus-r2 → hermes_plugins.nexus_r2,
# 把目录名 `-` 替 `_` 成合法 python 标识符),仅把 nexus-r2/ 自身列入
# submodule_search_locations,父 plugins/ 不在 sys.path。故用相对 import(同包内)。
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
