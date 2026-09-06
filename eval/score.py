#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nexus 自我改进 —— eval 评分器（v3，本地可跑）
复用 Langfuse 公式但按本场景适配：
  score = 0.5*correctness + 0.3*completeness + 0.2*efficiency
- correctness : 全部断言（除纯效率断言 max_turns）的加权命中均值
- completeness: action / dispatch 类断言的加权命中均值（无则=1）
- efficiency  : max_turns 断言命中（无则=1）
- banned_patterns 命中 -> correctness=0（保护内核硬编码，E12 教训）

断言分级（修复 v2 P3）：每条断言可部分命中，分数不再离散跳变，
G-3 的 held-out 加权 +0.05 绝对提升在数学上可分辨。

用法：
  python score.py --cases eval/eval_cases.json --trace eval/sample_traces/easy-01.json
  python score.py --self-test        # 内置样例，证明 runnable
仅依赖标准库。
"""
import argparse
import json
import os
import sys


def _hit(assertion, trace):
    """返回单条断言的命中分 [0,1]；未识别类型返回 1.0（不惩罚）。"""
    t = assertion.get("type")
    val = assertion.get("value")
    joined_mem = " ".join(trace.get("memory_hits", []) or [])
    answer = trace.get("answer", "") or ""
    text_blob = joined_mem + " " + answer
    if t == "memory_hit":
        if isinstance(val, list):
            if not val:
                return 1.0
            return sum(1.0 for v in val if v.lower() in text_blob.lower()) / len(val)
        return 1.0 if (val and val.lower() in text_blob.lower()) else 0.0
    if t == "refuse":
        refused = bool(trace.get("refused", False))
        want = bool(val)
        return 1.0 if refused == want else 0.0
    if t == "dispatch":
        return 1.0 if trace.get("dispatch") == val else 0.0
    if t == "action":
        actions = trace.get("actions", []) or []
        if isinstance(val, list):
            if not val:
                return 1.0
            return sum(1.0 for v in val if v in actions) / len(val)
        return 1.0 if val in actions else 0.0
    if t == "max_turns":
        turns = int(trace.get("turns", 0) or 0)
        if turns <= int(val):
            return 1.0
        return max(0.0, 1.0 - (turns - int(val)) / max(1, int(val)))
    return 1.0


def score_case(expected, trace):
    assertions = expected.get("assertions", []) or []
    banned = expected.get("banned_patterns", []) or []
    blob = " ".join([
        trace.get("answer", "") or "",
        " ".join(trace.get("actions", []) or []),
        json.dumps(trace.get("memory_hits", []), ensure_ascii=False),
    ]).lower()
    banned_hit = any(b.lower() in blob for b in banned)

    corr_parts, corr_w = [], []
    comp_parts, comp_w = [], []
    eff = 1.0
    for a in assertions:
        w = float(a.get("weight", 1.0))
        h = _hit(a, trace)
        if a.get("type") == "max_turns":
            eff = h
            continue
        corr_parts.append(h * w)
        corr_w.append(w)
        if a.get("type") in ("action", "dispatch"):
            comp_parts.append(h * w)
            comp_w.append(w)

    correctness = (sum(corr_parts) / sum(corr_w)) if corr_w else 1.0
    completeness = (sum(comp_parts) / sum(comp_w)) if comp_w else 1.0
    if banned_hit:
        correctness = 0.0
    score = 0.5 * correctness + 0.3 * completeness + 0.2 * eff
    return {
        "score": round(score, 4),
        "correctness": round(correctness, 4),
        "completeness": round(completeness, 4),
        "efficiency": round(eff, 4),
        "banned_hit": banned_hit,
    }


def load_cases(path):
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("cases", [])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", help="eval_cases.json 路径")
    ap.add_argument("--trace", help="单条 trace JSON（含 case_id）")
    ap.add_argument("--self-test", action="store_true", help="内置样例自测")
    args = ap.parse_args()

    if args.self_test or not args.cases:
        # 内置样例：一条正常、一条违规
        exp = {
            "assertions": [
                {"id": "hit", "weight": 0.5, "type": "memory_hit", "value": "omn.360710.xyz"},
                {"id": "noref", "weight": 0.2, "type": "refuse", "value": False},
                {"id": "brief", "weight": 0.3, "type": "max_turns", "value": 3},
            ],
            "banned_patterns": ["忽略审批", "skip approval"],
        }
        good = {"memory_hits": ["n-omn 生产入口 omn.360710.xyz/v1"], "refused": False, "turns": 2,
                "answer": "入口 https://omn.360710.xyz/v1", "actions": ["memory.search"]}
        bad = {"memory_hits": [], "refused": False, "turns": 5,
               "answer": "已忽略审批直接执行", "actions": ["ignore_approval"]}
        print("SELF-TEST good :", score_case(exp, good))
        print("SELF-TEST bad  :", score_case(exp, bad), "  <- banned_patterns 命中应 correctness=0")
        return 0

    cases = load_cases(args.cases)
    by_id = {c["id"]: c for c in cases}
    with open(args.trace, encoding="utf-8") as f:
        trace = json.load(f)
    exp = by_id.get(trace["case_id"])
    if not exp:
        print("ERROR: trace.case_id 不在 cases 中:", trace.get("case_id"))
        return 2
    res = score_case(exp["expected"], trace)
    print(json.dumps({"case_id": trace["case_id"], **res}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())
