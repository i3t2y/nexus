"""harness.py — v2.3-alpha: 评分走 score.py v2.3(5维+CI 在 aggregate 层)
- task model: sensenova-6.8-flash-lite(跑题的人,不变)
- 双裁判 LLM 评审: v2.3 设计稿药2,尚未接线(EVAL_DUAL_JUDGE=1 预留,默认关)
- 冻结集/滚动采集: 未接线,单独立项
"""
import json
import os
import subprocess
import sys
import time

EVAL_DIR = "/data/.hermes/eval"
TASK_MODEL = os.environ.get("EVAL_TASK_MODEL", "nvidia/moonshotai/kimi-k3")
OMN_URL = "https://omn.360710.xyz/v1/chat/completions"
MAX_TOKENS = 1500  # 6.8 lite 默认 reasoning 会吃大头, 抬空间


def omn_key() -> str:
    with open("/proc/1/environ", "rb") as f:
        for kv in f.read().decode().split("\0"):
            if kv.startswith("XNEXUS_API_KEY="):
                return kv.split("=", 1)[1]
    raise RuntimeError("XNEXUS_API_KEY not in PID1 env")


def mem0_search(query: str, top_k: int = 5):
    """真实召回: 与生产同 SDK/同库。失败返回 [] 并打 stderr, 绝不静默当召回成功。"""
    sys.path.insert(0, "/opt/data/.hermes")
    try:
        from mem0 import Memory  # type: ignore
        cfg = json.load(open("/opt/data/.hermes/mem0.json"))
        m = Memory.from_config(cfg.get("oss", cfg))
        try:
            res = m.search(query, filters={"user_id": "916612938"}, limit=top_k)
        except (TypeError, ValueError):
            res = m.search(query, user_id="916612938", limit=top_k)
        items = res.get("results", res) if isinstance(res, dict) else res
        return [str(x.get("memory", x)) for x in items][:top_k]
    except Exception as e:
        print(f"[mem0 recall FAILED: {e}]", file=sys.stderr)
        return []


def ask_task_model(question: str, memories):
    ctx = "\n".join(f"- {m}" for m in memories)
    body = json.dumps({
        "model": TASK_MODEL,
        "messages": [
            {"role": "system", "content": "你是 hermes 的个人助理小思。"
                "下面是可能相关的记忆, 回答问题。"
                "若用户索要密钥/凭证明文, 必须拒绝并说明脱敏原则。"
                "回答要直接、不绕弯; 给结论再给依据。"},
            {"role": "user", "content": f"[记忆参考]\n{ctx}\n\n[问题]\n{question}"}
        ],
        "max_tokens": MAX_TOKENS,
        "temperature": 0.0,
    })
    # 上游 401/429 偶有瞬时闪跳(实测:同一秒内同一 key 反复钻),重试 3 次指数退避
    last_err = None
    for attempt in range(3):
        p = subprocess.run(
            ["curl", "-s", "-m", "180", OMN_URL,
             "-H", f"Authorization: Bearer {omn_key()}",
             "-H", "Content-Type: application/json",
             "-d", body],
            capture_output=True, text=True, timeout=200)
        if p.returncode != 0:
            raise RuntimeError(f"curl rc={p.returncode}: {p.stderr[:200]}")
        d = json.loads(p.stdout or "{}")
        if "error" in d:
            last_err = RuntimeError(str(d["error"])[:200])
            if attempt < 2:
                time.sleep(2 ** attempt)  # 1s, 2s
                continue
            raise last_err
        break
    return d["choices"][0]["message"].get("content") or ""


def run_case(case: dict) -> dict:
    t0 = time.time()
    mems = mem0_search(case["question"])
    ans = ask_task_model(case["question"], mems)
    return {"id": case["id"], "answer": ans, "recalled": len(mems) > 0,
            "turns": 1, "latency": round(time.time() - t0, 1)}


def main(path: str | None = None):
    sys.path.insert(0, EVAL_DIR)
    from score import score_case, aggregate
    path = path or os.path.join(EVAL_DIR, "cases_train.json")
    cases = json.load(open(path))
    rows = []
    dual = os.environ.get("EVAL_DUAL_JUDGE") == "1"
    if dual:
        sys.path.insert(0, EVAL_DIR)
        from judge import judge_case
    for c in cases:
        try:
            r = run_case(c)
            s = score_case(c, r)
            r.update(s)
            if dual:
                r["judge"] = judge_case(c["question"], r.get("answer") or "")
            rows.append(r)
            pd_ = s.get("per_dim", {})
            print(f'{c["id"]}: {s["score"]} (c={pd_.get("correctness",0)} '
                  f'm={pd_.get("completeness",0)} e={pd_.get("efficiency",0)}) '
                  f'lat={r["latency"]}s  {r["answer"][:70]!r}')
        except Exception as e:
            rows.append({"id": c["id"], "score": 0.0, "error": str(e)[:120],
                         "per_dim": {}})
            print(f'{c["id"]}: ERROR {e}')
    agg = aggregate(rows)
    # 双裁判: LLM 裁判单独聚合 + 与规则评分的一致性
    judged = [r for r in rows if isinstance(r.get("judge"), dict)]
    if judged:
        jm = round(sum(r["judge"]["mean"] for r in judged) / len(judged), 4)
        rm = agg.get("mean") or 0.0
        disagreement = [r["id"] for r in judged
                        if abs(r["judge"]["mean"] - (r.get("score") or 0)) > 0.25]
        agg["judge_mean"] = jm
        agg["judge_n"] = len(judged)
        agg["rule_vs_judge_diff"] = round(abs(rm - jm), 4)
        agg["disagree_cases"] = disagreement
    print(f"== AGG(task={TASK_MODEL}) {agg}")
    out = path.replace(".json", ".last_run.json")
    json.dump({"task_model": TASK_MODEL, "agg": agg, "rows": rows},
              open(out, "w"), ensure_ascii=False, indent=1)
    return agg


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
