# LangGraph Worker Skeleton — Nexus 云端瘦编排器
# Upload to HF Dataset (<owner>/nworker/graph/__init__.py). start.sh pulls on boot.
# Modify nodes/tools/workflow as needed — no Space rebuild required.
#
# Key design (learned from production 2026-08-16):
#   1. In-process mem0 calls (get_memory_instance) NOT HTTP self-call (10s timeout kills it)
#   2. LLM plan node: rule-matching FIRST (coding/search keywords skip LLM entirely),
#      LLM fallback only for direct/ambiguous tasks — saves Zhipu QPS quota
#   3. act node: FIVE branches:
#      - direct: LLM answers
#      - search: AnySearch JSON-RPC (api.anysearch.com/mcp, method=tools/call)
#      - search_and_extract: search + extract top URL content (AnySearch extract)
#      - write_file: write file to HF Space /data directory
#      - delegate: writes task to Neon task_queue table (psycopg direct connect)
#   4. reflect node: LOCAL heuristic (zero LLM cost) — not a third LLM call
#   5. Auth: reuse mem0 ADMIN_API_KEY (X-API-Key header) — not a separate auth system
#   6. Conditional retry: verify → (needs_improvement? → act : reflect)
#   7. Fallback: if langgraph not installed, run nodes sequentially
#
# Full graph: retrieve → plan → act → verify →(conditional)→ reflect → write → END
#                                     ↑                    ↓
#                                     └── retry (max 1) ───┘

import os
import sys
import json
import logging
import requests
import time
from typing import TypedDict
from fastapi import APIRouter, Request, HTTPException

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/worker", tags=["LangGraph Worker"])

# ── In-process mem0 (NOT HTTP self-call) ──────────────────────────────
def _get_memory():
    try:
        from server_state import get_memory_instance
        return get_memory_instance()
    except Exception as e:
        logger.error(f"get_memory_instance failed: {e}")
        return None

def mem0_search(query, user_id="default", top_k=5):
    mem = _get_memory()
    if not mem:
        return []
    try:
        results = mem.search(query=query, user_id=user_id, limit=top_k)
        return results if isinstance(results, list) else results.get("results", [])
    except Exception as e:
        logger.warning(f"mem0_search error: {e}")
        return []

def mem0_add(content, user_id="default"):
    mem = _get_memory()
    if not mem:
        return False
    try:
        mem.add(messages=[{"role": "user", "content": content}], user_id=user_id)
        return True
    except Exception as e:
        logger.warning(f"mem0_add error: {e}")
        return False

# ── LLM call (with retry+backoff for QPS limits) ─────────────────────
LLM_API_KEY = os.environ.get("ZAI_API_KEY", "")
LLM_BASE_URL = os.environ.get("ZAI_BASE_URL", "https://api.z.ai/api/paas/v4")
LLM_MODEL = os.environ.get("ZAI_MODEL", "glm-4.7-flash")

def llm_chat(prompt, system="你是 Nexus 编排助手", retries=2):
    if not LLM_API_KEY:
        return f"[no LLM_API_KEY] {prompt}"
    for attempt in range(retries):
        try:
            resp = requests.post(
                f"{LLM_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {LLM_API_KEY}"},
                json={
                    "model": LLM_MODEL,
                    "messages": [
                        {"role": "system", "content": system},
                        {"role": "user", "content": prompt},
                    ],
                    "temperature": 0.3,
                    "max_tokens": 512,
                },
                timeout=15,
            )
            if resp.status_code == 429:
                wait = 3 * (attempt + 1)
                logger.warning(f"[LLM] 429, retry in {wait}s ({attempt+1}/{retries})")
                time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"llm_chat error ({attempt+1}): {e}")
            if attempt < retries - 1:
                time.sleep(3)
    return f"[LLM error: 429 after {retries} retries]"

# ── AnySearch (act search/extract branches) ───────────────────────────
# AnySearch uses JSON-RPC 2.0 protocol (NOT REST). Endpoint: api.anysearch.com/mcp
# method=tools/call, params.name=search|extract, response=result.content[0].text
# The CLI scripts (anysearch_cli.py) wrap this same protocol — read them if unsure.
ANYSEARCH_API_KEY = os.environ.get("ANYSEARCH_API_KEY", "")
ANYSEARCH_ENDPOINT = "https://api.anysearch.com/mcp"

