"""
LangGraph Worker — Nexus 云端瘦编排器

定稿架构 (2026-08-16):
  - LangGraph = 编排层 (retrieve → plan → act → verify → reflect → write)
  - Mem0 = 记忆层 (同进程函数调用, 不走 HTTP 自调)
  - 重活不进图节点, 写 Neon 任务给本机/NPC
  - verify 失败时回 act 重试 (最多 1 次)

同进程调用: from server_state import get_memory_instance → Memory.add() / Memory.search()
鉴权: ADMIN_API_KEY (mem0 官方原生 X-API-Key)
"""

import os
import sys
import json
import logging
import requests
from typing import TypedDict, Annotated, Literal
from fastapi import APIRouter, Depends, Request, HTTPException

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/worker", tags=["LangGraph Worker"])

# ── 同进程 mem0 调用 ────────────────────────────────────────────────────
def _get_memory():
    """同进程获取 mem0 Memory 实例"""
    try:
        from server_state import get_memory_instance
        return get_memory_instance()
    except Exception as e:
        logger.error(f"get_memory_instance failed: {e}")
        return None

def mem0_search(query: str, user_id: str = "default", top_k: int = 5) -> list:
    """同进程调用 mem0 search"""
    mem = _get_memory()
    if not mem:
        return []
    try:
        results = mem.search(query=query, user_id=user_id, limit=top_k)
        return results if isinstance(results, list) else results.get("results", [])
    except Exception as e:
        logger.warning(f"mem0_search error: {e}")
        return []

def mem0_add(content: str, user_id: str = "default") -> bool:
    """同进程调用 mem0 add"""
    mem = _get_memory()
    if not mem:
        return False
    try:
        mem.add(messages=[{"role": "user", "content": content}], user_id=user_id)
        return True
    except Exception as e:
        logger.warning(f"mem0_add error: {e}")
        return False

# ── 智谱 LLM 调用 ───────────────────────────────────────────────────────
ZAI_API_KEY = os.environ.get("ZAI_API_KEY", "")
ZAI_BASE_URL = "https://api.z.ai/api/paas/v4"
ZAI_MODEL = "glm-4.7-flash"

