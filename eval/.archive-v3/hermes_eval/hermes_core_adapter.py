#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hermes /eval —— 真实后端适配骨架（v3 §阶段1 / M1 real 后端）

接 v3.1 阶段 0.5：把你的 hermes 路由核心包成 `replay(case, diff_patch, neon_ro_dsn)`，
server.py 在 `HERMES_EVAL_BACKEND=real` 时调用本模块（HERMES_CORE_MODULE=hermes_core_adapter）。

⚠️ 这是骨架：标记 [TODO-真实接入] 处需你按自己 hermes 代码结构填充。
   未接入时，`_STUB=True` 返回确定性占位 trace，证明契约/管线可跑（与 replay.py 的 stub 等价）。

trace 字段（须匹配 eval/score.py）：memory_hits / refused / dispatch / actions / turns / answer
"""
import os
import sys
import json

_STUB = os.environ.get("HERMES_CORE_STUB", "1") == "1"

# ---- 配置路径（按你的 hermes 仓实际位置改）----
ROUTING_PROMPT_PATH = os.environ.get("HERMES_ROUTING_PROMPT_PATH", "")
MEM0_CONFIG_PATH = os.environ.get("HERMES_MEM0_CONFIG_PATH", "")
MODEL_URL = os.environ.get("HERMES_EVAL_MODEL_URL", os.environ.get("NEXUS_OMN_URL", ""))
MODEL_KEY = os.environ.get("HERMES_EVAL_MODEL_KEY", os.environ.get("NEXUS_OMN_PSK", ""))
MODEL_NAME = os.environ.get("HERMES_EVAL_MODEL", "deepseek-v4-flash")
NEON_RO_DSN = os.environ.get("NEON_RO_DSN", "")


# ---------- 1) 应用 diff 到配置副本 ----------
def apply_diff(config_text, diff_patch):
    """把 diff_patch 应用到 config_text（hermes 路由 prompt / mem0 检索策略）。

    [TODO-真实接入] 生产应支持 unified diff（git apply）或 JSON-patch。
    此处提供最小可用实现：若 diff 含 'diff --git' 头，尝试 `git apply`；
    否则把 diff 视为"整文件替换"（最朴素的契约，便于首版联调）。
    """
    if not diff_patch:
        return config_text
    if diff_patch.strip().startswith("diff --git") or "\n--- " in diff_patch:
        # 用临时文件 + git apply（需在 hermes 仓内）
        import subprocess, tempfile
        with tempfile.NamedTemporaryFile("w", suffix=".patch", delete=False) as f:
            f.write(diff_patch)
            patch = f.name
        try:
            r = subprocess.run(["git", "apply", patch], capture_output=True, text=True)
            if r.returncode == 0:
                return config_text  # git apply 已直接改工作副本；此处返回原文本（实际应读新文件）
            # 失败则退化为整文件替换
        finally:
            os.unlink(patch)
    # 退化路径：整文件替换（首版联调用）
    return diff_patch


# ---------- 2) mem0 只读检索 ----------
def mem0_retrieve(query, neon_ro_dsn, top_k=5):
    """[TODO-真实接入] 从 Neon pgvector 只读快照检索相关记忆。

    生产实现示例（伪码）：
        import psycopg2
        conn = psycopg2.connect(neon_ro_dsn)
        cur = conn.cursor()
        cur.execute("SELECT content FROM memories ORDER BY embedding <=> %s::vector LIMIT %s",
                    (embed(query), top_k))
        return [r[0] for r in cur.fetchall()]
    未接入（无 DSN / stub）时返回空列表——评分将由 assertions 决定（不伪造命中）。
    """
    if not neon_ro_dsn:
        return []
    # [TODO-真实接入] 在这里接你的 pgvector 查询
    return []


# ---------- 3) 模型调用（OpenAI 兼容，n-omn）----------
def call_model(system_prompt, user_query, temp=0):
    """[TODO-真实接入] 经 n-omn 免费池调 DeepSeek-V4-Flash（temp=0 保证可复现）。"""
    if not MODEL_URL or _STUB:
        # stub：确定性占位回答（证明管线；不伪造 memory 命中）
        return {"answer": "(stub) 未接入真实模型", "turns": 1,
                "actions": ["memory.search"], "dispatch": None, "refused": False}
    import requests
    r = requests.post(
        MODEL_URL.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {MODEL_KEY}", "Content-Type": "application/json"},
        json={"model": MODEL_NAME, "temperature": temp,
              "messages": [{"role": "system", "content": system_prompt},
                           {"role": "user", "content": user_query}]},
        timeout=120,
    )
    r.raise_for_status()
    return {"answer": r.json()["choices"][0]["message"]["content"],
            "turns": 1, "actions": ["memory.search"], "dispatch": None, "refused": False}


# ---------- 4) 主入口：replay ----------
def replay(case, diff_patch, neon_ro_dsn=""):
    """server.py 约定接口：replay(case, diff_patch, neon_ro_dsn) -> trace。

    case 来自 eval_cases.json：{"id","prompt"/"input","expected":{...}}
    返回 trace 须含：memory_hits / refused / dispatch / actions / turns / answer
    """
    # 4.1 加载并应用 diff 到路由 prompt
    base_prompt = ""
    if ROUTING_PROMPT_PATH and os.path.isfile(ROUTING_PROMPT_PATH):
        with open(ROUTING_PROMPT_PATH, encoding="utf-8") as f:
            base_prompt = f.read()
    routing_prompt = apply_diff(base_prompt, diff_patch)

    # 4.2 mem0 检索（只读快照）
    query = case.get("prompt") or case.get("input") or case.get("id", "")
    memory_hits = mem0_retrieve(query, neon_ro_dsn or NEON_RO_DSN)

    # 4.3 组装 system prompt（路由 prompt + 检索到的记忆）
    system = routing_prompt
    if memory_hits:
        system += "\n\n[记忆上下文]\n" + "\n".join(memory_hits)

    # 4.4 调模型得回答
    m = call_model(system, query)

    # 4.5 组装 trace（字段对齐 score.py）
    trace = {
        "case_id": case.get("id"),
        "memory_hits": memory_hits,
        "refused": bool(m.get("refused", False)),
        "dispatch": m.get("dispatch"),
        "actions": m.get("actions", []),
        "turns": int(m.get("turns", 1) or 1),
        "answer": m.get("answer", ""),
    }
    return trace


# ---------- 自测（无 Neon / 无模型也能跑）----------
def self_test():
    case = {"id": "easy-01", "prompt": "nexus 生产入口是哪个？",
            "expected": {"assertions": [], "banned_patterns": []}}
    tr = replay(case, "", "")
    return {"trace_fields": sorted(tr.keys()),
            "memory_hits": tr["memory_hits"],
            "answer": tr["answer"][:40]}


if __name__ == "__main__":
    print(json.dumps(self_test(), ensure_ascii=False, indent=2))
