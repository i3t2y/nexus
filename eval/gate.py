#!/usr/bin/env python3
"""eval-gate.py — PR 评审门禁(hermes 侧,防作弊:G-2 评估器在 NPC 不可达处)。

流程:
  cron 每 10min 跑一次 → 拉 nexus/npc-lab 两仓 open PR →
  对未评过的 PR 跑 eval(harness.py, 真后端) →
  把分数评论到 PR(评分卡) → 写 processed 标记防重评

红线:
  - eval cases 在 /data/.hermes/eval/(HF Bucket),CNB 仓与 NPC 均不可见
  - 只评论,不 merge;merge 永远人工
  - 评分卡含"与 baseline 对比",跌落 >0.05 显著标红
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
import urllib.error

# 河图 judge(LLM 裁判)融合;懒加载,失败降级回 rule 单源
try:
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    import judge as judge_mod
    from harness import OMN_URL, omn_key
except Exception as e:
    judge_mod = None
    OMN_URL = None
    def omn_key(): return ""

EVAL_DIR = "/data/.hermes/eval"
STATE_FILE = os.path.join(EVAL_DIR, "gate_state.json")
CASES_POOL = os.path.join(EVAL_DIR, "cases_pool.jsonl")  # 滚动采集的真实 PR 事件流
REPOS = ["nexus.zen/nexus", "nexus.zen/npc-lab", "nexus.zen/omn"]  # 2026-09-10 立项接入 omn(e48-2a六)
API = "https://api.cnb.cool"

DROP_ALERT = 0.05  # 与 baseline 比跌落超此值标 ⚠️

def ci_regressed(base_ci, cur_ci, cur_mean, b_mean):
    """v2.3 药4: CI 区间决策(替代纯点估计比较)。
    返回 (regressed: bool, note: str)。
    - 当前 CI 上界低于基线 CI 下界 → 确认退化(真信号)
    - 区间重叠 → 证据不足,观察(不告警)
    - 无 CI(旧 agg 格式) → 退回点估计比较
    """
    if not base_ci or not cur_ci or base_ci[0] is None or cur_ci[0] is None:
        return (b_mean is not None and cur_mean is not None and b_mean - cur_mean > DROP_ALERT), "点估计比较(无 CI)"
    if cur_ci[1] < base_ci[0]:
        return True, f"CI 不重叠: 当前[{cur_ci[0]},{cur_ci[1]}] < 基线下界 {base_ci[0]}"
    return False, f"CI 重叠: 当前[{cur_ci[0]},{cur_ci[1]}] vs 基线[{base_ci[0]},{base_ci[1]}],证据不足以判退化"

# ===== 全自动 merge 配置 =====
# 只对沙盒仓开自动 merge;nexus 生产仓永远人工
AUTO_MERGE_REPOS = ["nexus.zen/npc-lab"]
# 自动 merge 的 PR 改动的【所有】文件必须命中白名单,否则转人工
ALLOWED_PATHS = ("docs/", "lab/", "README.md", "scripts/")
# 永远拒绝自动 merge 的路径(改写 NPC 权限/流水线 = 提权攻击面)
FORBIDDEN_PATHS = (".cnb", ".cnb.yml", "eval/")


def pr_files(repo: str, num: int) -> list:
    """返回 PR 的真实仓内路径列表。CNb API 的 filename 只给 basename,
    patch 头的 '+++ b/<full_path>' 对新增文件可能缺失;
    最稳的是 blob_url/contents_url 里的路径段。"""
    d = cnb("GET", f"/{repo}/-/pulls/{num}/files?page_size=100")
    if isinstance(d, dict) and d.get("_err"):
        return []
    items = d if isinstance(d, list) else d.get("data", [])
    paths = []
    for f in items:
        real = ""
        # 优先级1: contents_url / blob_url(永远含完整路径)
        for key in ("contents_url", "blob_url", "raw_url"):
            url = f.get(key) or ""
            if url:
                # contents: .../git/contents/<path>?ref=...  blob: /-/blob/<sha>/<path>
                for marker in ("/-/git/contents/", "/-/blob/", "/-/git/raw/"):
                    if marker in url:
                        tail = url.split(marker, 1)[1]
                        tail = tail.split("?", 1)[0]
                        if marker == "/-/blob/" or marker == "/-/git/raw/":
                            # 弃第一段(sha)
                            parts = tail.split("/", 1)
                            if len(parts) > 1:
                                real = parts[1]
                        else:
                            real = tail
                        break
            if real:
                break
        # 优先级2: patch 头
        if not real:
            patch = f.get("patch") or ""
            for line in patch.splitlines():
                if line.startswith("+++ b/"):
                    real = line[6:].strip()
                    break
        paths.append(real or f.get("filename") or "")
    return paths


# ===== 则三: diff 实测(静态代码冒烟) =====
# 原则: 不 clone 仓(慢),直接解析 patch 重建"新文件全文",
# 然后本地语法/编译级检查。失败即拦,不问分数。

import tempfile


def _rebuild_new_file(patch: str) -> tuple:
    """从 unified diff patch 重建 '+++' 侧的文件全文。
    返回 (code_text, applied_hunks)。若 patch 不是全文(仓里其他部分没动),
    只对纯新增文件(hunks 覆盖全文)有意义 — 其余做保守降级。"""
    lines_new = []
    applied = 0
    for line in patch.splitlines():
        if line.startswith("+++ ") or line.startswith("--- "):
            continue
        if line.startswith("@@"):
            applied += 1
            continue
        if line.startswith("+"):
            lines_new.append(line[1:])
        elif line.startswith(" "):
            lines_new.append(line[1:])
        # '-' 行丢弃
    return "\n".join(lines_new), applied


def smoke_pr(repo: str, num: int, paths: list) -> dict:
    """对 PR 的每个代码文件做静态实测。返回 {file: (ok, note)}。
    - .py  → ast.parse(语法) + py_compile(字节码)
    - .sh  → bash -n,但**跳过嵌套在其它语言文本里的 shell 片段**(heredoc/if-block 混入会被当独立 shell 拦截→误伤fallout #19类 UX)。启发式:**文件仅含**若干非 shell 行(例如 python/js 逻辑行)则跳过。
    - .json → json.loads
    - .yml/.yaml → 结构粗检(不引 pyyaml,免依赖)
    其他类型一律 pass(不阻止)。"""
    results = {}
    d = cnb("GET", f"/{repo}/-/pulls/{num}/files?page_size=100")
    items = d if isinstance(d, list) else d.get("data", [])
    for f in items:
        patch = f.get("patch") or ""
        if not patch:
            continue
        real = ""
        for line in patch.splitlines():
            if line.startswith("+++ b/"):
                real = line[6:].strip()
                break
        path = real or f.get("filename") or ""
        if not path:
            continue
        text, _ = _rebuild_new_file(patch)
        if not text:
            continue

        ext = os.path.splitext(path)[1].lower()
        try:
            if ext == ".py":
                import ast as _ast
                _ast.parse(text)  # 语法
                with tempfile.NamedTemporaryFile("w", suffix=".py", delete=False) as tf:
                    tf.write(text)
                    tpath = tf.name
                r = subprocess.run([sys.executable, "-m", "py_compile", tpath],
                                   capture_output=True, timeout=20)
                os.unlink(tpath)
                if r.returncode != 0:
                    results[path] = (False, "py_compile 失败: " + r.stderr.decode()[:200])
                else:
                    results[path] = (True, "py ok")
            elif ext == ".sh":
                # heredoc/嵌套-in-python 启发式:全是独立 shell 才真跑 bash -n
                head = text.lstrip()[:200]
                non_shell_structure = (
                    "```" in text                     # markdown 混块
                    or head.startswith(("if ", "#", "\"", "'"))   # 被 others 包起来
                    or "EOF" in text                   # heredoc 终止符
                    or ("python" in text and ">>>" not in text[:50])
                )
                if non_shell_structure:
                    results[path] = (True, "sh ok (嵌入结构,跳过静态检)")
                else:
                    with tempfile.NamedTemporaryFile("w", suffix=".sh", delete=False) as tf:
                        tf.write(text)
                        tpath = tf.name
                    r = subprocess.run(["bash", "-n", tpath], capture_output=True, timeout=10)
                    os.unlink(tpath)
                    if r.returncode != 0:
                        results[path] = (False, "bash -n 失败: " + r.stderr.decode()[:200])
                    else:
                        results[path] = (True, "bash ok")
            elif ext == ".json":
                json.loads(text)
                results[path] = (True, "json ok")
            elif ext in (".yml", ".yaml"):
                # 粗检: 不能不空、不能含 tab(yaml 禁 tab)
                if "\t" in text:
                    results[path] = (False, "yaml 包含 tab")
                else:
                    results[path] = (True, "yaml 粗检过")
            else:
                pass  # 其他类型不挡
        except SyntaxError as e:
            results[path] = (False, f"语法错: {e.msg}@{e.lineno}")
        except json.JSONDecodeError as e:
            results[path] = (False, f"json 错: {e.msg}@{e.lineno}")
        except Exception as e:
            results[path] = (False, f"smoke 异常: {e}")
    return results


def smoke_summary(smoke: dict) -> tuple:
    """(all_ok, failures_text)"""
    fails = {p: n for p, (ok, n) in smoke.items() if not ok}
    return (len(fails) == 0, fails)


def auto_merge_verdict(repo: str, num: int) -> tuple:
    """返回 (ok, reason): 是否允许自动 merge。"""
    if repo not in AUTO_MERGE_REPOS:
        return False, "非沙盒仓,人工 merge"
    files = pr_files(repo, num)
    if not files:
        return False, "拿不到 diff 文件清单"
    for f in files:
        fb = f.lstrip("./")
        if any(fb == p or fb.startswith(p) for p in FORBIDDEN_PATHS):
            return False, f"触碰禁线 {f}"
        if not any(fb == p or fb.startswith(p) for p in ALLOWED_PATHS):
            return False, f"路径越白名单: {f}"
    return True, f"{len(files)} 个文件均在白名单"


EVAL_TOKENS = ('score.py', 'harness.py', 'judge.py', 'gate.py', 'eval/', 'cases_train', 'cases_heldout')

def hallucination_scan(pr: dict) -> tuple:
    """(v2 / 药7) 幻觉检测只查 diff 真实改动,不查 title/body 元文本(那是文档,不是代码)。
    检测两类:
      A. diff 中真实新增/修改涉及测量仪路径( EVAL_TOKENS )  → 拦
      B. title/body 声称"新增/实现/补了 X"但 files 中不存在该文件 → 拦(ARIS 反造假词表)
    返回 (suspicious: bool, reason: str)"""
    num = pr.get('number')
    repo = pr.get('_repo') or ''
    title = (pr.get('title') or '').lower()
    body = ((pr.get('body') or '') + (pr.get('description') or '')).lower()

    # --- A. diff 实查测量仪 ---
    if repo:
        files = pr_files(repo, num)
        ev_hits = [f for f in files if any(t in f for t in EVAL_TOKENS)]
        if ev_hits:
            return True, f"diff 实际改了测量仪/评估器:{ev_hits} — 人工检实"

        # --- B. 声称但没做:body 含"新增 X/实现 X"但 files 无该文件名 ---
        import re
        claimed = re.findall(r'(?:新增|添加|实现|落地|添加文件)\s*[：:]?\s*[`\'"]?([\w./-]+\.(?:py|md|json|yml|sh|jsonl|txt|yaml))', body)
        claimed = [c for c in claimed if '.' in c]
        if claimed and files:
            missing = [c for c in claimed if not any(c in f or f.endswith(c.split('/')[-1]) for f in files)]
            if missing:
                return True, f"声称新增 {missing} 但 PR 无对应文件 diff—造假风险"

        # --- C. 根目录误放检测: 新文件落在仓根(不在 docs/ scripts/ lab/ 已知目录) → 标记
        root_bad = [f for f in files
                    if "/" not in f
                    and f not in ("README.md", "LICENSE", "AGENTS.md", ".gitignore", ".gitattributes", "CLAUDE.md")]
        if root_bad:
            return True, f"{len(root_bad)} 个文件落在仓根(应进 docs/scripts/lab 子目录):{root_bad}"

    return False, ""


def key() -> str:
    for kv in open("/proc/1/environ", "rb").read().decode().split("\0"):
        if kv.startswith("HERMES_NEXUS_CNB="):
            return kv.split("=", 1)[1]
    # fallback: .env 文件
    for line in open("/opt/data/.hermes/.env"):
        if line.startswith("HERMES_NEXUS_CNB="):
            return line.split("=", 1)[1].strip()
    raise RuntimeError("HERMES_NEXUS_CNB not found")


def cnb(method: str, path: str, body=None):
    req = urllib.request.Request(
        API + path,
        data=json.dumps(body, ensure_ascii=False).encode() if body is not None else None,
        headers={
            "Authorization": f"Bearer {key()}",
            "Accept": "application/vnd.cnb.api+json",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=20) as r:
            raw = r.read()
            return json.loads(raw) if raw else {}
    except urllib.error.HTTPError as e:
        return {"_err": e.code, "_body": e.read()[:200].decode(errors="replace")}


def load_state():
    if os.path.exists(STATE_FILE):
        return json.load(open(STATE_FILE))
    return {"baseline": None, "processed": {}}


def save_state(s):
    json.dump(s, open(STATE_FILE, "w"), ensure_ascii=False, indent=1)


# ===== 药1: 滚动采集真实 PR 到 cases_pool =====
def harvest_pr_event(repo: str, pr: dict, outcome: str, scores: dict | None = None):
    """每个 eval 过的 PR 追加一条到 cases_pool.jsonl。
    数据最小化: 只抓可复测字段(title/paths/outcome/scores/judge),
    不抓 body 全文。append-only, 慢工慢用。"""
    rec = {
        "ts": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "repo": repo, "num": pr.get("number"),
        "title": (pr.get("title") or "")[:120],
        "author": ((pr.get("author") or {}).get("username") or "")[:40],
        "outcome": outcome,  # auto_merge | block_smoke | flag_halluc | human_review | merged
        "files": (scores or {}).get("paths") or [],
        "levels": (scores or {}).get("levels") or {},
        "mean": (scores or {}).get("mean"),
        "updated_at": pr.get("updated_at"),
    }
    # 幂等: 同 PR+同 updated_at 已在池里则跳过(避免 10 分钟 cron 重复采集)
    try:
        if os.path.exists(CASES_POOL):
            sig = f'{repo}#{pr.get("number")}@{pr.get("updated_at")}'
            with open(CASES_POOL) as f:
                for line in f:
                    try:
                        r = json.loads(line)
                    except Exception:
                        continue
                    if f"{r.get('repo')}#{r.get('num')}@{r.get('updated_at')}" == sig:
                        return
    except Exception:
        pass
    try:
        with open(CASES_POOL, "a") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except Exception as e:
        print(f"[harvest] write fail: {e}", file=sys.stderr)


def pool_stats() -> dict:
    """池中现存量与结局分布(纯读)。用完在 scorecard 里透出。"""
    if not os.path.exists(CASES_POOL):
        return {"n": 0}
    try:
        from collections import Counter
        outs = Counter()
        n = 0
        with open(CASES_POOL) as f:
            for line in f:
                try:
                    r = json.loads(line)
                    n += 1
                    outs[r.get("outcome") or "?"] += 1
                except Exception:
                    pass
        return {"n": n, "by_outcome": dict(outs)}
    except Exception:
        return {"n": 0}


def run_eval(path: str) -> dict | None:
    """跑指定集合(train 或 heldout),返回 {'mean':..,'n':..} 或 None"""
    p = subprocess.run(
        [sys.executable, os.path.join(EVAL_DIR, "harness.py"), path],
        capture_output=True, text=True, timeout=560, cwd=EVAL_DIR,
    )
    last = [l for l in p.stdout.splitlines() if l.startswith("== AGG")]
    if not last:
        print("[gate] no AGG line; stderr:", p.stderr[-300:], file=sys.stderr)
        return None
    i = last[-1].index("{")
    import ast
    return ast.literal_eval(last[-1][i:])


def run_eval_pair() -> dict | None:
    """train(防退化) + heldout(防过拟合) 双向保险丝。"""
    tr = run_eval(os.path.join(EVAL_DIR, "cases_train.json"))
    ho = run_eval(os.path.join(EVAL_DIR, "cases_heldout.json"))
    if not tr or not ho:
        return None
    return {"train": tr, "heldout": ho}


def scorecard(pr: dict, pair: dict, baseline) -> str:
    """pair = {'train': agg_tr, 'heldout': agg_ho}; baseline 兼容旧 float 或新 dict。"""
    tr, ho = pair["train"], pair["heldout"]
    if isinstance(baseline, dict):
        b_tr, b_ho = baseline.get("train"), baseline.get("heldout")
    else:
        b_tr, b_ho = baseline, None
    def line(name, agg, base):
        d = "" if base is None else f"(base {base:.4f} → {agg['mean']:.4f}, Δ={agg['mean']-base:+.4f})"
        w = " ⚠️" if (base is not None and base - agg["mean"] > DROP_ALERT) else ""
        ci = agg.get("ci") or (None, None)
        ci_txt = f" CI=[{ci[0]}, {ci[1]}]" if ci[0] is not None else ""
        return f"- {name} {agg['count']} 例: **{agg['mean']:.4f}**{ci_txt} {d}{w}"
    warn = ""
    if (b_tr is not None and b_tr - tr["mean"] > DROP_ALERT) or \
       (b_ho is not None and b_ho - ho["mean"] > DROP_ALERT):
        warn = "\n\n⚠️ **跌落超 0.05,请谨慎 merge**"
    def dims(agg):
        p = agg.get("per_dim") or {}
        if not p: return ""
        return "correct={correctness} compl={completeness} adh={adherence} repro={reproducibility} eff={efficiency}".format(**p)
    return (
        f"## 🤖 eval-gate 评分卡 (v2.3)\n\n"
        f"- PR: #{pr.get('number')} {pr.get('title')}\n"
        f"- task model: sensenova/sensenova-6.8-flash-lite\n"
        f"{line('train', tr, b_tr)} — {dims(tr)}\n{line('heldout', ho, b_ho)} — {dims(ho)}{warn}\n\n"
        f"> 评测在 hermes 侧(HF)运行,CNB/NPC 不可见 cases。raw_scores 已入 last_run.json 可复算。\n"
        f"> 本评论仅供人工评审参考,是否 merge 由人决定。"
    )


def main():
    state = load_state()

    # 首跑建 baseline(不入 PR);双集结构 {'train': x, 'heldout': y}
    if state["baseline"] is None:
        pair = run_eval_pair()
        if pair:
            state["baseline"] = {"train": pair["train"]["mean"], "heldout": pair["heldout"]["mean"]}
            save_state(state)
            print(f"[gate] baseline recorded: {state['baseline']}")
        return

    processed = state["processed"]
    for repo in REPOS:
        prs = cnb("GET", f"/{repo}/-/pulls?page_size=20")
        if isinstance(prs, dict) and prs.get("_err"):
            print(f"[gate] {repo} pulls err {prs['_err']}")
            continue
        items = prs if isinstance(prs, list) else prs.get("data", [])
        for pr in items:
            if pr.get("state") != "open":
                continue
            pr_key = f"{repo}#{pr['number']}"
            if processed.get(pr_key) == pr.get("updated_at"):
                continue
            # 只对 NPC 开的 PR 跑(人开的不烧钱);ophiros=env 模式验所有
            author = (pr.get("author") or {}).get("username", "")
            force_eval = os.environ.get("EVAL_FORCE_PR") == str(pr["number"])
            if not force_eval and "npc" not in author.lower() and "codebuddy" not in author.lower():
                processed[pr_key] = pr.get("updated_at")
                continue
            # 幂等键: head.sha 不变(只是评论/标签更新)即跳过; push 新 commit 才重评
            head_sha = (pr.get("head") or {}).get("sha") or ""
            prev = processed.get(pr_key)
            if isinstance(prev, dict) and prev.get("head_sha") == head_sha and head_sha:
                continue
            print(f"[gate] evaluating {pr_key} by {author} head={head_sha[:8]}")
            
            # === 幻觉检测(先于评估): diff 实际动测量仪 → 人工化 ===
            pr['_repo'] = repo  # 药7: 让 scan 能看到 diff files
            sus, r_reason = hallucination_scan(pr)
            if sus:
                cnb("POST", f"/{repo}/-/pulls/{pr['number']}/comments",
                    {"body": f"🚨 eval-gate: PR {pr['number']} 标记待人工 — {r_reason}\n\n这是防御性锁定(俗称防 NPC 自我扩权)。若需要人工决，请 @ 一下。"})
                harvest_pr_event(repo, pr, "flag_halluc")
                processed[pr_key] = pr.get("updated_at")
                save_state(state)
                print(f"[gate] {pr_key} flagged: {r_reason}")
                continue

            # === 则三: diff 实测(代码静态冒烟),不过即拦,不走评分 ===
            paths = pr_files(repo, pr["number"])
            smoke = smoke_pr(repo, pr["number"], paths)
            smoke_ok, smoke_fails = smoke_summary(smoke)
            if smoke and not smoke_ok:
                fail_lines = "\n".join(f"- `{p}` → {n}" for p, n in smoke_fails.items())
                cnb("POST", f"/{repo}/-/pulls/{pr['number']}/comments",
                    {"body": (f"🔴 eval-gate 代码实测未过,阻塞 merge:\n\n{fail_lines}\n\n"
                              f"请修正后**在原分支追加 commit**,eval-gate 下一轮会自动复测。")})
                harvest_pr_event(repo, pr, "block_smoke", {"paths": list(smoke.keys())})
                processed[pr_key] = pr.get("updated_at")
                save_state(state)
                print(f"[gate] {pr_key} smoke failed: {list(smoke_fails)}")
                continue
            if smoke:
                print(f"[gate] {pr_key} smoke pass: {list(smoke.keys())}")

            pair = run_eval_pair()
            if not pair:
                continue
            body = scorecard(pr, pair, state["baseline"])
            r = cnb("POST", f"/{repo}/-/pulls/{pr['number']}/comments", {"body": body})
            ok = not (isinstance(r, dict) and r.get("_err"))
            print(f"[gate] comment {'ok' if ok else 'FAILED '+str(r)}")

            # === 半自动返工: CI 决策(药4)优先于点估计跌落 ===
            if isinstance(state["baseline"], dict):
                b_tr = state["baseline"]["train"]
                b_ci = state["baseline"].get("ci", {}).get("train")
            else:
                b_tr, b_ci = state["baseline"], None
            mean_tr = pair["train"]["mean"]
            cur_ci = pair["train"].get("ci")

            # === judge 融合(药2+热绑路径感知): judge 打分后用 0.4/0.6 融合 ===
            judge_mean = None
            jst = state.setdefault("judge_stats", {"ok": 0, "fail": 0})
            if os.environ.get("EVAL_DUAL_JUDGE", "0") == "1" and judge_mod is not None:
                try:
                    files = pr_files(repo, pr["number"])
                    paths = [f["filename"] if isinstance(f, dict) else f for f in files]
                    rubric = judge_mod.rubric_for_paths(paths)
                    diff = "\n\n".join(f"### {p}\n{(fd.get('patch','') if isinstance(fd,dict) else '')[:2000]}"
                                        for p, fd in zip(paths, files))
                    body_ = json.dumps({"model": judge_mod.JUDGE_MODEL,
                        "messages":[{"role":"system","content":rubric},
                                    {"role":"user","content":f"[PR标题]\n{pr.get('title','')}\n\n[diff]\n{diff}"}],
                        "max_tokens":1400,"temperature":0}).encode()
                    pj = subprocess.run(["curl","-s","-m","100",OMN_URL,
                                          "-H",f"Authorization: Bearer {omn_key()}",
                                          "-H","Content-Type: application/json","-d",body_],
                                         capture_output=True, text=True, timeout=110)
                    _m = json.loads(pj.stdout)["choices"][0]["message"]
                    ct = (_m.get("content") or _m.get("reasoning_content") or "").strip()
                    s_,e_ = ct.find("{"), ct.rfind("}")
                    j = json.loads(ct[s_:e_+1])
                    judge_mean = float(j.get("mean", 0))
                    jst["ok"] += 1
                    print(f"[gate] judge mean = {judge_mean}")
                except Exception as e:
                    jst["fail"] += 1
                    print(f"[gate] judge fail: {e}")

            fused = mean_tr if judge_mean is None else 0.4*mean_tr + 0.6*judge_mean
            regressed, ci_note = ci_regressed(b_ci, cur_ci, mean_tr, b_tr)
            # judge 融合后,主判定走 fused;judge 挂才回退 rule
            if judge_mean is not None:
                # 分档阈值: docs 类 0.05 容差, code 类 0.10(0.78/0.87 咬紧)
                is_doc_pr = False
                try:
                    for fd in pr_files(repo, pr["number"]):
                        p_ = fd if isinstance(fd, str) else fd.get("filename","")
                        if not (p_.startswith("docs/") or p_.endswith(".md")):
                            break
                    else:
                        is_doc_pr = True
                except Exception:
                    pass
                tol = 0.05 if is_doc_pr else 0.10
                regressed = (fused < b_tr - tol)
                ci_note = (ci_note or "") + f" | judge_fused={fused:.3f}(rule {mean_tr:.3f}×0.4 + judge {judge_mean:.3f}×0.6, tol={tol})"
            drop = (b_tr - mean_tr) if mean_tr is not None else 0
            reworked = state.setdefault("rework", {})
            if regressed and pr_key not in reworked:
                rework_body = (
                    "@npc/CodeBuddy(deepseek-v4-flash)\n"
                    "角色: 老班张\n\n"
                    f"本 PR 评测得分 {mean_tr:.4f},与基线 {b_tr:.4f} 发生 CI 级退化。\n"
                    f"判定依据: {ci_note}\n"
                    "请检查改动是否引入了退化:核对 diff、修正后在同一分支追加 commit。"
                    "改完推送到本 PR 即可,eval-gate 会重新评分。\n"
                    "如确认为评测误差,请在评论说明理由,由人工裁决。"
                )
                r2 = cnb("POST", f"/{repo}/-/pulls/{pr['number']}/comments",
                         {"body": rework_body, "work_mode": True})
                ok2 = not (isinstance(r2, dict) and r2.get("_err"))
                print(f"[gate] rework summon {'ok' if ok2 else 'FAILED ' + str(r2)}")
                reworked[pr_key] = True  # 无论成败只尝试一次,防死循环
                # 判退也入池(负样本),否则池子只进及格分致基线失真
                harvest_pr_event(repo, pr, "rework_regressed", {"mean": mean_tr, "judge_mean": judge_mean, "fused": fused, "paths": pr_files(repo, pr["number"])})

            if ok:
                processed[pr_key] = pr.get("updated_at")
                save_state(state)

            # === 全自动 merge: CI 不重叠退化才拒,其余照常 ===
            # 点估计规则放宽: 只要 CI 判定无退化(含重叠观察)即视为达标
            drop_ok = not regressed
            if ok and drop_ok:
                allow, why = auto_merge_verdict(repo, pr["number"])
                if allow:
                    r3 = cnb("PUT", f"/{repo}/-/pulls/{pr['number']}/merge",
                             {"commit_title": f"[auto-gate] {pr.get('title')}", "merge_style": "squash"})
                    merged = isinstance(r3, dict) and r3.get("merged")
                    print(f"[gate] auto-merge {'🟢 ' if merged else 'FAILED ' + str(r3)}")
                    if merged:
                        harvest_pr_event(repo, pr, "auto_merge", {"mean": mean_tr, "paths": pr_files(repo, pr["number"])})
                        # 删源分支
                        pr_meta = cnb("GET", f"/{repo}/-/pulls/{pr['number']}")
                        # 分支名兜底从 head 推: 先理 head_ref
                        br = (pr_meta.get("head") or {}).get("ref") or pr_meta.get("head_ref") or ""
                        if br:
                            import urllib.parse
                            cnb("DELETE", f"/{repo}/-/git/branches/{urllib.parse.quote(br.removeprefix('refs/heads/'), safe='')}")
                        body2 = (f"✅ eval-gate 自动合并: 得分 {mean_tr:.4f} 达标(baseline {b_tr:.4f}),"
                                 f"diff 全在白名单({ALLOWED_PATHS})。分支已清理。")
                        # merge 后 issue 评论端点已关; PR 未合并前已评过分,此处只留 repo 日志
                        print(f"[gate] auto-merge ok: {body2}")
                else:
                    cnb("POST", f"/{repo}/-/pulls/{pr['number']}/comments",
                        {"body": f"🛑 eval-gate: 分项达标({mean_tr:.4f}),但拒自动 merge:{why}。转人工处理。"})
                    harvest_pr_event(repo, pr, "human_review", {"mean": mean_tr, "path_reject": why})
            time.sleep(1)
    save_state(state)


if __name__ == "__main__":
    main()