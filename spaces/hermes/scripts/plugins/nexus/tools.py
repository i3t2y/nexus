"""Nexus 下游桥接工具 schema + handler。

三 handler 均 `async def(args: dict, **kw) -> str`,内部 await
libs/shared/gateway.call_space 调下游 Space,返 tool_result()/tool_error()
JSON 字符串(hermes tool result 约定,见 tools/registry.py:847/861)。

call_space 签名(gateway.py:57):
  async def call_space(space, path, payload, request_id=None) -> dict
路径契约:_target_path = {"langgraph":"/execute","claude":"/run","codex":"/complete"}
payload = {"thread_id":..., "prompt":...}(下游 RunBody 期望)。
返下游 result(下游各自 main.py /run //complete //execute 返形)。
"""
from __future__ import annotations

from typing import Any

from tools.registry import tool_error, tool_result

# call_space 经 PYTHONPATH=/data/libs 同进程 import(同 hermes/(hermes_app) 框壳)。
# 模块级 import 避免 handler 每次重 import;但 call_space 依赖 R2/env 在 hermes 启动已就绪。
from shared.gateway import call_space


# ── tool schemas(OpenAI function 形态,同 plugins/spotify/tools.py:328 SAMPLE)──
NEXUS_CALL_CLAUDE_SCHEMA = {
    "name": "nexus_call_claude",
    "description": (
        "调用 Claude Code 下游 Space 做编码任务(实现/重构/调试/复杂逻辑)。"
        "当用户请求涉及写代码、修改代码、定位 bug、重构结构时选此 tool。"
        "返回下游 Claude 的推理/改稿结果(JSON),会自动回写 agent 会话记忆。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "要交给 Claude 的编码任务描述(自然语言或具体需求)",
            },
            "thread_id": {
                "type": "string",
                "description": "会话隔离 id;复用上游 Nexus thread_id 以串联多步任务",
            },
        },
        "required": ["prompt"],
    },
}

NEXUS_CALL_CODEX_SCHEMA = {
    "name": "nexus_call_codex",
    "description": (
        "调用 Codex 下游 Space 做快速补全/片段生成。"
        "当用户请求是补全一段代码、生成小片段、快速 snippet 时选此 tool(轻量 lane)。"
        "返回下游 Codex 的补全结果(JSON),会自动回写 agent 会话记忆。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "要交给 Codex 的补全/片段需求",
            },
            "thread_id": {
                "type": "string",
                "description": "会话隔离 id;复用上游 Nexus thread_id 以串联多步任务",
            },
        },
        "required": ["prompt"],
    },
}

NEXUS_ROUTE_LANGGRAPH_SCHEMA = {
    "name": "nexus_route_langgraph",
    "description": (
        "调用 LangGraph 下游 Space 做多步工作流编排。"
        "当用户请求是规划多步骤、含依赖分解的复杂工作流时选此 tool(编排专用 lane)。"
        "普通单步任务不该进此;默认用 omniroute 自推理或 nexus_call_claude 即可。"
        "返回下游 LangGraph 编排的中间步骤 + 最终结果(JSON),会自动回写 agent 会话记忆。"
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "prompt": {
                "type": "string",
                "description": "要交给 LangGraph 编排的规划/多步任务描述",
            },
            "thread_id": {
                "type": "string",
                "description": "会话隔离 id;复用上游 Nexus thread_id 以串联多步任务",
            },
        },
        "required": ["prompt"],
    },
}


# ── 路径契约(同 main.py _target_path:182)──
# ⚠️ 两处字面量复制无单一真理源:阶段四改下游契约(/execute_task 等)时必须两处同改,
#    否则 agent 路改了 force 路漏改 / 或反之 → 404 割裂。阶段四前两路映射均指一致老契约,不阻当前部署。
_TARGET_PATH = {
    "claude": "/run",
    "codex": "/complete",
    "langgraph": "/execute",
}


async def _invoke_downstream(space: str, args: dict[str, Any], **kw: Any) -> str:
    """统一桥到 call_space。三 handler 共用。

    handler 在 hermes _run_async 自建 loop 的 disposable thread 跑,
    await call_space(每次 httpx.AsyncClient context manager,无 loop-sticky client,安全)。

    thread_id 透传:优先 args["thread_id"],次 Hermes kw["task_id"](=上游 agent 的
      run_conversation task_id, agent_server 已传 task_id=上游 thread_id),
      缺省占位 "__nexus_tool_autogen__"。
    request_id 透传:沿同 thread_id 作 rid 串全链路排障(路A 显式传 rid 对照,路B 此处补齐);
      下游 new_request_id 收非空即复用不重生,跨 Space 串联不断。
    """
    prompt = args.get("prompt")
    if not prompt:
        return tool_error("prompt 是必填参数")

    thread_id = args.get("thread_id") or kw.get("task_id") or "__nexus_tool_autogen__"
    # request_id 透传:agent_server 调 run_conversation(task_id=thread_id) 时 kw 带 task_id;
    # 作 rid 透传下游,跨 HR→claude 多跳串联排障。
    request_id = kw.get("request_id") or thread_id
    payload = {"thread_id": thread_id, "prompt": prompt}

    try:
        result = await call_space(space, _TARGET_PATH[space], payload, request_id=request_id)
    except Exception as e:  # noqa: BLE001
        return tool_error(f"{space} 下游调用失败: {e}", space=space)

    # 下游返形主含 result/task_id/space/最终输出;原样回 tool_result 供 agent 读取
    return tool_result(result if isinstance(result, dict) else {"result": str(result)})


async def _handle_nexus_call_claude(args: dict[str, Any], **kw: Any) -> str:
    return await _invoke_downstream("claude", args, **kw)


async def _handle_nexus_call_codex(args: dict[str, Any], **kw: Any) -> str:
    return await _invoke_downstream("codex", args, **kw)


async def _handle_nexus_route_langgraph(args: dict[str, Any], **kw: Any) -> str:
    return await _invoke_downstream("langgraph", args, **kw)
