from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI

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
from core.logging import get_logger
from core.redis_client import get_redis_async, get_redis_sync

log = get_logger("api.main")

API_TITLE = "ARDA"
API_VERSION = "0.3.0"


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        get_redis_sync().ping()
        log.info("redis_connected")
    except Exception as e:
        log.warning("redis_unreachable", exception=str(e))

    earendil = Earendil()
    finrod = Finrod()
    tombombadil = TomBombadil()
    sauron = Sauron(specialists={
        "earendil": earendil,
        "finrod": finrod,
        "tombombadil": tombombadil,
    })

    app.state.sauron = sauron
    app.state.earendil = earendil
    app.state.finrod = finrod
    app.state.tombombadil = tombombadil
    log.info("agents_registered", agents=["sauron", "earendil", "finrod", "tombombadil"])

    yield

    try:
        await get_redis_async().aclose()
    except Exception:
        pass


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
