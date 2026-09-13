"""judge.py — v2.3 药2/药5: 第二裁判 LLM 评审(可选,默认关)

- 裁判模型: nvidia/moonshotai/kimi-k3(与 task model sensenova 异构,非同源)
- 冻结集:裁判只能看到 question + answer + rubric,看不到 case 的
  must_contain / expected_actions 等期望答案——防"出题=评分同源"。
- 输出: 5 维 [0,1] JSON。调用开关: EVAL_DUAL_JUDGE=1
"""
import json
import os
import subprocess

from harness import OMN_URL, omn_key  # 复用网关配置

JUDGE_MODEL = os.environ.get("EVAL_JUDGE_MODEL", "nvidia/moonshotai/kimi-k3")

RUBRIC = """你是独立评审。对下面这个"助手回答"按 5 个维度各打 [0,1] 连续分。
只依据问题与回答本身,你不知道标准答案也不知道系统内部期望。

维度:
- correctness: 回答内容是否事实上正确、切题
- completeness: 是否覆盖了问题的主要方面
- adherence: 是否聚焦所问、未越权发散、未泄露疑似凭证
- reproducibility: 是否含可复核的数字/引证/步骤
- efficiency: 是否简洁不注水

严格返回 JSON: {"correctness": x, "completeness": x, "adherence": x,
"reproducibility": x, "efficiency": x},不要任何其他文字。"""


def judge_case(question: str, answer: str) -> dict:
    """LLM 裁判打分。重试 2 次,失败返回 None(不阻塞主流程)。"""
    body = json.dumps({
        "model": JUDGE_MODEL,
        "messages": [
            {"role": "system", "content": RUBRIC},
            {"role": "user", "content": f"[问题]\n{question}\n\n[回答]\n{answer}"},
        ],
        "max_tokens": 1400,
        "temperature": 0.0,
    })
    key = omn_key()
    headers = ["-H", f"Authorization: Bearer {key}", "-H", "Content-Type: application/json"]
    for attempt in range(2):  # v2.3.2: 重试机制
        try:
            p = subprocess.run(
                ["curl", "-s", "-m", "60", OMN_URL] + headers + ["-d", body],
                capture_output=True, text=True, timeout=160)
            d = json.loads(p.stdout or "{}")
            msg = d["choices"][0]["message"] or {}
            txt = msg.get("content") or msg.get("reasoning_content") or ""
            import re
            m = re.search(r"\{[^{}]*\}", txt, re.S)
            scores = json.loads(m.group(0))
            dims = ("correctness", "completeness", "adherence", "reproducibility", "efficiency")
            per_dim = {k: float(scores.get(k, 0.0)) for k in dims}
            per_dim = {k: max(0.0, min(1.0, v)) for k, v in per_dim.items()}
            per_dim["mean"] = round(sum(per_dim.values()) / len(dims), 4)
            return per_dim
        except Exception as e:
            print(f"[judge] attempt {attempt+1} fail: {str(e)[:120]}", flush=True)
    return None


RUBRIC_PR_DOC = """你是独立评审。这是一个文档类(docs/**) PR,按 2 维打分 [0,1]:
- correctness: 文档内容是否事实正确、无幻觉
- relevance: 是否贴合 PR 标题/描述的目标
严格返回 JSON: {"correctness": x, "relevance": x, "mean": (c+r)/2}。无其他文字。"""

RUBRIC_PR_CODE = """你是独立评审。这是一个代码类 PR,按 5 维打分 [0,1]:
- correctness: 代码是否正确、无 bug
- minimal: 是否最小改动、不啰嗦
- actionable: 是否可直接运行/可落地
- relevant: 是否贴合 PR 目标
- extensive: 是否完备(覆盖边界/异常)
严格返回 JSON 5 维 + mean 字段。无其他文字。"""


# =========================================================================
# judge-prompt 外置热绑(2026-09-09 河图递归): 运行时从 nexus 仓拉声明稿
# 解析 "仅 docs 变更" 的维度名列表 + "含代码变更" 的维度名列表
# 拉取失败/解析失败 → 回退到上方硬编码 RUBRIC_PR_DOC / RUBRIC_PR_CODE
# =========================================================================
_REMOTE_MD_URL = "https://cnb.cool/nexus.zen/nexus/raw/main/docs/self-improvement/judge-prompt.md"
_MD_CACHE = {"at": 0.0, "text": None}
_MD_TTL_SEC = 3600
_DIM_ALLOWED = ("correctness", "minimal", "actionable", "relevant", "relevance", "extensive")
_DIM_CANON = {"relevant": "relevance"}  # 统一到长形


def _fetch_remote_md():
    import time
    now = time.time()
    if _MD_CACHE["text"] and now - _MD_CACHE["at"] < _MD_TTL_SEC:
        return _MD_CACHE["text"]
    try:
        import urllib.request
        t = urllib.request.urlopen(_REMOTE_MD_URL, timeout=8).read().decode("utf-8")
        _MD_CACHE["text"] = t
        _MD_CACHE["at"] = now
        return t
    except Exception:
        return _MD_CACHE["text"]  # 旧 cache 或 None


def _extract_dims(md: str, marker: str):
    """从 md 里抓一行含 marker 的句子,抽出维度名(re**x**x** 格式)。"""
    import re
    for line in md.splitlines():
        if marker not in line:
            continue
        dims = re.findall(r"\*\*([a-z]+)\*\*", line)
        dims = [d for d in dims if d in _DIM_ALLOWED]
        dims = [_DIM_CANON.get(d, d) for d in dims]
        if dims:
            return dims
    return None


def _dims_for_paths(paths):
    """从远程声明稿取维度;失败回退 (docs→2, code→5)"""
    md = _fetch_remote_md() or ""
    is_doc = all(p.startswith("docs/") or p.endswith(".md") for p in paths)
    key = "仅 docs 变更" if is_doc else "含代码变更"
    dims = _extract_dims(md, key)
    if dims:
        return dims
    return (["correctness", "relevance"] if is_doc
            else ["correctness", "minimal", "actionable", "relevance", "extensive"])


def _rubric_from_dims(dims, is_doc):
    descs = {
        "correctness": "内容是否事实正确、无 bug",
        "minimal": "最小改动,不冗余",
        "actionable": "可落地可执行",
        "relevance": "贴合 PR 目标",
        "extensive": "覆盖完备,考虑边界",
    }
    lines = [f"- {d}: {descs[d]}" for d in dims]
    kind = "文档类(docs/**)" if is_doc else "代码类"
    body = f"你是独立评审。这是一个{kind} PR,按 {len(dims)} 维打分 [0,1]:\n" + "\n".join(lines)
    keys = ", ".join(f'"{d}": x' for d in dims)
    body += f'\n严格返回 JSON: {{{keys}, "mean": <有效维度均值>}}。不要其他文字。'
    return body


def rubric_for_paths(paths):
    """路径感知标尺(热绑声明稿,失败回退硬编码)。"""
    is_doc = all(p.startswith("docs/") or p.endswith(".md") for p in paths)
    dims = _dims_for_paths(paths)
    # 远程声明稿给空 → 用硬编码
    if not dims:
        return RUBRIC_PR_DOC if is_doc else RUBRIC_PR_CODE
    return _rubric_from_dims(dims, is_doc)
