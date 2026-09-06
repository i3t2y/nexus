#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
hermes /eval —— FastAPI 服务端（v3 §3.3 / 阶段1 / P7）

职责（黑盒评估器，NPC 不可达、eval 集与评估器仅在服务端、返回体只含标量）：
  POST /eval        {repo,base_ref,diff_patch,target} -> {score,breakdown,kept}
  POST /eval/batch  {repo,base_ref,diff_patch}         -> {scores,baseline_scores,dataset,heldout:{before,after},kept}
  GET  /eval/health
  GET  /eval/dryrun  Gate 1 自测：高分 / 归零(banned) 两类，证明评分与归零生效
  POST /eval/gate1  {diff_patch} -> {blocked:bool}  预检 diff 是否触碰受保护 eval 文件

鉴权（P7，n-omn gate 模式）：Bearer PSK（HERMES_EVAL_PSK）。无 PSK 拒绝对外服务。
隔离环境：backend="real" 时只读 Neon/R2 快照（HERMES_CORE_MODULE 提供）。
Gate 1（grep 锚点）：diff 触碰 eval_cases/ | /expected.json$ | /score.py$ -> kept:false，不生成 PR。

部署：本目录 + 同目录放 score.py / eval_cases.json / heldout.json（即 eval/ 三件套），
      HF Docker Space 须监听 7860（见 spaces.Dockerfile）。真实后端设 HERMES_EVAL_BACKEND=real。
