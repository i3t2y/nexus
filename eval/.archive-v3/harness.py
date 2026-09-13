#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nexus 自我改进 —— 评估执行器（replay harness）骨架  [v3 §3.5 / 阶段 0.5]

职责（修复 v2 P1）：给定 prompt/检索策略 diff，在隔离环境重跑 nexus agent，
抓取 trace，喂 score.py 得标量。

两种模式：
  --online  : 真实链路。需环境变量 NEXUS_REPO / BASE_REF / HERMES_EVAL_URL / HERMES_PSK，
              以及已部署的 hermes /eval 接口（v3 §3.3，PSK 鉴权）。
              流程：git worktree 起 diff 后 hermes 副本(只读 Neon/R2 快照)
                    → 对 cases 逐条回放(经 n-omn 免费池调模型) → 抓 trace → score。
  --offline : 本地验证。读已录制的 trace 文件，直接 score（无需 hermes，先跑通用）。

依赖：标准库 + requests（online 模式）。
"""
import argparse
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))


def score_one(expected, trace):
    # 延迟 import，避免无 requests 时 offline 也崩
    sys.path.insert(0, HERE)
    from score import score_case
    return score_case(expected, trace)


def score_dataset_offline(cases_path, traces_dir):
    """返回 (dataset_avg, n)。供 loop.py 取中位数/方差（Gate 0.5）。"""
    with open(cases_path, encoding="utf-8") as f:
        cases = json.load(f)
    by_id = {c["id"]: c for c in cases}
    scores = []
    for fn in sorted(os.listdir(traces_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(traces_dir, fn), encoding="utf-8") as f:
            trace = json.load(f)
        exp = by_id.get(trace["case_id"])
        if not exp:
            continue
        scores.append(score_one(exp["expected"], trace)["score"])
    return (sum(scores) / len(scores)) if scores else 0.0, len(scores)


def run_offline(cases_path, traces_dir):
    with open(cases_path, encoding="utf-8") as f:
        cases = json.load(f)
    by_id = {c["id"]: c for c in cases}
    for fn in sorted(os.listdir(traces_dir)):
        if not fn.endswith(".json"):
            continue
        with open(os.path.join(traces_dir, fn), encoding="utf-8") as f:
            trace = json.load(f)
        exp = by_id.get(trace["case_id"])
        if not exp:
            print("SKIP (no case):", trace["case_id"])
            continue
        res = score_one(exp["expected"], trace)
        print(f"{trace['case_id']:10s} score={res['score']:.4f}  banned={res['banned_hit']}")
    avg, n = score_dataset_offline(cases_path, traces_dir)
    print(f"DATASET score = {avg:.4f}  (n={n})")


def run_online(cases_path, eval_url, psk):
    try:
        import requests
    except ImportError:
        print("ERROR: online 模式需 requests (pip install requests)")
        return 2
    with open(cases_path, encoding="utf-8") as f:
        cases = json.load(f)
    for c in cases:
        # 占位：真实回放需调用 hermes /eval（PSK 头），由 hermes 侧 harness 跑 agent 抓 trace。
        # 此处仅示意调用形态，不实现模型交互（需 n-omn + hermes 运行时）。
        resp = requests.post(
            eval_url,
            headers={"Authorization": f"Bearer {psk}"},
            json={"repo": os.environ.get("NEXUS_REPO"),
                  "base_ref": os.environ.get("BASE_REF"),
                  "diff_patch": "<由 loop.py 提供>",
                  "target": c["id"]},
            timeout=120,
        )
        print(c["id"], resp.json())
    return 0


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--cases", default=os.path.join(HERE, "eval_cases.json"))
    ap.add_argument("--traces", default=os.path.join(HERE, "sample_traces"))
    ap.add_argument("--offline", action="store_true")
    ap.add_argument("--eval-url", default=os.environ.get("HERMES_EVAL_URL"))
    ap.add_argument("--psk", default=os.environ.get("HERMES_PSK"))
    args = ap.parse_args()
    if args.offline:
        return run_offline(args.cases, args.traces)
    if args.eval_url and args.psk:
        return run_online(args.cases, args.eval_url, args.psk)
    print("INFO: 未提供 --eval-url/--psk，回退离线模式（等价于 --offline）")
    return run_offline(args.cases, args.traces)


if __name__ == "__main__":
    sys.exit(main())
