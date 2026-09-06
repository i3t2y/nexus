#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
nexus 自我改进 —— 迭代驱动器（v3 §1.4 / §3.5 / §4 阶段2 / §7.5）

修复 v2 P2（迭代驱动器未定）。流程：
  取 diff（--diff 直接给，或 --propose 经 n-omn CodeBuddy 由 --goal 生成）
    → 起隔离 worktree / 应用 diff
    → 跑 harness K 次（离线录制 trace 或在线 hermes /eval）
    → K=3 取中位数（P12）+ 方差（P6）
    → G-3 决策（held-out 加权 +0.05 绝对 且 零回归）→ keep / revert
    → 记 attempts 到 ~/.nexus-eval/attempts（worktree 外，修复 P4）+ 每日 R2 快照 + 追加 dashboard.md

两种模式：
  --dry / 离线（默认）: 用录制 trace 干跑。录制 trace 不随 diff 变化，故 candidate==baseline、
                        delta=0，G-3 会安全拒绝（no-op 不应通过）。用于验证端到端控制流与方差（Gate 0.5）。
  在线           : 传 --eval-url + --psk（hermes /eval，PSK 网关后置），由 /eval 重跑 hermes 抓新 trace。
                  /eval 约定返回 {"scores":{case_id:float}, "dataset":float}；M1 未建时为 stub，会给出指引。

