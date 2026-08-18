#!/usr/bin/env python3
"""
Nexus Worker MCP Server — stdio MCP server

⚠ DEPRECATED (2026-08-18, Gork 首席架构裁决 + 仓内核证):
  换装后 Hermes Agent 走原生 plugin `scripts/plugins/nexus-r2/` 三 tool
  (nexus_call_claude / nexus_call_codex / nexus_route_langgraph) 经
  `libs/shared/gateway.call_space` 直调下游 Space, 替掉本 stdio 中转路。
  本文件仓内零引用 (grep 全仓 .py/.sh/.yaml/Dockerfile 无 import/config/路径),
  不再 `hermes mcp add nexus-worker`。子进程隔离在 HF 收益小 (worker 本就另一
  Space, 真隔离靠 Space 边界 + task_queue 队列, 不靠本机 stdio 桥)。
  文件留作旧架构 (Hermes→LangGraph Worker MCP 桥) 历史回溯, 待清理进 old/。
  现役真态见 docs/hermes/hermes-换装实况.md §2 三组件 + §10 plugin。

---------------------------- 以下为旧文档 (历史保留) ----------------------------

暴露一个工具: run_worker(task, user_id)
hermes 通过 MCP 调用此工具, 将多步任务 POST 到 LangGraph worker /worker/run

部署: hermes mcp add nexus-worker --command python3 --args /opt/data/.hermes/mcp/nexus_worker_mcp.py
环境变量:
  WORKER_URL    — HF Space URL (默认 https://nmem-memgraph.hf.space)
  WORKER_API_KEY — ADMIN_API_KEY 值
"""

import os
import sys
import json
import requests

WORKER_URL = os.environ.get("WORKER_URL", "https://nmem-memgraph.hf.space")
WORKER_API_KEY = os.environ.get("WORKER_API_KEY", os.environ.get("MEM0_API_KEY", ""))


def run_worker(task: str, user_id: str = "default") -> dict:
    """POST /worker/run 到 LangGraph worker"""
    try:
        resp = requests.post(
            f"{WORKER_URL}/worker/run",
            headers={
                "Content-Type": "application/json",
                "X-API-Key": WORKER_API_KEY,
            },
            json={"task": task, "user_id": user_id},
            timeout=120,
        )
        if resp.status_code != 200:
            return {"error": f"HTTP {resp.status_code}", "detail": resp.text[:500]}
        return resp.json()
    except Exception as e:
        return {"error": str(e)}


# ── MCP stdio protocol (JSON-RPC over stdin/stdout) ─────────────────────

def handle_request(req: dict) -> dict:
    method = req.get("method", "")
    req_id = req.get("id")
    params = req.get("params", {})

    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "capabilities": {
                    "tools": {"listChanged": False},
                },
                "serverInfo": {
                    "name": "nexus-worker",
                    "version": "1.0.0",
                },
            },
        }

    if method == "tools/list":
        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "result": {
                "tools": [
                    {
                        "name": "run_worker",
                        "description": (
                            "将多步任务发送到 LangGraph worker 编排器执行。"
                            "worker 会自动: 检索记忆 → 规划(plan) → 执行(act) → "
                            "验证(verify) → 反思(reflect) → 写回记忆(write)。"
                            "适用于: 需要记忆检索的推理任务、需要搜索的任务、"
                            "需要委托编码的任务。不适合简单的直接问答。"
                        ),
                        "inputSchema": {
                            "type": "object",
                            "properties": {
                                "task": {
                                    "type": "string",
                                    "description": "任务描述 (自然语言)",
                                },
                                "user_id": {
                                    "type": "string",
                                    "description": "用户 ID (默认 'default')",
                                    "default": "default",
                                },
                            },
                            "required": ["task"],
                        },
                    },
                    {
                        "name": "worker_health",
                        "description": "检查 LangGraph worker 和 mem0 server 的健康状态",
                        "inputSchema": {
                            "type": "object",
                            "properties": {},
                            "required": [],
                        },
                    },
                ],
            },
        }

    if method == "tools/call":
        tool_name = params.get("name", "")
        args = params.get("arguments", {})

        if tool_name == "run_worker":
            result = run_worker(
                task=args.get("task", ""),
                user_id=args.get("user_id", "default"),
            )
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, indent=2),
                        }
                    ],
                },
            }

        if tool_name == "worker_health":
            try:
                resp = requests.get(f"{WORKER_URL}/worker/health", timeout=10)
                result = resp.json() if resp.status_code == 200 else {"error": f"HTTP {resp.status_code}"}
            except Exception as e:
                result = {"error": str(e)}
            return {
                "jsonrpc": "2.0",
                "id": req_id,
                "result": {
                    "content": [
                        {
                            "type": "text",
                            "text": json.dumps(result, ensure_ascii=False, indent=2),
                        }
                    ],
                },
            }

        return {
            "jsonrpc": "2.0",
            "id": req_id,
            "error": {"code": -32601, "message": f"Unknown tool: {tool_name}"},
        }

    if method == "initialized":
        return {}  # notification, no response

    return {
        "jsonrpc": "2.0",
        "id": req_id,
        "error": {"code": -32601, "message": f"Unknown method: {method}"},
    }


def main():
    """MCP stdio 主循环"""
    while True:
        line = sys.stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            req = json.loads(line)
            resp = handle_request(req)
            if resp:  # notifications 返回空 dict, 不回复
                sys.stdout.write(json.dumps(resp) + "\n")
                sys.stdout.flush()
        except json.JSONDecodeError:
            pass
        except Exception as e:
            err = {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32603, "message": str(e)},
            }
            sys.stdout.write(json.dumps(err) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    main()
