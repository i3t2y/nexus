"""Hermes 主控。

一个进程双用：Gradio Dashboard(监听7860, HF要求) + FastAPI 路由 API。
- GET  /health          存活探测（保活/唤醒）
- POST /run             提交任务路由到下游 Space
- GET  /state/{tid}     查任务状态
- POST /run_sync         Gradio UI 提交（同 /run，供前端按钮）
- 文件管理 Tab:        R2 文件 上传/下载/编辑/刷新

设计：HF Space 必须单进程监听 7860。Gradio 5 可挂到 FastAPI 上同端口。
底层存储走 shared libs/storage(R2+Supabase)、libs/gateway(调下游Space)。
"""
from __future__ import annotations

import os
from typing import Any

import gradio as gr
from fastapi import FastAPI, Header
from pydantic import BaseModel

# 共享库（构建前已同步到本 Space 目录 libs/）
from storage import load_state, log_task, save_state
from storage import enqueue_task, claim_task, complete_task, load_task
from storage import r2_client
from shared.gateway import call_space
from shared.errors import new_request_id, raise_nexus_error, log_event

# Hermes Agent 内核(NousResearch hermes-agent,editable 装在 base 镜像 /opt/hermes-agent)。
# 路径 B 主执行路径:_do_run 无 force_space 时交 agent_server.run_agent_once 智能决策调下游。
# 相对 import:main.py 作 app.main(由 uvicorn --app-dir /data 解析),agent_server 是 app/ 同包兄弟。
from .agent_server import run_agent_once

# ── FastAPI 路由层 ──────────────────────────────────────────────────
api = FastAPI(title="Hermes")
_API_KEY = os.getenv("NEXUS_API_KEY", "")
_SPACE = "hermes"


def auth(authorization: str | None, request_id: str) -> None:
    """统一鉴权，fail-closed。

    生产：NEXUS_API_KEY 必填，缺失或不对 → 拒绝（缺 key 是配置错误，而非放行理由）。
    本地开发想免鉴权：设 NEXUS_AUTH_MODE=dev（明确开关，不靠“忘配 key”侥幸放行）。
    /health 不经本函数（保活探测需放行）。
    失败统一走 raise_nexus_error，带 request_id 便于客户端串联排障。
    """
    if os.getenv("NEXUS_AUTH_MODE") == "dev":
        return
    if not _API_KEY:
        log_event(request_id, _SPACE, "auth", "error", reason="NEXUS_API_KEY 未配置")
        raise_nexus_error("config_error", "NEXUS_API_KEY 未配置（生产必填；本地免鉴权设 NEXUS_AUTH_MODE=dev）", 500, request_id)
    if authorization != f"Bearer {_API_KEY}":
        log_event(request_id, _SPACE, "auth", "error", reason="bad credential")
        raise_nexus_error("unauthorized", "鉴权失败", 401, request_id)


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "space": "hermes"}


