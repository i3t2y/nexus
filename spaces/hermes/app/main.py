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
from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

# 共享库（构建前已同步到本 Space 目录 libs/）
from storage import load_state, log_task, save_state
from storage import enqueue_task, claim_task, complete_task, load_task
from storage import r2_client
from gateway import call_space

# ── FastAPI 路由层 ──────────────────────────────────────────────────
api = FastAPI(title="Hermes")
_API_KEY = os.getenv("NEXUS_API_KEY", "")


def auth(authorization: str | None) -> None:
    """统一鉴权，fail-closed。

    生产：NEXUS_API_KEY 必填，缺失或不对 → 拒绝（缺 key 是配置错误，而非放行理由）。
    本地开发想免鉴权：设 NEXUS_AUTH_MODE=dev（明确开关，不靠“忘配 key”侥幸放行）。
    /health 不经本函数（保活探测需放行）。
    """
    if os.getenv("NEXUS_AUTH_MODE") == "dev":
        return
    if not _API_KEY:
        raise HTTPException(500, "NEXUS_API_KEY 未配置（生产必填；本地免鉴权设 NEXUS_AUTH_MODE=dev）")
    if authorization != f"Bearer {_API_KEY}":
        raise HTTPException(401, "unauthorized")


@api.get("/health")
async def health() -> dict[str, str]:
    return {"status": "ok", "space": "hermes"}


@api.get("/state/{thread_id}")
async def get_state(thread_id: str, x_nexus_key: str | None = Header(None), authorization: str | None = Header(None)) -> dict[str, Any]:
    """查任务/Agent 状态（agent_states 表）。无记录返 404。"""
    auth(x_nexus_key or authorization)
    state = load_state(thread_id)
    if state is None:
        raise HTTPException(404, f"thread {thread_id} 无状态记录")
    return {"thread_id": thread_id, "state": state}


# ── 异步任务队列（task_queue，带幂等键）───────────────────────────
@api.post("/enqueue")
async def enqueue(body: RunBody, x_nexus_key: str | None = Header(None), authorization: str | None = Header(None), idempotency_key: str | None = Header(None, alias="Idempotency-Key")) -> dict[str, Any]:
    """入队任务（异步模式）。带 `Idempotency-Key` header 实现幂等：同键重复命中已有，不重复执行。

    路由决策同 /run；区别是不立即调下游，写 task_queue(queued)，由 /dequeue 消费。
    """
    auth(x_nexus_key or authorization)
    import uuid

    thread_id = str(uuid.uuid4())
    space = route(body.prompt, body.force_space)
    created = enqueue_task(thread_id, space, {"prompt": body.prompt}, idempotency_key)
    log_task(thread_id, "hermes", f"enqueue→{space}", "queued")
    save_state(thread_id, {"prompt": body.prompt, "space": space, "phase": "queued"})
    # 幂等命中：返回已入队信息（不暴露既有 thread_id 避免越权；调用方靠 idempotency_key 查状态）
    if not created and idempotency_key:
        return {"enqueued": False, "idempotency_key": idempotency_key, "space": space, "hint": "已有同键任务，用 Idempotency-Key + GET /task 查状态"}
    return {"enqueued": True, "task_id": thread_id, "space": space}


@api.post("/dequeue")
async def dequeue(x_nexus_key: str | None = Header(None), authorization: str | None = Header(None), space: str | None = None) -> dict[str, Any]:
    """认领一条 queued 任务 → 调下游 → 完成。单消费者模板实现。

    `space` query 可限某 lane。无任务返 `{"idle": True}`。
    """
    auth(x_nexus_key or authorization)
    job = claim_task(space)
    if job is None:
        return {"idle": True}
    tid = job["thread_id"]
    target_space = job["space"]
    prompt = (job.get("payload") or {}).get("prompt", "")
    log_task(tid, "hermes", f"dequeue→{target_space}", "claimed")
    try:
        result = await call_space(target_space, _target_path(target_space), {"thread_id": tid, "prompt": prompt})
        complete_task(tid, {"result": result}, status="done")
        save_state(tid, {"phase": "done", "downstream": target_space, "result": result})
        log_task(tid, target_space, "invoke", "done")
        return {"task_id": tid, "space": target_space, "status": "done", "result": result}
    except Exception as e:  # noqa: BLE001
        complete_task(tid, {"error": str(e)}, status="error")
        save_state(tid, {"phase": "error", "err": str(e)})
        log_task(tid, target_space, "invoke", "error")
        return {"task_id": tid, "space": target_space, "status": "error", "error": str(e)}


@api.get("/task/{thread_id}")
async def get_task(thread_id: str, x_nexus_key: str | None = Header(None), authorization: str | None = Header(None)) -> dict[str, Any]:
    """查 task_queue 一行（queued/claimed/done/error）。无返 404。"""
    auth(x_nexus_key or authorization)
    row = load_task(thread_id)
    if row is None:
        raise HTTPException(404, f"task {thread_id} 无记录")
    return {"task_id": thread_id, "space": row.get("space"), "status": row.get("status"), "result": row.get("result")}


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
async def run(body: RunBody, x_nexus_key: str | None = Header(None), authorization: str | None = Header(None)) -> dict[str, Any]:
    auth(x_nexus_key or authorization)
    return await _do_run(body.prompt, body.force_space)


async def _do_run(prompt: str, force: str | None = None) -> dict[str, Any]:
    """核心执行，被 HTTP 端点与 Dashboard 共用。"""
    import uuid

    thread_id = str(uuid.uuid4())
    space = route(prompt, force)

    log_task(thread_id, "hermes", f"route→{space}", "pending")
    save_state(thread_id, {"prompt": prompt, "space": space, "phase": "dispatched"})

    try:
        result = await call_space(space, _target_path(space), {"thread_id": thread_id, "prompt": prompt})
    except Exception as e:  # noqa: BLE001
        log_task(thread_id, space, "invoke", "error")
        save_state(thread_id, {"phase": "error", "err": str(e)})
        return {"task_id": thread_id, "space": space, "error": str(e)}

    log_task(thread_id, space, "invoke", "done")
    save_state(thread_id, {"phase": "done", "downstream": space, "result": result})
    return {"task_id": thread_id, "space": space, "result": result}


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


async def _do_run_sync(prompt: str, force: str | None) -> Any:
    """Gradio 同步回调桥到 async。"""
    import asyncio

    if not prompt:
        return {"error": "prompt 为空"}
    force = force or None
    return await asyncio.get_event_loop().run_until_complete(_do_run(prompt, force))


async def _ping_all() -> dict[str, Any]:
    import asyncio

    from gateway import ping

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