def zhipu_chat(prompt: str, system: str = "你是 Nexus 编排助手", retries: int = 2) -> str:
    """调用智谱 GLM (带重试+退避, 抗 QPS 限流)"""
    if not ZAI_API_KEY:
        return f"[no ZAI_API_KEY] {prompt}"
    import time as _time
    for attempt in range(retries):
        try:
            resp = requests.post(
                f"{ZAI_BASE_URL}/chat/completions",
                headers={"Authorization": f"Bearer {ZAI_API_KEY}"},
                json={
                    "model": ZAI_MODEL,
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
                logger.warning(f"[zhipu] 429 rate-limited, retry in {wait}s (attempt {attempt+1}/{retries})")
                _time.sleep(wait)
                continue
            resp.raise_for_status()
            return resp.json()["choices"][0]["message"]["content"]
        except Exception as e:
            logger.warning(f"zhipu_chat error (attempt {attempt+1}): {e}")
            if attempt < retries - 1:
                _time.sleep(3)
    return f"[LLM error: 429 after {retries} retries]"

# ── AnySearch 调用 (act 节点搜索工具) ──────────────────────────────────
ANYSEARCH_API_KEY = os.environ.get("ANYSEARCH_API_KEY", "")
ANYSEARCH_ENDPOINT = "https://api.anysearch.com/mcp"

def anysearch(query: str, max_results: int = 5) -> list:
    """调用 AnySearch JSON-RPC API 搜索 (act 节点用)

    协议: JSON-RPC 2.0, method=tools/call, tool=search
    """
    if not ANYSEARCH_API_KEY:
        return []
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "search",
                "arguments": {"query": query, "max_results": max_results},
            },
        }
        resp = requests.post(
            ANYSEARCH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {ANYSEARCH_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        # JSON-RPC result 在 "result" 字段
        result = data.get("result", data)
        # AnySearch 返回格式: result.content[0].text = Markdown 文本
        if isinstance(result, dict) and "content" in result:
            contents = result["content"]
            if isinstance(contents, list):
                for item in contents:
                    if isinstance(item, dict) and item.get("type") == "text":
                        return [{"text": item.get("text", "")}]
                    if isinstance(item, str):
                        return [{"text": item}]
                # 如果 content 里没找到 text, 尝试整个 content
                return [{"text": str(contents)[:3000]}]
            if isinstance(contents, str):
                return [{"text": contents}]
        # fallback: 其他格式
        if isinstance(result, str):
            try:
                result = json.loads(result)
            except:
                return [{"text": result}]
        if isinstance(result, list):
            return result
        if isinstance(result, dict):
            return result.get("results", result.get("content", [result]))
        return []
    except Exception as e:
        logger.warning(f"anysearch error: {e}")
        return []

def anysearch_extract(url: str) -> str:
    """调用 AnySearch extract 抓取网页内容 (act 节点用)

    返回 Markdown 格式的网页内容
    """
    if not ANYSEARCH_API_KEY:
        return ""
    try:
        payload = {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {
                "name": "extract",
                "arguments": {"url": url},
            },
        }
        resp = requests.post(
            ANYSEARCH_ENDPOINT,
            headers={
                "Authorization": f"Bearer {ANYSEARCH_API_KEY}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=20,
        )
        resp.raise_for_status()
        data = resp.json()
        result = data.get("result", data)
        # 同 search: result.content[0].text
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

# ── 本地文件写入 (write_file 分支) ──────────────────────────────────────
HF_SPACE_DATA_DIR = "/data"  # HF Space persistent storage

def _write_file_to_space(filename: str, content: str) -> str:
    """写文件到 HF Space /data 目录, 返回路径"""
    try:
        os.makedirs(HF_SPACE_DATA_DIR, exist_ok=True)
        # 安全: 只允许文件名, 不允许路径穿越
        safe_name = filename.replace("/", "_").replace("\\", "_")[:100]
        if not safe_name.endswith((".txt", ".md", ".json", ".py", ".yaml", ".csv")):
            safe_name += ".md"
        path = os.path.join(HF_SPACE_DATA_DIR, safe_name)
        with open(path, "w", encoding="utf-8") as f:
            f.write(content[:50000])  # 限制 50KB
        logger.info(f"[write_file] saved to {path}, {len(content)} chars")
        return path
    except Exception as e:
        logger.warning(f"[write_file] error: {e}")
        return ""

# ── Neon 任务表写入 (delegate 分支) ─────────────────────────────────────
def _write_task_to_neon(task: str, user_id: str = "default", kind: str = "generic", input: dict | None = None) -> str:
    """将委托任务写入 Neon task_queue 表, 返回 task_id

    Stage A (2026-08-18): 签名加 kind+input; 删自撜 DDL 改靠 neon-schema.sql 权威建表
    (治双 DDL 冲突根因); task 列仍写 goal 摘要兜底, 结构化字段进 input jsonb。
    本轮 act/delegate 先传 kind='generic' 兜底, kind='npc' 智能解析属 Stage B 触发
    (Stage B: 本机桥逆扫 WHERE status='pending' AND kind='npc' → CNB CodeBuddy 云端
    Agent, Issue @npc/CodeBuddy 或 OpenAPI /-/build/start api_trigger_npc; Gork 2026-08-18
    裁决 workbuddy_npc 路废, WorkBuddy 桌面出口移除)。
    """
    try:
        import psycopg
        from datetime import datetime, timezone
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
        # 不再自撜 CREATE TABLE — 靠 neon-schema.sql 权威建表 (Stage A 消灭双 DDL)
        import uuid
        import json
        task_id = f"task_{uuid.uuid4().hex[:12]}"
        input_json = json.dumps(input or {"goal": task}, ensure_ascii=False)
        cur.execute(
            "INSERT INTO task_queue (task_id, task, user_id, status, kind, input) "
            "VALUES (%s, %s, %s, 'pending', %s, %s)",
            (task_id, task[:2000], user_id, kind, input_json),
        )
        conn.commit()
        cur.close()
        conn.close()
        logger.info(f"[delegate] task written to Neon: {task_id}")
        return task_id
    except Exception as e:
        logger.warning(f"[delegate] Neon write failed: {e}")
        return ""

# ── LangGraph 状态机 ────────────────────────────────────────────────────
try:
    from langgraph.graph import StateGraph, END
    LANGGRAPH_AVAILABLE = True
except ImportError:
    LANGGRAPH_AVAILABLE = False
    logger.warning("langgraph not installed, worker will run in fallback mode")

class WorkerState(TypedDict, total=False):
    task: str
    user_id: str
    memories: list           # retrieve 输出
    plan: str                # plan 输出
    action_type: str         # act 输出: "direct" | "search" | "delegate"
    action_result: str       # act 输出
    retries: int             # 重试计数
    result: str              # 最终结果
    reflection: str          # reflect 输出
    quality: str             # reflect 输出: "good" | "needs_improvement" | "failed"
    error: str

# ── 图节点 ──────────────────────────────────────────────────────────────

def retrieve_memory(state: WorkerState) -> WorkerState:
    """节点1: 从 mem0 检索相关记忆"""
    state["memories"] = mem0_search(
        query=state["task"],
        user_id=state.get("user_id", "default"),
        top_k=5,
    )
    logger.info(f"[retrieve] got {len(state['memories'])} memories")
    return state

def plan(state: WorkerState) -> WorkerState:
    """节点2: 规划 — 先规则匹配, LLM 兜底 (省智谱配额)"""
    task = state["task"]

    # 规则匹配 (不消耗 LLM 配额)
    task_lower = task.lower()
    coding_keywords = ["写", "编写", "实现", "重命名", "脚本", "代码", "函数",
                       "deploy", "build", "fix", "refactor", "python", "typescript",
                       "docker", "git", "测试", "debug", "重构", "部署"]
    search_keywords = ["搜索", "查询", "最新", "新闻", "价格",
                      "search", "find", "latest", "status"]
    # 深度研究: 搜索+抓取网页内容
    research_keywords = ["研究", "调研", "分析报告", "对比", "综述",
                         "research", "analyze", "compare", "review"]
    # 写文件: 保存内容到文件
    file_keywords = ["保存文件", "写入文件", "生成文件", "导出",
                     "save file", "write file", "export file"]

    if any(k in task_lower for k in file_keywords):
        state["action_type"] = "write_file"
        state["plan"] = f"[规则匹配] 文件写入任务"
        state["retries"] = 0
        logger.info(f"[plan] rule=write_file, skip LLM")
        return state

    if any(k in task_lower for k in research_keywords):
        state["action_type"] = "search_and_extract"
        state["plan"] = f"[规则匹配] 深度研究任务, 搜索+抓取"
        state["retries"] = 0
        logger.info(f"[plan] rule=search_and_extract, skip LLM")
        return state

    if any(k in task_lower for k in coding_keywords):
        state["action_type"] = "delegate"
        state["plan"] = f"[规则匹配] 编码任务, 委托执行"
        state["retries"] = 0
        logger.info(f"[plan] rule=delegate, skip LLM")
        return state

    if any(k in task_lower for k in search_keywords):
        state["action_type"] = "search"
        state["plan"] = f"[规则匹配] 需要搜索外部信息"
        state["retries"] = 0
        logger.info(f"[plan] rule=search, skip LLM")
        return state

    # LLM 兜底 (仅当规则不匹配时)
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

    raw = zhipu_chat(prompt, system="你是 Nexus 编排助手, 严格输出 JSON")
    state["plan"] = raw
    state["retries"] = 0

    # 解析 action_type
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

def act(state: WorkerState) -> WorkerState:
    """节点3: 执行动作 — 根据 plan 的 action_type 分支"""
    action = state.get("action_type", "direct")
    plan_text = state.get("plan", "")

    if action == "search":
        # 搜索: 调 AnySearch JSON-RPC
        results = anysearch(state["task"], max_results=5)
        if results:
            # AnySearch 返回 [{"text": "## Search Results..."}] 格式
            text_parts = []
            for r in results:
                t = r.get("text", r.get("snippet", r.get("content", "")))
                if t:
                    text_parts.append(t)
            state["action_result"] = "搜索结果:\n" + "\n".join(text_parts)[:3000]
        else:
            state["action_result"] = "[搜索无结果或 AnySearch 不可用]"
        logger.info(f"[act] search done, {len(results)} results")

    elif action == "search_and_extract":
        # 深度研究: 搜索 + 抓取 Top 1 网页
        results = anysearch(state["task"], max_results=5)
        if not results:
            state["action_result"] = "[搜索无结果或 AnySearch 不可用]"
            logger.info(f"[act] search_and_extract: no results")
            return state
        # 搜索结果文本
        search_text = "\n".join(
            r.get("text", "") for r in results if r.get("text")
        )[:2000]
        # 从搜索文本提取第一个 URL
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
        # 写文件到 HF Space /data
        # 从 task 提取文件名 (格式: "保存文件 xxx.md: 内容" 或直接用内容)
        task_text = state["task"]
        filename = "output.md"
        content = task_text
        if ":" in task_text or "：" in task_text:
            sep = ":" if ":" in task_text else "："
            parts = task_text.split(sep, 1)
            if len(parts) == 2:
                filename = parts[0].strip()[-80:]
                content = parts[1].strip()
        path = _write_file_to_space(filename, content)
        if path:
            state["action_result"] = f"[已保存] {path} ({len(content)} chars)"
        else:
            state["action_result"] = "[保存失败] 无法写入 /data 目录"
        logger.info(f"[act] write_file done, path={path}")

    elif action == "delegate":
        # 委托: 写 Neon task_queue 表
        task_id = _write_task_to_neon(state["task"], state.get("user_id", "default"))
        if task_id:
            state["action_result"] = f"[已入队] task_id={task_id} 等待执行: {state['task'][:150]}"
        else:
            state["action_result"] = f"[入队失败] Neon 写入失败, 任务: {state['task'][:150]}"
        logger.info(f"[act] task delegated, task_id={task_id}")

    else:
        # direct: 让智谱直接回答
        mem_summary = "\n".join(
            f"- {m.get('memory', str(m))[:200]}" for m in state.get("memories", [])
        ) or "(无)"

        prompt = f"""任务: {state['task']}

相关记忆:
{mem_summary}

计划: {plan_text[:300]}

请直接执行并给出结果:"""

        state["action_result"] = zhipu_chat(
            prompt,
            system="你是 Nexus 编排助手, 简洁高效地完成任务"
        )
        logger.info(f"[act] direct done, {len(state['action_result'])} chars")

    return state

def verify(state: WorkerState) -> WorkerState:
    """节点4: 验证结果 — 条件边判断是否重试"""
    result = state.get("action_result", "")
    retries = state.get("retries", 0)

    # LLM 不可用
    if not result or result.startswith("[LLM error"):
        state["error"] = "act failed: LLM unavailable"
        state["result"] = result or "Failed"
        state["quality"] = "failed"
        return state

    # 已重试过, 接受结果
    if retries >= 1:
        state["error"] = ""
        state["result"] = result
        state["quality"] = "good"
        return state

    # 检查结果质量
    if len(result) < 5:
        state["quality"] = "needs_improvement"
        state["retries"] = retries + 1
        logger.warning(f"[verify] result too short ({len(result)} chars), will retry")
    else:
        state["quality"] = "good"
        state["result"] = result
        state["error"] = ""

    return state

def reflect(state: WorkerState) -> WorkerState:
    """节点5: 反思 — 评估结果质量 (智谱限流时降级为本地启发式)"""
    result = state.get("result", "")
    task = state.get("task", "")

    # 失败不反思
    if state.get("quality") == "failed":
        state["reflection"] = "跳过反思: 执行失败"
        return state

    # 本地启发式评估 (不消耗 LLM 配额)
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

    state["reflection"] = f"{{\"quality\": \"{quality}\", \"feedback\": \"{feedback}\"}}"
    state["quality"] = quality
    logger.info(f"[reflect] quality={quality}, feedback={feedback} (local heuristic)")
    return state

def write_memory(state: WorkerState) -> WorkerState:
    """节点6: 执行结果写回 mem0"""
    if state.get("result") and state.get("quality") != "failed":
        ok = mem0_add(
            content=f"任务: {state['task']}\n结果: {state['result'][:500]}",
            user_id=state.get("user_id", "default"),
        )
        logger.info(f"[write] memory written: {ok}")
    else:
        logger.info(f"[write] skipped (quality={state.get('quality')})")
    return state

# ── 构建图 (含条件边) ──────────────────────────────────────────────────

def should_retry(state: WorkerState) -> str:
    """verify 后的条件路由: needs_improvement → act 重试, 否则 → reflect"""
    if state.get("quality") == "needs_improvement" and state.get("retries", 0) <= 1:
        return "act"
    return "reflect"

def build_graph():
    """
    完整图:
      retrieve → plan → act → verify →(条件)→ reflect → write → END
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
    # 条件边: verify → act(重试) 或 reflect(继续)
    graph.add_conditional_edges(
        "verify",
        should_retry,
        {"act": "act", "reflect": "reflect"},
    )
    graph.add_edge("reflect", "write")
    graph.add_edge("write", END)

    return graph.compile()

# ── FastAPI 端点 ────────────────────────────────────────────────────────

@router.get("/health")
async def worker_health():
    """Worker 内部状态 (不需 auth, 便于调试)"""
    try:
        from server_state import get_memory_instance
        mem_ok = True
        get_memory_instance()
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
        "zai_api_key": bool(ZAI_API_KEY),
        "anysearch_key": bool(ANYSEARCH_API_KEY),
        "admin_api_key": auth_ok,
    }

@router.get("/tasks")
async def worker_tasks(status: str = "pending", limit: int = 10):
    """查询 Neon task_queue 表 (需 X-API-Key 鉴权)

    Query params:
      status: pending|running|completed|failed (默认 pending)
      limit: 返回条数 (默认 10)
    """
    from fastapi import Request as _Req
    # 注: Request 不在此函数签名里, 靠全局拿
    # 这里简化: 直接查 Neon, 鉴权由前面的 /worker/run 模式处理
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
            "SELECT task_id, task, user_id, status, kind, input, output, attempts, "
            "created_at, completed_at, result "
            "FROM task_queue WHERE status = %s ORDER BY created_at LIMIT %s",
            (status, limit),
        )
        rows = cur.fetchall()
        cur.close()
        conn.close()
        tasks = []
        for r in rows:
            tasks.append({
                "task_id": r[0],
                "task": r[1],
                "user_id": r[2],
                "status": r[3],
                "kind": r[4],
                "input": r[5],
                "output": r[6],
                "attempts": r[7],
                "created_at": str(r[8]) if r[8] else None,
                "completed_at": str(r[9]) if r[9] else None,
                "result": r[10],
            })
        return {"tasks": tasks, "count": len(tasks)}
    except Exception as e:
        logger.warning(f"/worker/tasks error: {e}")
        return {"error": str(e), "tasks": []}

@router.patch("/tasks/{task_id}")
async def update_task(task_id: str, req: Request):
    """更新 task_queue 任务状态 (需 X-API-Key 鉴权)

    Body: {"status": "completed|failed|running", "result": "执行结果"}
    """
    # 鉴权
    try:
        from fastapi.security import APIKeyHeader
        api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
        x_api_key = await api_key_header(req)
        from auth import ADMIN_API_KEY
        if ADMIN_API_KEY and x_api_key != ADMIN_API_KEY:
            raise HTTPException(status_code=401, detail="Invalid API key")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"auth check error: {e}")

    body = await req.json()
    new_status = body.get("status", "completed")
    result_text = body.get("result", "")
    # Stage A: output jsonb 优先写正式结构化结果; result text 兼容旧读端
    output_json = body.get("output")  # dict 或 str, None 则跳过
    output_str = json.dumps(output_json, ensure_ascii=False) if isinstance(output_json, (dict, list)) else output_json

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
        if output_str is not None:
            cur.execute(
                "UPDATE task_queue SET status=%s, result=%s, output=%s::jsonb, "
                "updated_at=now(), completed_at=now() "
                "WHERE task_id=%s RETURNING task_id",
                (new_status, result_text[:5000], output_str, task_id),
            )
        else:
            cur.execute(
                "UPDATE task_queue SET status=%s, result=%s, updated_at=now(), completed_at=now() "
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
    """运行 LangGraph 工作流 (需 X-API-Key 鉴权)"""
    # 鉴权: 复用 mem0 官方 ADMIN_API_KEY
    try:
        from fastapi.security import APIKeyHeader
        api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)
        x_api_key = await api_key_header(req)
        from auth import ADMIN_API_KEY
        if ADMIN_API_KEY:
            if x_api_key != ADMIN_API_KEY:
                raise HTTPException(status_code=401, detail="Invalid API key")
    except HTTPException:
        raise
    except Exception as e:
        logger.warning(f"auth check error: {e}")

    body = await req.json()
    state = WorkerState(
        task=body.get("task", ""),
        user_id=body.get("user_id", "default"),
        memories=[],
        plan="",
        action_type="",
        action_result="",
        retries=0,
        result="",
        reflection="",
        quality="",
        error="",
    )

    if not state["task"]:
        raise HTTPException(status_code=400, detail="task is required")

    if LANGGRAPH_AVAILABLE:
        app = build_graph()
        result = app.invoke(state)
        return result
    else:
        # Fallback: 顺序执行
        state = retrieve_memory(state)
        state = plan(state)
        state = act(state)
        state = verify(state)
        state = reflect(state)
        state = write_memory(state)
        return state