@api.get("/state/{thread_id}")
async def get_state(
    thread_id: str,
    x_nexus_key: str | None = Header(None),
    authorization: str | None = Header(None),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """查任务/Agent 状态（agent_states 表）。无记录返 404。"""
    rid = new_request_id(x_request_id)
    auth(x_nexus_key or authorization, rid)
    state = load_state(thread_id)
    if state is None:
        log_event(rid, _SPACE, "get_state", "not_found", thread_id=thread_id)
        raise_nexus_error("not_found", f"thread {thread_id} 无状态记录", 404, rid)
    return {"thread_id": thread_id, "state": state, "request_id": rid}


# ── 异步任务队列（task_queue，带幂等键）───────────────────────────
@api.post("/enqueue")
async def enqueue(
    body: RunBody,
    x_nexus_key: str | None = Header(None),
    authorization: str | None = Header(None),
    idempotency_key: str | None = Header(None, alias="Idempotency-Key"),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """入队任务（异步模式）。带 `Idempotency-Key` header 实现幂等：同键重复命中已有，不重复执行。

    路由决策同 /run；区别是不立即调下游，写 task_queue(queued)，由 /dequeue 消费。
    """
    rid = new_request_id(x_request_id)
    auth(x_nexus_key or authorization, rid)
    import uuid

    thread_id = str(uuid.uuid4())
    # 路由决策:有 force → 该 lane(路 A);无 force → "agent" 占位,
    #   由 /dequeue 认领后交 Hermes Agent 智能决策(路 B)。
    #   (原 route() 关键词分发致异步链结构性永不可达 agent 路——闭合此落差。)
    if body.force_space and body.force_space in _KEYWORDS:
        space = body.force_space
    else:
        space = "agent"
    created = enqueue_task(thread_id, space, {"prompt": body.prompt}, idempotency_key)
    log_task(thread_id, "hermes", f"enqueue→{space}", "queued", rid)
    try:
        save_state(thread_id, {"prompt": body.prompt, "space": space, "phase": "queued"})
    except Exception as e:  # noqa: BLE001
        log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(e), phase="queued")
    log_event(rid, _SPACE, "enqueue", "queued", thread_id=thread_id, target=space)
    # 幂等命中：返回已入队信息（不暴露既有 thread_id 避免越权；调用方靠 idempotency_key 查状态）
    if not created and idempotency_key:
        return {"enqueued": False, "idempotency_key": idempotency_key, "space": space, "hint": "已有同键任务，用 Idempotency-Key + GET /task 查状态", "request_id": rid}
    return {"enqueued": True, "task_id": thread_id, "space": space, "request_id": rid}


@api.post("/dequeue")
async def dequeue(
    x_nexus_key: str | None = Header(None),
    authorization: str | None = Header(None),
    space: str | None = None,
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """认领一条 queued 任务 → 调下游 → 完成。单消费者模板实现。

    `space` query 可限某 lane。无任务返 `{"idle": True}`。
    """
    rid = new_request_id(x_request_id)
    auth(x_nexus_key or authorization, rid)
    job = claim_task(space)
    if job is None:
        return {"idle": True, "request_id": rid}
    tid = job["thread_id"]
    target_space = job["space"]
    prompt = (job.get("payload") or {}).get("prompt", "")
    log_task(tid, "hermes", f"dequeue→{target_space}", "claimed", rid)
    log_event(rid, _SPACE, "dequeue", "claimed", thread_id=tid, target=target_space)
    # 异步路闭合:space="agent"(无 force 入队)→ 交 Hermes Agent 内核(路 B),不再
    #   困老 route() 关键词 lane。其余 space → 透传 call_space 调下游(路 A 一致)。
    if target_space == "agent":
        try:
            result = await run_agent_once(prompt, tid, request_id=rid)
            phase = "interrupted" if (result.get("final_response") and not result.get("completed")) else "done"
            complete_task(tid, {"result": result}, status="done")
            try:
                save_state(tid, {"phase": phase, "mode": "agent", "completed": result.get("completed"),
                                 "final_response": result.get("final_response"), "tokens": result.get("tokens")})
            except Exception as se:  # noqa: BLE001
                log_event(rid, _SPACE, "save_state", "error", thread_id=tid, err=str(se), phase=phase)
            log_task(tid, "hermes", "agent", "done", rid)
            log_event(rid, _SPACE, "agent", "done", thread_id=tid,
                      completed=result.get("completed"), tokens=result.get("tokens", {}).get("total"))
            out = dict(result); out.setdefault("space", None)
            out["status"] = phase
            out["request_id"] = rid
            return out
        except Exception as e:  # noqa: BLE001
            complete_task(tid, {"error": str(e)}, status="error")
            try:
                save_state(tid, {"phase": "error", "err": str(e), "mode": "agent"})
            except Exception as se:  # noqa: BLE001
                log_event(rid, _SPACE, "save_state", "error", thread_id=tid, err=str(se), phase="error")
            log_task(tid, "hermes", "agent", "error", rid)
            log_event(rid, _SPACE, "agent", "error", thread_id=tid, err=str(e))
            return {"task_id": tid, "space": None, "status": "error", "error": str(e), "request_id": rid}
    try:
        # 透传 request_id 给下游 Space，全链路同 rid 串联
        result = await call_space(target_space, _target_path(target_space), {"thread_id": tid, "prompt": prompt}, request_id=rid)
        complete_task(tid, {"result": result}, status="done")
        try:
            save_state(tid, {"phase": "done", "downstream": target_space, "result": result})
        except Exception as se:  # noqa: BLE001
            log_event(rid, _SPACE, "save_state", "error", thread_id=tid, err=str(se), phase="done")
        log_task(tid, target_space, "invoke", "done", rid)
        log_event(rid, target_space, "invoke", "done", thread_id=tid)
        return {"task_id": tid, "space": target_space, "status": "done", "result": result, "request_id": rid}
    except Exception as e:  # noqa: BLE001
        complete_task(tid, {"error": str(e)}, status="error")
        try:
            save_state(tid, {"phase": "error", "err": str(e)})
        except Exception as se:  # noqa: BLE001
            log_event(rid, _SPACE, "save_state", "error", thread_id=tid, err=str(se), phase="error")
        log_task(tid, target_space, "invoke", "error", rid)
        log_event(rid, target_space, "invoke", "error", thread_id=tid, err=str(e))
        return {"task_id": tid, "space": target_space, "status": "error", "error": str(e), "request_id": rid}


@api.get("/task/{thread_id}")
async def get_task(
    thread_id: str,
    x_nexus_key: str | None = Header(None),
    authorization: str | None = Header(None),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    """查 task_queue 一行（queued/claimed/done/error）。无返 404。"""
    rid = new_request_id(x_request_id)
    auth(x_nexus_key or authorization, rid)
    row = load_task(thread_id)
    if row is None:
        log_event(rid, _SPACE, "get_task", "not_found", thread_id=thread_id)
        raise_nexus_error("not_found", f"task {thread_id} 无记录", 404, rid)
    return {"task_id": thread_id, "space": row.get("space"), "status": row.get("status"), "result": row.get("result"), "request_id": rid}


# ── 路由决策 ────────────────────────────────────────────────────────
_KEYWORDS = {
    "langgraph": ["规划", "多步", "工作流", "依赖", "分解", "plan", "workflow"],
    "claude": ["实现", "重构", "调试", "复杂", "implement", "refactor", "debug"],
    "codex": ["补全", "快速", "片段", "complete", "snippet", "fast"],
}


def route(prompt: str, force: str | None) -> str:
    if force and force in _KEYWORDS:
        return force
    for space, kws in _KEYWORDS.items():
        if any(k in prompt.lower() for k in kws):
            return space
    # 默认不走 langgraph（普通任务不该进工作流编排）；
    # 兜底用 claude 通用推理 lane，需编排时显式 force_space=langgraph。
    return "claude"


def _target_path(space: str) -> str:
    return {"langgraph": "/execute", "claude": "/run", "codex": "/complete"}[space]


class RunBody(BaseModel):
    prompt: str
    force_space: str | None = None


@api.post("/run")
async def run(
    body: RunBody,
    x_nexus_key: str | None = Header(None),
    authorization: str | None = Header(None),
    x_request_id: str | None = Header(None, alias="X-Request-ID"),
) -> dict[str, Any]:
    rid = new_request_id(x_request_id)
    auth(x_nexus_key or authorization, rid)
    return await _do_run(body.prompt, body.force_space, request_id=rid)


async def _do_run(prompt: str, force: str | None = None, request_id: str | None = None) -> dict[str, Any]:
    """核心执行，被 HTTP 端点与 Dashboard 共用。

    request_id 由 HTTP 端点传入；Dashboard 直接调时为 None（内部置生成）。透传给下游。

    永续改造后两条路径:
      - force_space 兜底(路 A):收 force 时跳 agent,直按现役 route()+call_space 调下游
        (向后兼容老 dashboard 调用语义 + 显式指派 lane)。
      - 主路径(路 B):无 force 时交 Hermes Agent 内核(agent_server.run_agent_once),
        agent loop 自推理 + 按语义智能决策调 nexus_call_claude/codex/route_langgraph 三 tool
        (注册为 hermes plugin toolset=nexus,桥到 call_space 调下游)。结果回写 agent 记忆。
    """
    import uuid

    rid = request_id or new_request_id(None)
    thread_id = str(uuid.uuid4())

    # ── force 兜底(路 A):显式指派 → 跳 agent 直调下游。
    # 保留现役 route() 关键词分发 + call_space,作 agent 兜底/向后兼容。
    if force and force in _KEYWORDS:
        space = force
        log_task(thread_id, "hermes", f"route→{space}(forced)", "pending", rid)
        try:
            save_state(thread_id, {"prompt": prompt, "space": space, "phase": "dispatched_forced"})
        except Exception as e:  # noqa: BLE001
            log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(e), phase="dispatched_forced")
        log_event(rid, _SPACE, "route", "dispatched", thread_id=thread_id, target=space, mode="forced")
        try:
            result = await call_space(space, _target_path(space), {"thread_id": thread_id, "prompt": prompt}, request_id=rid)
        except Exception as e:  # noqa: BLE001
            log_task(thread_id, space, "invoke", "error", rid)
            try:
                save_state(thread_id, {"phase": "error", "err": str(e)})
            except Exception as se:  # noqa: BLE001
                log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(se), phase="error")
            log_event(rid, space, "invoke", "error", thread_id=thread_id, err=str(e))
            return {"task_id": thread_id, "space": space, "error": str(e), "request_id": rid}
        log_task(thread_id, space, "invoke", "done", rid)
        try:
            save_state(thread_id, {"phase": "done", "downstream": space, "result": result, "mode": "forced"})
        except Exception as e:  # noqa: BLE001
            log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(e), phase="done")
        log_event(rid, space, "invoke", "done", thread_id=thread_id)
        return {"task_id": thread_id, "space": space, "result": result, "request_id": rid}

    # ── 主路径(路 B):Hermes Agent 内核智能决策。
    # save_state 防 Supabase 挂:save_state 是同步 supabase HTTP upsert,裸炸会阻塞
    # uvicorn 单线程 event loop + 冒泡 500 致 agent 根本跑不起来。包 try fail-soft
    # (状态索引是观测面,挂了不该拦 agent 主执行;路由路 A 同理)。
    try:
        save_state(thread_id, {"prompt": prompt, "phase": "agent_running", "mode": "agent"})
    except Exception as e:  # noqa: BLE001
        log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(e), phase="agent_running")
    log_task(thread_id, "hermes", "agent", "pending", rid)
    log_event(rid, _SPACE, "agent", "start", thread_id=thread_id)
    try:
        result = await run_agent_once(prompt, thread_id, request_id=rid)
    except Exception as e:  # noqa: BLE001
        log_task(thread_id, "hermes", "agent", "error", rid)
        try:
            save_state(thread_id, {"phase": "error", "err": str(e), "mode": "agent"})
        except Exception as se:  # noqa: BLE001
            log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(se), phase="error")
        log_event(rid, _SPACE, "agent", "error", thread_id=thread_id, err=str(e))
        return {"task_id": thread_id, "error": str(e), "request_id": rid, "mode": "agent"}

    log_task(thread_id, "hermes", "agent", "done", rid)
    try:
        save_state(thread_id, {
            "phase": "done",
            "mode": "agent",
            "final_response": result.get("final_response"),
            "tokens": result.get("tokens"),
            "completed": result.get("completed"),
        })
    except Exception as e:  # noqa: BLE001
        log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(e), phase="done")
    # 软退化:max_iterations 截断(completed=False)却标 done 误导。区分:
    #   completed=False(如 turn_exit_reason=iteration_limit_reached) → phase=interrupted
    #   让客户端见"未完成"而非伪 done。completed=True 才真 done。
    if not result.get("completed") and result.get("final_response"):
        try:
            save_state(thread_id, {"phase": "interrupted", "completed": False, "mode": "agent"})
        except Exception as e:  # noqa: BLE001
            log_event(rid, _SPACE, "save_state", "error", thread_id=thread_id, err=str(e), phase="interrupted")
        log_event(rid, _SPACE, "agent", "interrupted", thread_id=thread_id,
                  completed=False, tokens=result.get("tokens", {}).get("total"))
    log_event(rid, _SPACE, "agent", "done", thread_id=thread_id,
              completed=result.get("completed"), tokens=result.get("tokens", {}).get("total"))
    # 扁平返(补 space 字段为 None 表 agent 自决,adb 与老客户端查 space 字段仍健在)
    out = dict(result)
    out.setdefault("space", None)
    return out


