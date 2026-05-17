from __future__ import annotations

import contextlib
from contextlib import AsyncExitStack, asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

from agents.earendil.agent import Earendil
from agents.finrod.agent import Finrod
from agents.sauron.agent import Sauron
from agents.tombombadil.agent import TomBombadil
from api.routes import agents as agents_routes
from api.routes import cron as cron_routes
from api.routes import health as health_routes
from api.routes import memory as memory_routes
from api.routes import query as query_routes
from api.routes import tasks as tasks_routes
from core.config import settings
from core.logging import get_logger
from core.redis_client import get_redis_async, get_redis_sync

log = get_logger("api.main")

API_TITLE = "ARDA"
API_VERSION = "0.3.0"


async def _make_checkpointer(stack: AsyncExitStack):
    """Sauron's LangGraph checkpointer.

    Dev/test (mock LLM) uses an in-process MemorySaver so the test
    suite stays file-free and fast. Production gets a durable
    AsyncSqliteSaver so Sauron's thread_id cross-turn memory actually
    survives restarts. The saver's lifetime is bound to `stack` (the
    app lifespan's AsyncExitStack).
    """
    if settings.use_mock_llm:
        return MemorySaver()

    db_path = Path(settings.checkpointer_db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    checkpointer = await stack.enter_async_context(
        AsyncSqliteSaver.from_conn_string(str(db_path))
    )
    await checkpointer.setup()
    return checkpointer


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        get_redis_sync().ping()
        log.info("redis_connected")
    except Exception as e:
        log.warning("redis_unreachable", exception=str(e))

    async with AsyncExitStack() as stack:
        checkpointer = await _make_checkpointer(stack)

        earendil = Earendil()
        finrod = Finrod()
        tombombadil = TomBombadil()
        sauron = Sauron(
            specialists={
                "earendil": earendil,
                "finrod": finrod,
                "tombombadil": tombombadil,
            },
            checkpointer=checkpointer,
        )

        app.state.sauron = sauron
        app.state.earendil = earendil
        app.state.finrod = finrod
        app.state.tombombadil = tombombadil
        log.info(
            "agents_registered",
            agents=["sauron", "earendil", "finrod", "tombombadil"],
            checkpointer=type(checkpointer).__name__,
        )

        yield

    with contextlib.suppress(Exception):
        await get_redis_async().aclose()


def create_app() -> FastAPI:
    app = FastAPI(title=API_TITLE, version=API_VERSION, lifespan=lifespan)
    app.include_router(health_routes.router)
    app.include_router(tasks_routes.router)
    app.include_router(agents_routes.router)
    app.include_router(memory_routes.router)
    app.include_router(query_routes.router)
    app.include_router(cron_routes.router)
    return app


app = create_app()
