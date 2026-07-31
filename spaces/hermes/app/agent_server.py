"""Hermes Agent 内核调用入口(薄包装 NousResearch hermes-agent 的 AIAgent)。

永续改造:spaces/hermes/ 内核从自建关键词 route 换装为 Hermes Agent。
- 路径 B(主):agent loop 默认调 omniroute 推理,语义触发工具时 agent 调
  nexus_call_claude / nexus_call_codex / nexus_route_langgraph 三 custom tool
  (注册为 Hermes plugin,toolset="nexus",见 scripts/plugins/nexus/),桥到
  libs/shared/gateway.call_space 调下游 claude/codex/langgraph Space。结果回写 agent messages。
- force_space 兜底(路 A):main._do_run 收到 force_space= 时跳过 agent,直 call_space
  (向后兼容老 dashboard 调用语义)。本文件不处理 force,force 在 main.py 判断分流。

AIAgent 构造一次(模块级惰性单例),SQLite SessionDB 串行(asyncio.Lock 防并发写者)。
"""
from __future__ import annotations

import asyncio
import os
import logging
from typing import Any

logger = logging.getLogger("hermes.agent_server")

# 模块级惰性单例 + 锁
_agent: Any | None = None
_agent_lock = asyncio.Lock()  # 串行化 run_conversation(SQLite FTS5 单写者)


def _build_agent() -> Any:
    """惰性构造 AIAgent 单例。首次 import run_agent + 构造。

    provider=anthropic + ANTHROPIC_BASE_URL 指 omniroute(暴露 anthropic-Messages 兼容 API 最常见形态)。
    enabled_toolsets=["nexus"] 把自定义 nexus plugin 的三 tool 喂给 agent loop。
    disabled_toolsets 砍 HF 无头环境起不来 + 触发风控的浏览器/图像/语音/tts 工具集。
    fail-closed:ANTHROPIC_BASE_URL 缺 → 发 RuntimeError(配置错误,非放行)。
    """
    from run_agent import AIAgent  # type: ignore  # noqa: PLC0415

    base_url = os.getenv("ANTHROPIC_BASE_URL")
    api_key = os.getenv("ANTHROPIC_API_KEY")
    if not base_url or not api_key:
        raise RuntimeError(
            "config_error: ANTHROPIC_BASE_URL/ANTHROPIC_API_KEY 缺失"
            "(omniroute 入口 + 32 位 key 必填,HF Space Secrets 注入)"
        )

    model = os.getenv("HERMES_MODEL", "claude-sonnet-5")
    hermes_home = os.getenv("HERMES_HOME", "/data/.hermes")
    session_db = os.path.join(hermes_home, "state.db")

    # max_iterations 降 15-20(HF CPU-Basic 90 轮破 7860 超时;异步队列模式可放宽另设)
    # V3 实测前置(run_conversation 返 dict 键名)首跑 print 确认键名后再提值。
    return AIAgent(
        base_url=base_url,
        api_key=api_key,
        provider="anthropic",
        model=model,
        platform="http",
        quiet_mode=True,
        enabled_toolsets=["nexus"],
        disabled_toolsets=["browser", "image_generation", "voice", "tts"],
        session_db=session_db,
        max_iterations=15,
    )


def _get_agent() -> Any:
    global _agent
    if _agent is None:
        _agent = _build_agent()
    return _agent


async def run_agent_once(
    prompt: str,
    thread_id: str,
    request_id: str | None = None,
) -> dict[str, Any]:
    """单次 agent 执行。串行化(SQLite 单写者),await asyncio.to_thread 包同步 run_conversation。

    返 dict 形如 {task_id, final_response, completed, tokens, request_id}。
    run_conversation 返 dict 键(V4 源码实证 finalize_turn 完成路径,turn_finalizer.py:574-607):
      final_response(str)/messages/completed(bool)/interrupted(bool)/failed(bool)/
      total_tokens/input_tokens/output_tokens/turn_exit_reason/api_calls。
    """
    rid = request_id or ""
    logger.info("agent run start tid=%s rid=%s", thread_id, rid)

    async with _agent_lock:
        agent = _get_agent()
        # run_conversation 是同步阻塞 agent loop;to_thread 避免阻塞 uvicorn event loop。
        # task_id= 传我们的 thread_id(agent 据此隔离并发任务的 VM/资源,V4 实证 signature 含 task_id)。
        result = await asyncio.to_thread(
            agent.run_conversation,
            user_message=prompt,
            task_id=thread_id,
        )

    final = result.get("final_response") or ""
    completed = bool(result.get("completed"))
    interrupted = bool(result.get("interrupted"))
    failed = bool(result.get("failed"))
    tokens = {
        "in": result.get("input_tokens", 0),
        "out": result.get("output_tokens", 0),
        "total": result.get("total_tokens", 0),
    }

    return {
        "task_id": thread_id,
        "final_response": final,
        "completed": completed,
        "interrupted": interrupted,
        "failed": failed,
        "tokens": tokens,
        "request_id": rid,
    }