# ── 文件管理（R2，借鉴 HermesFace/HuggingMes 的 Dashboard 文件操作）─
_R2_BUCKET = os.getenv("R2_ARTIFACTS_BUCKET", "nexus-artifacts")


def _r2():
    return r2_client()


def list_r2_files() -> list[list[str]]:
    try:
        resp = _r2().list_objects_v2(Bucket=_R2_BUCKET)
        return [[o["Key"]] for o in resp.get("Contents", [])]
    except Exception as e:  # noqa: BLE001
        return [[f"(列文件失败: {e})"]]


def upload_r2(file) -> str:
    if file is None:
        return "未选择文件"
    try:
        key = os.path.basename(getattr(file, "name", "upload.bin") or "upload.bin")
        with open(file, "rb") as fp:
            _r2().upload_fileobj(fp, _R2_BUCKET, key)
        return f"✅ 上传成功: {key}"
    except Exception as e:  # noqa: BLE001
        return f"❌ 上传失败: {e}"


def read_r2(filename: str) -> str:
    if not filename:
        return ""
    import botocore.exceptions

    try:
        obj = _r2().get_object(Bucket=_R2_BUCKET, Key=filename)
        body = obj["Body"].read()
        try:
            return body.decode()
        except UnicodeDecodeError:
            return f"(二进制文件, {len(body)} 字节, 无法文本预览)"
    except botocore.exceptions.ClientError as e:
        code = e.response.get("Error", {}).get("Code", "")
        if code in ("NoSuchKey", "404"):
            return f"(文件不存在: {filename})"
        return f"(读取失败: {e})"