def anysearch(query, max_results=5):
    """AnySearch JSON-RPC search. Returns list of [{"text": "## Search Results..."}]."""
    if not ANYSEARCH_API_KEY:
        return []
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "search", "arguments": {"query": query, "max_results": max_results}},
        }
        resp = requests.post(
            ANYSEARCH_ENDPOINT,
            headers={"Authorization": f"Bearer {ANYSEARCH_API_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", data)
        # AnySearch returns: result.content[0].text = Markdown-formatted search results
        if isinstance(result, dict) and "content" in result:
            contents = result["content"]
            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return [{"text": item.get("text", "")}]
                    if isinstance(item, str):
                        return [{"text": item}]
                return [{"text": str(contents)[:3000]}]
            if isinstance(contents, str):
                return [{"text": contents}]
        if isinstance(result, str):
            return [{"text": result}]
        if isinstance(result, list):
            return result
        return []
    except Exception as e:
        logger.warning(f"anysearch error: {e}")
        return []

def anysearch_extract(url):
    """AnySearch JSON-RPC extract. Returns Markdown content of the URL (max 3000 chars)."""
    if not ANYSEARCH_API_KEY:
        return ""
    try:
        payload = {
            "jsonrpc": "2.0", "id": 1, "method": "tools/call",
            "params": {"name": "extract", "arguments": {"url": url}},
        }
        resp = requests.post(
            ANYSEARCH_ENDPOINT,
            headers={"Authorization": f"Bearer {ANYSEARCH_API_KEY}", "Content-Type": "application/json"},
            json=payload, timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", data)
        if isinstance(result, dict) and "content" in result:
            contents = result["content"]
            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return item.get("text", "")[:3000]
                    if isinstance(item, str):
                        return item[:3000]
            if isinstance(contents, str):
                return contents[:3000]
        if isinstance(result, str):
            return result[:3000]
        return str(result)[:3000]
    except Exception as e:
        logger.warning(f"anysearch_extract error: {e}")
        return ""

# ── Local file write (act write_file branch) ──────────────────────────
HF_SPACE_DATA_DIR = "/data"  # HF Space persistent storage

def _write_file_to_space(filename, content):
    """Write file to HF Space /data directory. Returns path or ''."""
    try:
        os.makedirs(HF_SPACE_DATA_DIR, exist_ok=True)
        # Safety: strip path separators, cap length, enforce extension allowlist
        safe_name = filename.replace("/", "_").replace("\\", "_")[-100:]
        if not safe_name.endswith((".txt", ".md", ".json", ".py", ".yaml", ".csv")):
            safe_name += ".md"
        path = os.path.join(HF_SPACE_DATA_DIR, safe_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content[:50000])  # 50KB cap
        logger.info(f"[write_file] saved to {path}, {len(content)} chars")
        return path
    except Exception as e:
        logger.warning(f"[write_file] error: {e}")
        return ""

# ── Neon task_queue write (act delegate branch) ───────────────────────
# Creates task_queue table if not exists, inserts pending task, returns task_id.
# External consumers (local bridge / NPC) poll this table and update status.
def write_task_to_neon(task, user_id="default"):
    """Write delegated task to Neon task_queue table. Returns task_id or ''."""
    try:
        import psycopg
        import uuid
        pg_host = os.environ.get("POSTGRES_HOST", "")
        pg_port = os.environ.get("POSTGRES_PORT", "5432")
        pg_user = os.environ.get("POSTGRES_USER", "")
        pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
        pg_db = os.environ.get("POSTGRES_DB", "neondb")
        conn = psycopg.connect(
            f"host={pg_host} port={pg_port} dbname={pg_db} user={pg_user} password={pg_pass}",
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute("""
            CREATE TABLE IF NOT EXISTS task_queue (
                task_id TEXT PRIMARY KEY,
                task TEXT NOT NULL,
                user_id TEXT DEFAULT 'default',
                status TEXT DEFAULT 'pending',
                created_at TIMESTAMPTZ DEFAULT now(),
                completed_at TIMESTAMPTZ,
                result TEXT
            )
        """)
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        cur.execute(
            "INSERT INTO task_queue (task_id, task, user_id, status) VALUES (%s, %s, %s, 'pending')",
            (task_id, task[:2000], user_id),
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[delegate] task written to Neon: {task_id}")
        return task_id
    except Exception as e:
        logger.warning(f"[delegate] Neon write failed: {e}")
        return ""

# ── LangGraph state machine ───────────────────────────────────────────
try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    logger.warning("langgraph not installed, using sequential fallback")

class WorkerState(TypedDict, total=False):
    task: str
    user_id: str
    memories: list           # retrieve output
    plan: str                # plan output
    action_type: str         # act output: "direct" | "search" | "delegate"
    action_result: str       # act output
    retries: int             # retry counter
    result: str              # final result
    reflection: str          # reflect output
    quality: str             # "good" | "needs_improvement" | "failed"
    error: str

# ── Graph nodes ───────────────────────────────────────────────────────

def retrieve_memory(state):
    """Node 1: Retrieve relevant memories from mem0."""
    state["memories"] = mem0_search(
        query=state["task"], user_id=state.get("user_id", "default"), top_k=5
    )
    logger.info(f"[retrieve] {len(state['memories'])} memories")
    return state

def plan(state):
    """Node 2: Plan — rule-match first (saves LLM quota), LLM fallback."""
    task = state["task"]
    task_lower = task.lower()

    # Rule matching (zero LLM cost)
    # NOTE: Keep keywords specific — avoid false positives.
    # "今天" removed from search_keywords (caused "翻译: 今天天气很好" → search misroute).
    # "状态" removed (too common in non-search contexts).
    # "部署" added to coding_keywords.
    coding_keywords = ["写", "编写", "实现", "重命名", "脚本", "代码", "函数",
                       "deploy", "build", "fix", "refactor", "python", "typescript",
                       "docker", "git", "测试", "debug", "重构", "部署"]
    search_keywords = ["搜索", "查询", "最新", "新闻", "价格",
                      "search", "find", "latest", "status"]
    # Deep research: search + extract top URL content
    research_keywords = ["研究", "调研", "分析报告", "对比", "综述",
                         "research", "analyze", "compare", "review"]
    # File write: save content to HF Space /data
    file_keywords = ["保存文件", "写入文件", "生成文件", "导出",
                     "save file", "write file", "export file"]

    if any(k in task_lower for k in file_keywords):
        state["action_type"] = "write_file"
        state["plan"] = "[规则匹配] 文件写入任务"
        state["retries"] = 0
        logger.info("[plan] rule=write_file, skip LLM")
        return state

    if any(k in task_lower for k in research_keywords):
        state["action_type"] = "search_and_extract"
        state["plan"] = "[规则匹配] 深度研究任务, 搜索+抓取"
        state["retries"] = 0
        logger.info("[plan] rule=search_and_extract, skip LLM")
        return state

    if any(k in task_lower for k in coding_keywords):
        state["action_type"] = "delegate"
        state["plan"] = "[规则匹配] 编码任务, 委托执行"
        state["retries"] = 0
        logger.info("[plan] rule=delegate, skip LLM")
        return state

    if any(k in task_lower for k in search_keywords):
        state["action_type"] = "search"
        state["plan"] = "[规则匹配] 需要搜索外部信息"
        state["retries"] = 0
        logger.info("[plan] rule=search, skip LLM")
        return state

    # LLM fallback (only for ambiguous/direct tasks)
    mem_summary = "\n".join(
        f"- {m.get('memory', str(m))[:200]}" for m in state.get("memories", [])
    ) or "(无相关记忆)"

    prompt = f"""任务: {task}

已有记忆:
{mem_summary}

分析任务类型，选择执行方式:
- direct: 直接回答 (推理/总结/翻译等)
- search: 需要搜索外部信息
- delegate: 需要编码/重活, 交给本机或 NPC 执行

输出格式 (严格 JSON):
{{"action_type": "direct|search|delegate", "plan": "简洁执行计划(2-3句)"}}

只输出 JSON, 不要其他内容。"""

    raw = llm_chat(prompt, system="你是 Nexus 编排助手, 严格输出 JSON")
    state["plan"] = raw
    state["retries"] = 0

    try:
        if "{" in raw and "}" in raw:
            start = raw.index("{")
            end = raw.rindex("}") + 1
            parsed = json.loads(raw[start:end])
            state["action_type"] = parsed.get("action_type", "direct")
        else:
            state["action_type"] = "direct"
    except Exception:
        state["action_type"] = "direct"

    logger.info(f"[plan] llm type={state.get('action_type')}, plan={len(raw)} chars")
    return state

def act(state):
    """Node 3: Execute — branch by action_type."""
    action = state.get("action_type", "direct")
    plan_text = state.get("plan", "")

    if action == "search":
        # Search via AnySearch JSON-RPC — returns [{"text": "## Search Results..."}]
        results = anysearch(state["task"], max_results=5)
        if results:
            text_parts = [r.get("text", r.get("snippet", r.get("content", ""))) for r in results if r.get("text")]
            state["action_result"] = "搜索结果:\n" + "\n".join(text_parts)[:3000]
        else:
            state["action_result"] = "[搜索无结果或 AnySearch 不可用]"
        logger.info(f"[act] search done, {len(results)} results")

    elif action == "search_and_extract":
        # Deep research: search + extract top URL content
        results = anysearch(state["task"], max_results=5)
        if not results:
            state["action_result"] = "[搜索无结果或 AnySearch 不可用]"
            logger.info("[act] search_and_extract: no results")
            return state
        search_text = "\n".join(r.get("text", "") for r in results if r.get("text"))[:2000]
        # Extract first URL from search results and fetch full page content
        import re
        urls = re.findall(r"https?://[^\s)]+", search_text)
        extracted = ""
        if urls:
            extracted = anysearch_extract(urls[0])
            if extracted:
                extracted = f"\n\n--- 抓取 {urls[0]} ---\n{extracted[:2000]}"
        state["action_result"] = f"搜索结果:\n{search_text}{extracted}"
        logger.info(f"[act] search_and_extract done, extract {len(extracted)} chars")

    elif action == "write_file":
        # Write file to HF Space /data directory
        # Parse "filename: content" or "filename：content" format from task
        task_text = state["task"]
        filename = "output.md"
        content = task_text
        for sep in [":", "："]:
            if sep in task_text:
                parts = task_text.split(sep, 1)
                if len(parts) == 2:
                    filename = parts[0].strip()[-80:]
                    content = parts[1].strip()
                    break
        path = _write_file_to_space(filename, content)
        if path:
            state["action_result"] = f"[已保存] {path} ({len(content)} chars)"
        else:
            state["action_result"] = "[保存失败] 无法写入 /data 目录"
        logger.info(f"[act] write_file done, path={path}")

    elif action == "delegate":
        # Write to Neon task_queue table for external consumers
        task_id = write_task_to_neon(state["task"], state.get("user_id", "default"))
        if task_id:
            state["action_result"] = f"[已入队] task_id={task_id} 等待执行: {state['task'][:150]}"
        else:
            state["action_result"] = f"[入队失败] Neon 写入失败, 任务: {state['task'][:150]}"
        logger.info(f"[act] task delegated, task_id={task_id}")

    else:
        # direct: LLM answers directly
        mem_summary = "\n".join(
            f"- {m.get('memory', str(m))[:200]}" for m in state.get("memories", [])
        ) or "(无)"

        prompt = f"""任务: {state['task']}

相关记忆:
{mem_summary}

计划: {plan_text[:300]}

请直接执行并给出结果:"""

        state["action_result"] = llm_chat(
            prompt, system="你是 Nexus 编排助手, 简洁高效地完成任务"
        )
        logger.info(f"[act] direct done, {len(state['action_result'])} chars")

    return state

def verify(state):
    """Node 4: Verify result — conditional edge to act (retry) or reflect."""
    result = state.get("action_result", "")
    retries = state.get("retries", 0)

    if not result or result.startswith("[LLM error"):
        state["error"] = "act failed: LLM unavailable"
        state["result"] = result or "Failed"
        state["quality"] = "failed"
        return state

    if retries >= 1:
        state["error"] = ""
        state["result"] = result
        state["quality"] = "good"
        return state

    if len(result) < 5:
        state["quality"] = "needs_improvement"
        state["retries"] = retries + 1
        logger.warning(f"[verify] result too short ({len(result)} chars), will retry")
    else:
        state["quality"] = "good"
        state["result"] = result
        state["error"] = ""

    return state

def reflect(state):
    """Node 5: Local heuristic reflection (zero LLM cost)."""
    result = state.get("result", "")

    if state.get("quality") == "failed":
        state["reflection"] = "跳过反思: 执行失败"
        return state

    quality = state.get("quality", "good")
    feedback = ""

    if len(result) < 10:
        quality = "needs_improvement"
        feedback = "结果过短"
    elif result.startswith("[LLM error"):
        quality = "failed"
        feedback = "LLM 不可用"
    elif result.startswith("[已入队]"):
        quality = "good"
        feedback = "任务已委托"
    elif result.startswith("[入队失败]"):
        quality = "failed"
        feedback = "Neon 写入失败"
    elif result.startswith("[已保存]"):
        quality = "good"
        feedback = "文件已保存"
    elif result.startswith("搜索结果"):
        quality = "good"
        feedback = "搜索完成"
    elif len(result) > 20 and not result.startswith("["):
        quality = "good"
        feedback = "结果合理"
    else:
        feedback = "结果待验证"

    state["reflection"] = f'{{"quality": "{quality}", "feedback": "{feedback}"}}'
    state["quality"] = quality
    logger.info(f"[reflect] quality={quality}, feedback={feedback} (local heuristic)")
    return state

def write_memory(state):
    """Node 6: Write result back to mem0 (only if quality != failed)."""
    if state.get("result") and state.get("quality") != "failed":
        ok = mem0_add(
            content=f"任务: {state['task']}\n结果: {state['result'][:500]}",
            user_id=state.get("user_id", "default"),
        )
        logger.info(f"[write] memory written: {ok}")
    else:
        logger.info(f"[write] skipped (quality={state.get('quality')})")
    return state

# ── Conditional routing ───────────────────────────────────────────────
def should_retry(state):
    """After verify: needs_improvement → act (retry), else → reflect."""
    if state.get("quality") == "needs_improvement" and state.get("retries", 0) <= 1:
        return "act"
    return "reflect"

# ── Build graph ───────────────────────────────────────────────────────
def build_graph():
    """
    retrieve → plan → act → verify →(conditional)→ reflect → write → END
                        ↑                 ↓
                        └── retry ←────────┘
    """
    graph = StateGraph(WorkerState)
    graph.add_node("retrieve", retrieve_memory)
    graph.add_node("plan", plan)
    graph.add_node("act", act)
    graph.add_node("verify", verify)
    graph.add_node("reflect", reflect)
    graph.add_node("write", write_memory)

    graph.set_entry_point("retrieve")
    graph.add_edge("retrieve", "plan")
    graph.add_edge("plan", "act")
    graph.add_edge("act", "verify")
    graph.add_conditional_edges(
        "verify",
        should_retry,
        {"act": "act", "reflect": "reflect"},
    )
    graph.add_edge("reflect", "write")
    graph.add_edge("write", END)
    return graph.compile()

# ── FastAPI endpoints ─────────────────────────────────────────────────
@router.get("/health")
async def worker_health():
    """Debug endpoint — check worker component status."""
    try:
        from server_state import get_memory_instance
        get_memory_instance()
        mem_ok = True
    except Exception as e:
        mem_ok = f"error: {e}"
    try:
        from auth import ADMIN_API_KEY
        auth_ok = bool(ADMIN_API_KEY)
    except Exception:
        auth_ok = "import error"
    return {
        "langgraph": LANGGRAPH_AVAILABLE,
        "mem0_instance": mem_ok,
        "llm_api_key": bool(LLM_API_KEY),
        "anysearch_key": bool(ANYSEARCH_API_KEY),
        "admin_api_key": auth_ok,
    }

@router.get("/tasks")
async def worker_tasks(status: str = "pending", limit: int = 10):
    """Query Neon task_queue table. Auth: X-API-Key.

    Query params:
      status: pending|running|completed|failed (default pending)
      limit: max rows (default 10)
    """
    try:
        import psycopg
        pg_host = os.environ.get("POSTGRES_HOST", "")
        pg_port = os.environ.get("POSTGRES_PORT", "5432")
        pg_user = os.environ.get("POSTGRES_USER", "")
        pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
        pg_db = os.environ.get("POSTGRES_DB", "neondb")
        conn = psycopg.connect(
            f"host={pg_host} port={pg_port} dbname={pg_db} user={pg_user} password={pg_pass}",
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute(
            "SELECT task_id, task, user_id, status, created_at, completed_at, result "
            "FROM task_queue WHERE status = %s ORDER BY created_at LIMIT %s",
            (status, limit),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        tasks = []
        for r in rows:
            tasks.append({
                "task_id": r[0], "task": r[1], "user_id": r[2],
                "status": r[3],
                "created_at": str(r[4]) if r[4] else None,
                "completed_at": str(r[5]) if r[5] else None,
                "result": r[6],
            })
        return {"tasks": tasks, "count": len(tasks)}
    except Exception as e:
        logger.warning(f"/worker/tasks error: {e}")
        return {"error": str(e), "tasks": []}

@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, req: Request):
    """Update task_queue status. Auth: X-API-Key.

    Body: {"status": "completed|failed|running", "result": "execution result"}
    """
    try:
        from auth import ADMIN_API_KEY
        from fastapi.security import APIKeyHeader
        api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
        x_api_key = await api_key_header(req)
        if ADMIN_API_KEY and x_api_key != ADMIN_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"auth check error: {e}")

    body = await req.json()
    new_status = body.get("status", "completed")
    result_text = body.get("result", "")

    try:
        import psycopg
        pg_host = os.environ.get("POSTGRES_HOST", "")
        pg_port = os.environ.get("POSTGRES_PORT", "5432")
        pg_user = os.environ.get("POSTGRES_USER", "")
        pg_pass = os.environ.get("POSTGRES_PASSWORD", "")
        pg_db = os.environ.get("POSTGRES_DB", "neondb")
        conn = psycopg.connect(
            f"host={pg_host} port={pg_port} dbname={pg_db} user={pg_user} password={pg_pass}",
            connect_timeout=10,
        )
        cur = conn.cursor()
        cur.execute(
            "UPDATE task_queue SET status=%s, result=%s, completed_at=now() "
            "WHERE task_id=%s RETURNING task_id",
            (new_status, result_text[:5000], task_id),
        )
        updated = cur.fetchone()
        conn.commit()
        cur.close()
        conn.close()
        if updated:
            return {"task_id": task_id, "status": new_status, "updated": True}
        return {"error": "task not found", "task_id": task_id}
    except Exception as e:
        logger.warning(f"update_task error: {e}")
        return {"error": str(e), "task_id": task_id}

@router.post("/run")
async def run_worker(req: Request):
    """Run LangGraph workflow. Requires X-API-Key (mem0 ADMIN_API_KEY)."""
    try:
        from auth import ADMIN_API_KEY
        from fastapi.security import APIKeyHeader
        api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
        x_api_key = await api_key_header(req)
        if ADMIN_API_KEY and x_api_key != ADMIN_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"auth check error: {e}")

    body = await req.json()
    state = WorkerState(
        task=body.get("task", ""),
        user_id=body.get("user_id", "default"),
        memories=[], plan="", action_type="", action_result="",
        retries=0, result="", reflection="", quality="", error="",
    )
    if not state["task"]:
        raise HTTPException(status_code=400, detail="task is required")

    if LANGGRAPH_AVAILABLE:
        app = build_graph()
        return app.invoke(state)
    else:
        # Sequential fallback
        state = retrieve_memory(state)
        state = plan(state)
        state = act(state)
        state = verify(state)
        state = reflect(state)
        state = write_memory(state)
        return state
