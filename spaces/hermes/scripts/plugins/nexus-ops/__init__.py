"""Nexus Ops plugin —— 纯 dashboard tab，无 tool 注册。

K 阶段(K1 决策-3):hermes dashboard 每 plugin 仅 1 tab(manifest `tab` single dict)，
nexus 仪表需求中"下游 Space 探活/调度" + "业务表只读查" 两件原生无对照 → 单独 plugin tab。
本 plugin provides_tools=[]，register(ctx) 无 ctx.register_tool 调用(kind: backend 仍自动加载，
但 toolset 注入面无，不影响 platform_toolsets 配置)。

经 web_server._discover_dashboard_plugins 扫 dashboard/manifest.json 自动注 tab，
后端 dashboard/plugin_api.py (FastAPI APIRouter) mount /api/plugins/nexus-ops/。
"""
from __future__ import annotations


def register(ctx) -> None:
    """无 tool。占位 pass，loader 仍调（kind: backend 自动加载调 register，
    即便 provides_tools=[])。"""
    return None
