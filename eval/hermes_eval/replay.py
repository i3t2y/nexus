#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hermes /eval —— 回放核心（v3 §3.5 / 阶段1）

replay_case(case, diff_patch, backend):
  - backend="stub" : 离线自测桩。easy-01 返回满分 trace；其余返回中性占位 trace。
                     证明端到端管线能跑、能出分，但**不是真实评测**（未接 hermes/mem0/模型）。
  - backend="real" : 真实后端。读 HERMES_CORE_MODULE（你的 hermes 路由核心），
                     core.replay(case, diff_patch, neon_ro_dsn) -> trace。
                     适配口在此：把你的 hermes 路由 + mem0 检索包成 core.replay 即可上线。

trace 字段（须匹配 eval/score.py）：memory_hits / refused / dispatch / actions / turns / answer。
"""
import os
import sys
import json

# 离线自测样例 trace（与 eval/sample_traces/easy-01.json 一致）
SAMPLE_EASY01 = {
    "case_id": "easy-01",
    "memory_hits": ["n-omn 生产入口 omn.360710.xyz/v1"],
    "refused": False,
    "turns": 2,
    "answer": "入口 https://omn.360710.xyz/v1",
    "actions": ["memory.search"],
}


def replay_stub(case, diff_patch):
    if case.get("id") == "easy-01":
        return dict(SAMPLE_EASY01)
    return {
        "case_id": case.get("id"),
        "memory_hits": [],
        "refused": False,
        "turns": 3,
        "answer": "(stub) 未接入真实 hermes，返回占位答案",
        "actions": ["memory.search"],
    }


def replay_real(case, diff_patch):
    mod = os.environ.get("HERMES_CORE_MODULE")
    if not mod:
        raise NotImplementedError(
            "HERMES_CORE_MODULE 未设置：真实后端需指向你的 hermes 路由核心。"
            "约定接口：core.replay(case, diff_patch, neon_ro_dsn) -> trace")
    core_path = os.environ.get("HERMES_CORE_PATH", ".")
    if core_path not in sys.path:
        sys.path.insert(0, core_path)
    import importlib
    core = importlib.import_module(mod)
    neon_ro = os.environ.get("NEON_RO_DSN")
    return core.replay(case, diff_patch, neon_ro)


def replay_case(case, diff_patch, backend="stub"):
    if backend == "real":
        return replay_real(case, diff_patch)
    return replay_stub(case, diff_patch)