"""
import os
import sys
import json
import re

HERE = os.path.dirname(os.path.abspath(__file__))
# 默认 EVAL_DIR = 本目录；本地自测可设 HERMES_EVAL_DIR=.. 指到 eval/
EVAL_DIR = os.environ.get("HERMES_EVAL_DIR", HERE)
sys.path.insert(0, EVAL_DIR)
sys.path.insert(0, HERE)  # 使 `import replay` 可用（replay.py 与本文件同目录）
from replay import replay_case  # 回放核心（stub / real 后端）

PSK = os.environ.get("HERMES_EVAL_PSK", "")
BACKEND = os.environ.get("HERMES_EVAL_BACKEND", "stub")
CASES_FILE = os.path.join(EVAL_DIR, "eval_cases.json")
HELDOUT_FILE = os.path.join(EVAL_DIR, "heldout.json")
GATE1_ANCHORS = [r"eval_cases/", r"/expected\.json$", r"/score\.py$"]

# ---- 评分（复用 eval/score.py，人写，NPC 不可读）----
try:
    from score import score_case
except Exception as exc:  # pragma: no cover
    score_case = None
    _SCORE_IMPORT_ERR = exc


def score_trace(expected, trace):
    if score_case is None:
        raise RuntimeError(f"score.py 未加载: {_SCORE_IMPORT_ERR}")
    return score_case(expected, trace)


def load_cases(path):
    if not os.path.exists(path):
        return []
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    return data if isinstance(data, list) else data.get("cases", [])


# ---- Gate 1 扫描 ----
def gate1_scan(diff_patch):
    """diff 触碰受保护 eval 文件 -> True（应拒）。"""
    for line in (diff_patch or "").splitlines():
        if line.startswith(("+++", "---", "diff --git")):
            for a in GATE1_ANCHORS:
                if re.search(a, line):
                    return True
    return False


# ---- POST /eval 单例 ----
def eval_one(target, diff_patch):
    if gate1_scan(diff_patch):
        return {"score": 0.0, "breakdown": {"gate1": "blocked"}, "kept": False}
    cases = load_cases(CASES_FILE)
    case = next((c for c in cases if c.get("id") == target), None)
    if not case:
        return {"error": "target not found", "kept": False}
    trace = replay_case(case, diff_patch, BACKEND)
    return {"score": score_trace(case["expected"], trace)["score"],
            "breakdown": score_trace(case["expected"], trace), "kept": True}


# ---- POST /eval/batch ----
def eval_batch(diff_patch):
    if gate1_scan(diff_patch):
        return {"scores": {}, "baseline_scores": {}, "dataset": 0.0,
                "heldout": {"before": 0.0, "after": 0.0}, "kept": False,
                "reason": "gate1_blocked"}
    cases = load_cases(CASES_FILE)
    ho = load_cases(HELDOUT_FILE)

    def _run(diff):
        sc, bs = {}, {}
        for c in cases:
            tr = replay_case(c, diff, BACKEND)
            s = score_trace(c["expected"], tr)["score"]
            sc[c["id"]] = round(s, 4)
            bs[c["id"]] = round(s, 4)  # baseline 与 candidate 同 diff 时一致；真实后端按 diff 区分
        ds = (sum(sc.values()) / len(sc)) if sc else 0.0
        ho_scores = [score_trace(c["expected"], replay_case(c, diff, BACKEND))["score"] for c in ho] if ho else []
        ho_avg = (sum(ho_scores) / len(ho_scores)) if ho_scores else 0.0
        return sc, bs, ds, ho_avg

    base_sc, base_bs, base_ds, ho_before = _run("")          # 基线（无 diff）
    cand_sc, cand_bs, cand_ds, ho_after = _run(diff_patch)   # 候选（含 diff）
    return {"scores": cand_sc, "baseline_scores": base_bs,
            "dataset": round(cand_ds, 4), "heldout": {"before": round(ho_before, 4), "after": round(ho_after, 4)},
            "kept": True}


# ---- trace 录制（仅校准期启用；日常默认关闭，防 trace/用例外泄）----
ALLOW_RECORD = os.environ.get("HERMES_EVAL_ALLOW_RECORD", "0") == "1"


def record_traces(ids):
    """对给定 case id（baseline diff=""）回放并返回 {case_id: trace}。
    仅在 HERMES_EVAL_ALLOW_RECORD=1 时可用；Gate 0 录完即关。"""
    if not ALLOW_RECORD:
        return {"error": "record disabled (set HERMES_EVAL_ALLOW_RECORD=1 during calibration only)", "traces": {}}
    pool = {c["id"]: c for c in load_cases(CASES_FILE) + load_cases(HELDOUT_FILE)}
    out = {}
    for cid in ids:
        c = pool.get(cid)
        if not c:
            continue
        out[cid] = replay_case(c, "", BACKEND)
    return {"traces": out}


# ---- dry-run 两类（Gate 1 证明）----
def dryrun():
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
    return {"high": score_trace(exp, good), "banned": score_trace(exp, bad)}


# ---- 无 FastAPI 时的自测 ----
def self_test():
    out = {}
    out["score_import"] = "ok" if score_case else f"FAIL:{_SCORE_IMPORT_ERR}"
    out["easy01"] = eval_one("easy-01", "")["score"]
    out["dryrun"] = {k: v["score"] for k, v in dryrun().items()}
    out["batch"] = eval_batch("")["dataset"]
    out["gate1_blocked"] = gate1_scan("diff --git a/eval_cases/x.json b/eval_cases/x.json\n+...")
    return out


# ---- HTTP 层（有 fastapi 才注册）----
try:
    from fastapi import FastAPI, Request, HTTPException
    from fastapi.responses import JSONResponse

    app = FastAPI(title="hermes /eval", version="1.0")

    def _auth(request: Request):
        if not PSK:
            raise HTTPException(status_code=503, detail="HERMES_EVAL_PSK 未配置，拒绝对外服务")
        auth = request.headers.get("Authorization", "")
        if auth.startswith("Bearer ") and auth[7:] == PSK:
            return True
        raise HTTPException(status_code=401, detail="missing/invalid PSK")

    @app.post("/eval")
    def http_eval_one(payload: dict):
        return eval_one(payload.get("target"), payload.get("diff_patch", ""))

    @app.post("/eval/batch")
    def http_eval_batch(payload: dict):
        return eval_batch(payload.get("diff_patch", ""))

    @app.get("/eval/health")
    def http_health():
        return {"ok": True, "backend": BACKEND, "psk_set": bool(PSK), "score_ok": score_case is not None}

    @app.get("/eval/dryrun")
    def http_dryrun():
        return dryrun()

    @app.post("/eval/gate1")
    def http_gate1(payload: dict):
        return {"blocked": gate1_scan(payload.get("diff_patch", ""))}

    @app.post("/eval/record")
    def http_record(payload: dict):
        return record_traces(payload.get("ids", []))

    # 注：上面 _auth 未接到路由依赖（保持示例简洁）；生产请在每路由加 Depends(_auth)。
    # 下方补一个统一的鉴权中间件更稳：
    from starlette.middleware.base import BaseHTTPMiddleware

    @app.middleware("http")
    async def psk_middleware(request: Request, call_next):
        if request.url.path in ("/eval/health",):
            return await call_next(request)
        try:
            _auth(request)
        except HTTPException as e:
            return JSONResponse(status_code=e.status_code, content={"detail": e.detail})
        return await call_next(request)

except ImportError:
    app = None  # 无 fastapi：仅 core 自测可用


if __name__ == "__main__":
    if app is None:
        print("fastapi 未安装。仅 core 自测：")
        print(json.dumps(self_test(), ensure_ascii=False, indent=2))
    else:
        import uvicorn
        uvicorn.run(app, host="0.0.0.0", port=int(os.environ.get("PORT", 7860)))
