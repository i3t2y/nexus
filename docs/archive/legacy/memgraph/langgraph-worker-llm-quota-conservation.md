# LangGraph Worker: LLM Quota Conservation Patterns

> Production lessons from deploying a six-node LangGraph worker on HF Space
> with Zhipu GLM-4.7-flash (tight QPS limit) + mem0 server (also uses Zhipu).

## The Problem: Three-LLM-Call Graph Bottleneck

A naive LangGraph worker with `plan → act → reflect` nodes calls the LLM
three times per run. When the LLM provider has QPS rate-limiting (Zhipu
GLM-4.7-flash 429s on consecutive calls), AND the mem0 server itself uses
the same LLM for memory extraction, the worker is guaranteed to hit 429:

```
Space boot → mem0 server LLM init (uses Zhipu)
           → worker /worker/run
             → plan: Zhipu call #1 (may 429 — boot exhausted quota)
             → act (direct): Zhipu call #2 (likely 429)
             → reflect: Zhipu call #3 (guaranteed 429)
```

## Solution 1: Rule Matching in Plan Node (Zero LLM Cost)

Match task keywords BEFORE calling the LLM. Coding and search tasks are
deterministic — no reasoning needed to classify them:

```python
coding_keywords = ["写", "编写", "实现", "脚本", "代码", "deploy", "build", ...]
search_keywords = ["搜索", "查询", "最新", "search", "find", "latest", ...]

if any(k in task_lower for k in coding_keywords):
    state["action_type"] = "delegate"   # no LLM call
elif any(k in task_lower for k in search_keywords):
    state["action_type"] = "search"     # no LLM call
else:
    # Only ambiguous/direct tasks use LLM
    raw = llm_chat(classification_prompt)
```

**Impact**: ~70% of tasks skip the LLM entirely in the plan node. Only
`direct` tasks (translation, reasoning, summarization) trigger LLM.

**Pitfall**: Keyword lists need careful tuning. "今天" in search_keywords
causes "翻译成英文: 今天天气很好" to misclassify as search. Use more
specific keywords or require keyword-only matches (not substring of
a larger sentence).

## Solution 2: Local Heuristic Reflect (Zero LLM Cost)

Move the `reflect` node from LLM-based evaluation to local heuristics:

```python
def reflect(state):
    result = state.get("result", "")
    if len(result) < 10:
        quality = "needs_improvement"
    elif result.startswith("[LLM error"):
        quality = "failed"
    elif result.startswith("[已入队]"):
        quality = "good"   # task delegated
    elif result.startswith("[入队失败]"):
        quality = "failed"  # Neon write failed
    elif len(result) > 20 and not result.startswith("["):
        quality = "good"
    else:
        feedback = "结果待验证"
        quality = "good"   # default accept
```

**Impact**: Eliminates the third LLM call entirely. The `reflect` node
runs in <1ms. Total LLM calls per run: 0 (delegate/search) or 1-2 (direct).

## Solution 3: Conditional Retry Edge

The `verify` node checks result quality. If suspiciously short (< 5 chars)
and no prior retry, route back to `act` for a second attempt:

```python
def should_retry(state):
    if state.get("quality") == "needs_improvement" and state.get("retries", 0) <= 1:
        return "act"      # retry
    return "reflect"      # proceed

# In build_graph():
graph.add_conditional_edges("verify", should_retry,
    {"act": "act", "reflect": "reflect"})
```

**Important**: `quality=failed` (LLM unavailable) does NOT trigger retry —
retrying won't help if the LLM is down. Only `needs_improvement`
(suspiciously short but not errored) retries.

## LLM Call Budget per Task Type

| Task type | plan LLM? | act LLM? | reflect LLM? | Total |
|-----------|-----------|----------|--------------|-------|
| delegate (coding) | ❌ rule | ❌ queue | ❌ heuristic | **0** |
| search | ❌ rule | ❌ AnySearch | ❌ heuristic | **0** |
| search_and_extract | ❌ rule | ❌ AnySearch+extract | ❌ heuristic | **0** |
| write_file | ❌ rule | ❌ local write | ❌ heuristic | **0** |
| direct (reasoning) | ✅ (1-2 retry) | ✅ (1-2 retry) | ❌ heuristic | **1-4** |

## Post-Restart Cooldown

After `api.restart_space()`, the mem0 server's own LLM initialization
consumes Zhipu quota. Wait 15-20 seconds before sending LLM-bearing
`/worker/run` requests. Use `GET /worker/health` (no LLM) to verify
component readiness first.

## AnySearch API Protocol (JSON-RPC 2.0, NOT REST)

AnySearch uses JSON-RPC 2.0 — the same protocol as the CLI scripts.
Do NOT use a hypothetical REST endpoint like `api.anysearch.pro/v1/search`
(that was a guess that returned empty results). The correct protocol:

```python
ENDPOINT = "https://api.anysearch.com/mcp"
payload = {
    "jsonrpc": "2.0", "id": 1, "method": "tools/call",
    "params": {"name": "search", "arguments": {"query": query, "max_results": 5}},
}
resp = requests.post(ENDPOINT,
    headers={"Authorization": f"Bearer {ANYSEARCH_API_KEY}", "Content-Type": "application/json"},
    json=payload, timeout=20)
```

Response format: `result.content[0].text` contains the search results as
**Markdown-formatted text** (not structured JSON with title/url/snippet fields).
Parse it as text:

```python
data = resp.json()
result = data.get("result", data)
# result.content = [{"type": "text", "text": "## Search Results (5 results...)..."}]
for item in result["content"]:
    if item.get("type") == "text":
        markdown_text = item["text"]  # use directly as action_result
```

The `extract` tool (for `search_and_extract` branch) uses the same protocol
with `params.name="extract"` and `arguments={"url": url}`. Extract output is
also Markdown text.

**Key lesson**: When a `[SKILL_PRUNED]` skill loses its content, don't guess
the API format. Read the CLI script source (`anysearch_cli.py`) to find the
real endpoint, method, and response structure.
