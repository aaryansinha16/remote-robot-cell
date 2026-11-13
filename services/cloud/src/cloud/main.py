import asyncio
import contextlib
import logging
import sys
from collections.abc import AsyncIterator

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from .api import Services, router
from .bridge import Bridge
from .config import Settings
from .db import Database
from .errors import Conflict, NotFound
from .fleet import FleetService
from .hub import Hub
from .jobs import JobService
from .reconciler import Reconciler


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


def create_app(settings: Settings | None = None, *, connect: bool = True) -> FastAPI:
    settings = settings or Settings()
    configure_logging(settings.log_level)

    @contextlib.asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        db = Database(settings.db_path)
        await db.open()
        hub = Hub()
        wake = asyncio.Event()
        fleet = FleetService(db, hub, settings, wake=wake.set)
        jobs = JobService(db, hub, settings, wake=wake.set)
        reconciler = Reconciler(db, fleet, jobs, settings, wake=wake)
        bridge = Bridge(settings, fleet, jobs, reconciler) if connect else None
        tasks = [asyncio.create_task(reconciler.run())]
        if bridge is not None:
            reconciler.attach(bridge)
            tasks.append(asyncio.create_task(bridge.run()))
        app.state.services = Services(fleet=fleet, jobs=jobs, hub=hub, bridge=bridge)
        app.state.reconciler = reconciler
        try:
            yield
        finally:
            if bridge is not None:
                await bridge.announce_offline()
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            await db.close()

    app = FastAPI(title="Robot Cell Cloud", version="0.1.0", lifespan=lifespan)
    app.include_router(router)

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=404)

    @app.exception_handler(Conflict)
    async def conflict(_: Request, exc: Conflict) -> JSONResponse:
        return JSONResponse({"detail": str(exc)}, status_code=409)

    return app