def save_r2(filename: str, content: str) -> str:
    if not filename:
        return "请填文件名"
    try:
        _r2().put_object(Bucket=_R2_BUCKET, Key=filename, Body=content.encode())
        return f"✅ 保存成功: {filename}"
    except Exception as e:  # noqa: BLE001
        return f"❌ 保存失败: {e}"


def delete_r2(filename: str) -> str:
    if not filename:
        return "请填文件名"
    try:
        _r2().delete_object(Bucket=_R2_BUCKET, Key=filename)
        return f"🗑️ 已删除: {filename}"
    except Exception as e:  # noqa: BLE001
        return f"❌ 删除失败: {e}"


# ── Dashboard UI ────────────────────────────────────────────────────
def _build_dashboard() -> gr.Blocks:
    with gr.Blocks(title="Nexus Hermes 控制台") as demo:
        gr.Markdown("# 🧠 Nexus Hermes 主控控制台")
        gr.Markdown(
            "HTTP API: `POST /run` · `POST /enqueue`(异步,带`Idempotency-Key`) · "
            "`POST /dequeue` · `GET /state/{id}` · `GET /task/{id}` · `GET /health` · "
            "路由 langgraph/claude/codex"
        )

        with gr.Tab("任务路由"):
            p_in = gr.Textbox(label="任务 prompt", lines=3, placeholder="例：规划一个三步部署流程")
            f_in = gr.Dropdown(["", "langgraph", "claude", "codex"], label="强制目标(可空)", value="")
            run_btn = gr.Button("提交任务")
            run_out = gr.JSON(label="结果")
            run_btn.click(lambda p, f: _do_run_sync(p, f), [p_in, f_in], run_out)

        with gr.Tab("文件管理 (R2)"):
            gr.Markdown(f"Bucket: `{_R2_BUCKET}`")
            file_df = gr.Dataframe(headers=["文件名"], value=list_r2_files, interactive=False)
            refresh_btn = gr.Button("🔄 刷新列表")
            refresh_btn.click(list_r2_files, outputs=file_df)

            with gr.Row():
                up_file = gr.File(label="上传文件")
                up_btn = gr.Button("⬆️ 上传")
                up_out = gr.Textbox(label="上传结果", interactive=False)
                up_btn.click(upload_r2, up_file, up_out)
                up_btn.click(list_r2_files, outputs=file_df)  # 上传后刷新

            with gr.Row():
                name_in = gr.Textbox(label="文件名", placeholder="remote/path.txt")
                load_btn = gr.Button("📥 读入编辑框")
                del_btn = gr.Button("🗑️ 删除")
            edit_box = gr.TextArea(label="文件内容(可编辑)", lines=15)
            save_btn = gr.Button("💾 保存回 R2")
            save_out = gr.Textbox(label="保存结果", interactive=False)

            load_btn.click(read_r2, name_in, edit_box)
            save_btn.click(save_r2, [name_in, edit_box], save_out)
            del_btn.click(delete_r2, name_in, save_out)
            del_btn.click(list_r2_files, outputs=file_df)

        with gr.Tab("系统状态"):
            gr.Markdown("## 组件健康")
            st_out = gr.JSON(label="状态")
            st_btn = gr.Button("检查下游 Space")
            st_btn.click(_ping_all, outputs=st_out)

    return demo