依赖：标准库 + requests（--propose / 在线模式）。
真相源：attempts 永落 worktree 外；NPC 不可写（违反非目标 3，评估器/驱动器均人写）。
"""
import argparse
import datetime
import json
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
ATTEMPTS_ROOT = os.path.expanduser("~/.nexus-eval/attempts")
DASHBOARD = os.path.expanduser("~/.nexus-eval/dashboard.md")
R2_TARGET = "nexus-data:nexus-eval/attempts"  # nexus 现有 R2 通路；按实际桶名改


# ---------- 工具 ----------
def median(xs):
    xs = sorted(xs)
    n = len(xs)
    if n == 0:
        return 0.0
    if n % 2:
        return xs[n // 2]
    return (xs[n // 2 - 1] + xs[n // 2]) / 2.0


def variance(xs):
    if len(xs) < 2:
        return 0.0
    m = sum(xs) / len(xs)
    return sum((x - m) ** 2 for x in xs) / len(xs)


def now_id():
    return "b" + datetime.datetime.now().strftime("%Y%m%d-%H%M%S")


# ---------- 评估（接 harness）----------
def evaluate_offline(cases, traces, k):
    sys.path.insert(0, HERE)
    from harness import score_dataset_offline
    scores = [score_dataset_offline(cases, traces)[0] for _ in range(k)]
    return scores


def evaluate_online(eval_url, psk, k, diff_patch=""):
    """调 hermes /eval/batch K 次（温度=0 应可复现）。
    返回 (dataset_list[K], heldout_before, heldout_after, baseline_scores, candidate_scores)。
    /eval/batch 契约：{scores, baseline_scores, dataset, heldout:{before,after}, kept}。
    """
    try:
        import requests
    except ImportError:
        print("ERROR: 在线模式需 requests")
        return None
    ds, hb, ha = [], 0.0, 0.0
    base_map, cand_map = {}, {}
    for _ in range(k):
        resp = requests.post(
            eval_url.rstrip("/") + "/batch",
            headers={"Authorization": f"Bearer {psk}"},
            json={"repo": os.environ.get("NEXUS_REPO"),
                  "base_ref": os.environ.get("BASE_REF"),
                  "diff_patch": diff_patch},
            timeout=300,
        )
        data = resp.json()
        if "dataset" not in data:
            print("ERROR: /eval 未返回 dataset（M1 接口未就绪/PSK 错误）。请用 --dry 验证控制流。")
            print("       响应:", json.dumps(data, ensure_ascii=False)[:200])
            return None
        ds.append(float(data["dataset"]))
        hb = float(data.get("heldout", {}).get("before", 0.0))
        ha = float(data.get("heldout", {}).get("after", 0.0))
        base_map = data.get("baseline_scores", {}) or {}
        cand_map = data.get("scores", {}) or {}
    return ds, hb, ha, base_map, cand_map


# ---------- 提议（n-omn CodeBuddy，OpenAI 兼容）----------
def propose(goal, target_snippet, omn_url, omn_psk, model):
    try:
        import requests
    except ImportError:
        print("ERROR: --propose 需 requests")
        return None
    sys_p = ("你是 nexus 路由优化助手。给定优化目标与当前 hermes 路由 prompt / mem0 检索策略片段，"
             "输出一个最小 unified diff（git diff --no-index 风格，或针对单文件的完整新内容）。"
             "只输出 diff 文本，不要任何解释。")
    user_p = f"目标：{goal}\n\n当前配置片段：\n{target_snippet}\n\n请给出要应用的修改（unified diff）。"
    r = requests.post(
        omn_url.rstrip("/") + "/chat/completions",
        headers={"Authorization": f"Bearer {omn_psk}", "Content-Type": "application/json"},
        json={"model": model,
              "messages": [{"role": "system", "content": sys_p},
                           {"role": "user", "content": user_p}],
              "temperature": 0},
        timeout=120,
    )
    r.raise_for_status()
    return r.json()["choices"][0]["message"]["content"]


# ---------- 应用 diff ----------
def apply_diff(diff_text, target_repo, batch_id, dry):
    if dry:
        print(f"[{batch_id}] DRY: 跳过 git worktree/apply（离线，录制 trace 不随 diff 变化）")
        return True
    if not target_repo or not os.path.isdir(os.path.join(target_repo, ".git")):
        print(f"ERROR: --target 需为 git 仓库根（含 .git），当前={target_repo}")
        return False
    wt = os.path.join(target_repo, ".nexus-worktree", batch_id)
    subprocess.run(["git", "-C", target_repo, "worktree", "add", wt, "--detach"],
                   check=True)
    dpath = os.path.join(ATTEMPTS_ROOT, batch_id + ".diff")
    with open(dpath, "w", encoding="utf-8") as f:
        f.write(diff_text)
    try:
        subprocess.run(["git", "-C", wt, "apply", dpath], check=True)
    except subprocess.CalledProcessError:
        print(f"[{batch_id}] git apply 失败，回滚 worktree")
        subprocess.run(["git", "-C", target_repo, "worktree", "remove", wt, "--force"])
        return False
    return True


# ---------- 决策（G-3）----------
def decide(baseline_med, cand_med, heldout_before, heldout_after, regressions):
    delta = cand_med - baseline_med
    heldout_delta = heldout_after - heldout_before
    keep = (heldout_delta >= 0.05) and (regressions == 0) and (cand_med >= baseline_med - 1e-9)
    return keep, delta, heldout_delta


# ---------- 记录 ----------
def log_attempt(batch_id, rec):
    os.makedirs(ATTEMPTS_ROOT, exist_ok=True)
    path = os.path.join(ATTEMPTS_ROOT, batch_id + ".jsonl")
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    print(f"[{batch_id}] attempts -> {path}")
    # R2 快照（stub：打印 rclone 命令，不自动执行，避免误触凭证）
    print(f"[{batch_id}] R2 快照(建议): rclone copy {ATTEMPTS_ROOT} {R2_TARGET} --include '*.jsonl'")
    # dashboard
    os.makedirs(os.path.dirname(DASHBOARD), exist_ok=True)
    line = (f"- `{batch_id}` {rec['ts']} goal={rec['goal'][:30]!r} "
            f"baseline={rec['baseline_med']:.4f} cand={rec['cand_med']:.4f} "
            f"heldoutΔ={rec['heldout_delta']:+.4f} -> **{'KEEP' if rec['keep'] else 'REVERT'}**")
    with open(DASHBOARD, "a", encoding="utf-8") as f:
        f.write(line + "\n")


# ---------- 主流程 ----------
def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--goal", help="自然语言优化目标（需 --propose）")
    ap.add_argument("--target", help="hermes 配置 git 仓库根（--propose/apply 用）")
    ap.add_argument("--diff", help="直接给 unified diff 文件，跳过 propose")
    ap.add_argument("--cases", default=os.path.join(HERE, "eval_cases.json"))
    ap.add_argument("--heldout", default=os.path.join(HERE, "heldout.json"))
    ap.add_argument("--traces", default=os.path.join(HERE, "sample_traces"))
    ap.add_argument("--K", type=int, default=3, help="中位数取样的重复次数（P12）")
    ap.add_argument("--batch", default=now_id())
    ap.add_argument("--propose", action="store_true", help="经 n-omn CodeBuddy 由 --goal 生成 diff")
    ap.add_argument("--dry", action="store_true", help="离线干跑，用录制 trace，不重跑 hermes")
    ap.add_argument("--eval-url", default=os.environ.get("HERMES_EVAL_URL"))
    ap.add_argument("--psk", default=os.environ.get("HERMES_PSK"))
    ap.add_argument("--omn-url", default=os.environ.get("NEXUS_OMN_URL"))
    ap.add_argument("--omn-psk", default=os.environ.get("NEXUS_OMN_PSK"))
    ap.add_argument("--model", default="deepseek-v4-flash")
    args = ap.parse_args()

    bid = args.batch
    print(f"=== loop [{bid}] K={args.K} mode={'dry/offline' if (args.dry or not (args.eval_url and args.psk)) else 'online'} ===")

    online = bool(args.eval_url and args.psk and not args.dry)

    # 1) 取 diff
    diff_text = None
    if args.diff:
        with open(args.diff, encoding="utf-8") as f:
            diff_text = f.read()
        print(f"[{bid}] 使用 --diff 文件")
    elif args.propose:
        if not (args.omn_url and args.omn_psk):
            print("ERROR: --propose 需 NEXUS_OMN_URL / NEXUS_OMN_PSK（n-omn 网关）。")
            return 2
        snippet = ""
        if args.target and os.path.isfile(args.target):
            with open(args.target, encoding="utf-8") as f:
                snippet = f.read()[:4000]
        diff_text = propose(args.goal or "", snippet, args.omn_url, args.omn_psk, args.model)
        if not diff_text:
            return 2
        print(f"[{bid}] n-omn 生成 diff（{len(diff_text)} 字符）")
    else:
        print(f"[{bid}] 无 diff → candidate = baseline（no-op，G-3 应拒）")

    # 2) 应用（在线：准备 worktree 供人工复核/提交；离线：跳过）
    if diff_text:
        if not apply_diff(diff_text, args.target, bid, args.dry):
            return 2

    # 3) 评估：baseline(diff="") + candidate(diff=diff_text)
    if online:
        rb = evaluate_online(args.eval_url, args.psk, args.K, "")
        if rb is None:
            return 2
        base_scores, hb, _, _, _ = rb
        rc = evaluate_online(args.eval_url, args.psk, args.K, diff_text or "")
        if rc is None:
            return 2
        cand_scores, _, ha2, base_map, cand_map = rc
        baseline_med = median(base_scores)
        cand_med = median(cand_scores)
        heldout_before = hb
        heldout_after = ha2
        regressions = sum(1 for cid in cand_map
                          if cand_map.get(cid, 1.0) < base_map.get(cid, 1.0) - 1e-9)
    else:
        base_scores = evaluate_offline(args.cases, args.traces, args.K)
        baseline_med = median(base_scores)
        cand_scores = evaluate_offline(args.cases, args.traces, args.K)
        cand_med = median(cand_scores)
        if os.path.exists(args.heldout):
            ho = evaluate_offline(args.heldout, args.traces, args.K)
            heldout_before = heldout_after = ho[0]  # 离线录制 trace 不变
        else:
            heldout_before = heldout_after = baseline_med
        regressions = 0
    print(f"[{bid}] baseline median={baseline_med:.4f} candidate median={cand_med:.4f} "
          f"var={variance(base_scores):.6f} regressions={regressions}")

    # 4) 决策（G-3）
    keep, delta, ho_delta = decide(baseline_med, cand_med, heldout_before, heldout_after, regressions)
    print(f"[{bid}] candidate median={cand_med:.4f} delta={delta:+.4f} heldoutΔ={ho_delta:+.4f} -> {'KEEP' if keep else 'REVERT'}")

    # 7) 落地（real: commit worktree；dry: 仅记录）
    if keep and not args.dry and args.target:
        print(f"[{bid}] KEEP: 提交 worktree（人工复核后合入，AI 不合并，违反 PR 规范）")
    elif keep and args.dry:
        print(f"[{bid}] KEEP(dry): 若上线需 hermes /eval 真实重跑确认 heldoutΔ>=0.05")

    rec = {
        "ts": datetime.datetime.now().isoformat(timespec="seconds"),
        "batch": bid, "goal": args.goal or "", "K": args.K,
        "baseline": base_scores, "baseline_med": round(baseline_med, 4),
        "candidate": cand_scores, "cand_med": round(cand_med, 4),
        "heldout_before": round(heldout_before, 4), "heldout_after": round(heldout_after, 4),
        "heldout_delta": round(ho_delta, 4), "regressions": regressions,
        "keep": keep, "mode": "dry" if (args.dry or not (args.eval_url and args.psk)) else "online",
    }
    log_attempt(bid, rec)
    print(f"[{bid}] 完成。Gate 0.5 干跑通过（端到端无报错）。")
    return 0


if __name__ == "__main__":
    sys.exit(main())
