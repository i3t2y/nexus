"""
Nexus Honcho launcher shim(2026-08-03)。

用途:HF Space 单进程硬约束下,把 honcho 原生两进程(api FastAPI + deriver worker)
压进单 uvicorn 进程,共用一个 asyncio event loop。0 行 honcho 源码 diff。

机制(源码实证 /tmp/honcho_check/honcho v3.0.11):
  - src/main.py 模块级 `app = FastAPI(lifespan=lifespan)`,`lifespan` 是普通
    `@asynccontextmanager`。shim import `app` 后运行时替换 `app.router.lifespan_context`
    为自定义 lifespan(FastAPI 0.131 支持 `app.router.lifespan_context` 属性)。
  - src/deriver/queue_manager.py:1132 `async def main()` 是纯 asyncio coroutine
    (init_cache try/except 容错 + QueueManager().initialize() polling loop)。
    shim 经 `asyncio.create_task` 压它进同 loop polling,不产新进程。
  - src/db.py:240 `async def init_db()` 跑 `CREATE EXTENSION vector` + `CREATE SCHEMA`
    + `alembic upgrade head`。shim 首启 await 它建表,再起 deriver task + 进 orig lifespan
    (orig lifespan src/main.py:116 validate_embedding_schema 验表必须先建好)。

顺序铁律:provision_db 必先于 deriver task + orig lifespan。
  - init_db 同步 await 完成 ← 建表+extension+schema
  - 再 create_task deriver(立即返 task 对象,polling 在 loop 上跑)
  - 再 await orig_lifespan(yield 阻塞服务 loop;此时表已建,validate_embed 过门)
  - deriver polling 与 fastapi 请求同 loop 协程级并发互不阻塞

shutdown:finally 取消 deriver task + await 收异常(tags except 不裸 except)。
退路:若 `app.router.lifespan_context` 属性运行时替换不通,抛 RuntimeError 让
    HF restart(首版不实现 fallback 重构造 FastAPI,简化优先)。
"""

from __future__ import annotations

import asyncio
import logging
import sys

from contextlib import asynccontextmanager

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(name)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger("honcho.launcher")

# init_db 重试:Neon serverless 首连冷启动可能 transient;tenacity 已 honcho 依赖。
# 不重试 init_db 失败原因:DSN 错/sslmode 缺/Neon 不存在 — 这些重试无用,直接 fail-loud。
# 重试目标:仅 transient 连接抖动。3 次固定 backoff 1s,总至多 ~3s。
_INIT_DB_RETRIES = 3
_INIT_DB_BACKOFF = 1.0


async def _provision_db_with_retry() -> None:
    """init_db 重试 3 次(仅 transient 抖动)。失败 fail-loud 抛出。"""
    from src.db import init_db

    last_exc: Exception | None = None
    for attempt in range(1, _INIT_DB_RETRIES + 1):
        try:
            logger.info("running alembic migrations against Neon (attempt %d/%d)...", attempt, _INIT_DB_RETRIES)
            await init_db()
            logger.info("migrations OK")
            return
        except Exception as exc:  # noqa: BLE001 — fail-loud 收最后一 exc
            last_exc = exc
            logger.warning("init_db attempt %d failed: %s", attempt, exc)
            if attempt < _INIT_DB_RETRIES:
                await asyncio.sleep(_INIT_DB_BACKOFF)
    # 重试用尽 fail-loud(不静默裸奔:表未建 orig lifespan validate_embed 必拒起)
    raise RuntimeError(f"init_db failed after {_INIT_DB_RETRIES} attempts: {last_exc}") from last_exc


async def _start_deriver() -> asyncio.Task:
    """起 deriver polling 作 api 同 loop 的后台 task。"""
    from src.deriver.queue_manager import main as deriver_main

    task = asyncio.create_task(deriver_main(), name="honcho-deriver")
    logger.info("deriver task created on api event loop")
    return task


@asynccontextmanager
async def lifespan(_app):  # noqa: ANN001 — FastAPI 传 app 实例,shim 不用
    """shim lifespan:provision + deriver task + 进 honcho 原生 lifespan。"""
    # ── 1. 建表 + extension + migrate(必须先于 deriver + orig lifespan)──
    await _provision_db_with_retry()

    # ── 2. deriver polling task(同 loop,立即返不阻塞)──
    deriver_task = await _start_deriver()

    try:
        # ── 3. 进 honcho 原生 lifespan(validate_embedding_schema + init_cache + yield)──
        # orig lifespan 的 finally 会 close_cache/clear_external_vector_store/dispose engine。
        # deriver task 在 orig lifespan finally 外取消(本 shim lifespan 的 finally)。
        from src.main import lifespan as orig_lifespan

        async with orig_lifespan(_app):
            yield
    finally:
        # ── 4. shutdown:取消 deriver task(orig lifespan 已退出,deriver 须停)──
        deriver_task.cancel()
        try:
            await deriver_task
        except asyncio.CancelledError:
            logger.info("deriver task cancelled cleanly on shutdown")
        except Exception as exc:  # noqa: BLE001 — 收 deriver polling 自身异常
            logger.warning("deriver task errored on shutdown: %s", exc)


def _inject_lifespan(app) -> bool:
    """运行时替换 app.router.lifespan_context 为 shim lifespan。

    返回 True 成功;False 表示该 FastAPI 版不支持属性替换(需 fallback 重构造)。
    """
    try:
        app.router.lifespan_context = lifespan
        return True
    except (AttributeError, TypeError) as exc:
        logger.error("app.router.lifespan_context 替换失败: %s", exc)
        return False


def main() -> None:
    """入口:import honcho app → 替换 lifespan → uvicorn 监听 7860。"""
    # honcho src 在 /app,Python 默认 sys.path[0] = launcher.py 所在目录(= /app),
    # 但 PYTHONPATH/venv 优先级有时乱,显式插 /app 保 `from src.main import app` 可达。
    if "/app" not in sys.path:
        sys.path.insert(0, "/app")

    import uvicorn

    logger.info("importing honcho app...")
    from src.main import app as honcho_app

    if not _inject_lifespan(honcho_app):
        # 退路未实现(首版简化):直接退让 HF restart。重构造 FastAPI 重 include router
        # 代价大且需手动重注册 add_pagination/CORSMiddleware/exception handlers,后续按需加。
        raise RuntimeError(
            "app.router.lifespan_context 替换不通 — 该 FastAPI 版本不支持,需 fallback 重构造"
        )
    logger.info("lifespan injected → launcher's lifespan(provision+deriver+orig)")

    # HF Space 公网须监听 7860(非官方 8000)。
    uvicorn.run(honcho_app, host="0.0.0.0", port=7860, log_level="info")


if __name__ == "__main__":
    main()