def _do_run_sync(prompt: str, force: str | None) -> Any:
    """Gradio 同步回调桥到 async _do_run。

    Gradio 回调跑在 AnyIO worker 线程,无当前事件循环——
    asyncio.get_event_loop().run_until_complete 在 py3.10+ 此场景抛
    "There is no current event loop in thread"。改 asyncio.run:
    为当前线程新建临时事件循环,单次 run 后清理,AnyIO worker 线程安全。
    """
    import asyncio

    if not prompt:
        return {"error": "prompt 为空"}
    force = force or None
    return asyncio.run(_do_run(prompt, force))


async def _ping_all() -> dict[str, Any]:
    import asyncio

    from shared.gateway import ping

    spaces = ["langgraph", "claude", "codex"]
    res = await asyncio.gather(*[ping(s) for s in spaces], return_exceptions=True)
    return {s: ("ok" if (not isinstance(r, Exception) and r) else "down") for s, r in zip(spaces, res)}


# ── 启动：FastAPI + Gradio 同端口挂载 ───────────────────────────────
def create_app() -> FastAPI:
    demo = _build_dashboard()
    gr.mount_gradio_app(api, demo, path="/")  # Dashboard 挂根路径
    return api


app = create_app()

if __name__ == "__main__":
    import uvicorn

    uvicorn.run("app.main:app", host="0.0.0.0", port=7860)
