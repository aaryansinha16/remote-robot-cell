import asyncio
import contextlib
import logging
import random
import sys
import time
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Self

import aiomqtt
import yaml
from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, model_validator
from robotcell_protocol import RobotFault, SiteMap, encode, now_ms, topics

from .config import Settings
from .world import World

log = logging.getLogger("sitesim")

STATIC = Path(__file__).resolve().parent / "static"


class SpawnPerson(BaseModel):
    cell: str | None = None
    station: str | None = None
    x: float | None = None
    y: float | None = None
    walk: bool = True

    @model_validator(mode="after")
    def _one_target(self) -> Self:
        if self.cell is None and self.station is None and (self.x is None or self.y is None):
            raise ValueError("give a cell, a station, or both x and y")
        return self


class FeedToggle(BaseModel):
    on: bool


class FaultBody(BaseModel):
    connected: bool = True
    stalled: bool = False


class Publisher:
    def __init__(self, settings: Settings, world: World) -> None:
        self._settings = settings
        self._world = world
        self.connected = False
        self.frames = 0

    async def run(self) -> None:
        period = 1.0 / self._settings.rate_hz
        while True:
            try:
                async with aiomqtt.Client(
                    self._settings.site_broker_host,
                    self._settings.site_broker_port,
                    identifier="site-sim",
                    keepalive=5,
                ) as client:
                    self.connected = True
                    self._world.dirty_faults.update(self._world.faults)
                    log.info("publishing fusion/objects at %.1f Hz", self._settings.rate_hz)
                    await self._loop(client, period)
            except aiomqtt.MqttError as exc:
                log.warning("site broker unavailable: %s", exc)
            finally:
                self.connected = False
            await asyncio.sleep(1.0)

    async def _loop(self, client: aiomqtt.Client, period: float) -> None:
        last = time.monotonic()
        while True:
            now = time.monotonic()
            self._world.step(now - last)
            last = now
            for robot_id in list(self._world.dirty_faults):
                await client.publish(
                    topics.sim_robot_fault(robot_id),
                    encode(self._world.faults[robot_id]),
                    qos=1,
                    retain=True,
                )
                self._world.dirty_faults.discard(robot_id)
            if self._world.feed_on:
                await client.publish(
                    topics.FUSION_OBJECTS, self._world.frame(now_ms()).to_json(), qos=0
                )
                self.frames += 1
            await asyncio.sleep(max(0.0, period - (time.monotonic() - now)))


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )
    site = SiteMap.model_validate(yaml.safe_load(settings.site_map_path.read_text()))
    world = World(site, ambient=settings.ambient_traffic, rng=random.Random())
    publisher = Publisher(settings, world)

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(publisher.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Site simulator", lifespan=lifespan)

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/world")
    async def state() -> dict[str, object]:
        return {
            "site": site.model_dump(mode="json"),
            "connected": publisher.connected,
            "feed_on": world.feed_on,
            "frames": publisher.frames,
            "faults": {rid: fault.model_dump() for rid, fault in world.faults.items()},
            "actors": [
                {
                    "gid": actor.gid,
                    "kind": actor.kind,
                    "x": round(actor.position[0], 2),
                    "y": round(actor.position[1], 2),
                    "ambient": actor.ambient,
                    "cells": site.cells_at(actor.position),
                }
                for actor in world.actors
            ],
        }

    @app.post("/api/people", status_code=201)
    async def spawn(body: SpawnPerson) -> dict[str, int]:
        if body.cell is not None and body.cell not in site.cells:
            raise HTTPException(404, f"unknown cell {body.cell}")
        if body.station is not None and body.station not in site.stations:
            raise HTTPException(404, f"unknown station {body.station}")
        if body.x is not None and body.y is not None:
            target = (body.x, body.y)
        else:
            target = world.target_for(cell=body.cell, station=body.station)
        actor = world.spawn_person(target, walk=body.walk)
        log.info("person %d heading to (%.1f, %.1f)", actor.gid, *target)
        return {"gid": actor.gid}

    @app.post("/api/people/leave")
    async def leave_all() -> dict[str, int]:
        return {"leaving": world.leave_all()}

    @app.post("/api/people/{gid}/leave")
    async def leave(gid: int) -> dict[str, bool]:
        if not world.leave(gid):
            raise HTTPException(404, f"no movable person {gid}")
        return {"leaving": True}

    @app.delete("/api/people/{gid}")
    async def vanish(gid: int) -> dict[str, bool]:
        if not world.remove(gid):
            raise HTTPException(404, f"no movable person {gid}")
        return {"removed": True}

    @app.post("/api/feed")
    async def feed(body: FeedToggle) -> dict[str, bool]:
        world.feed_on = body.on
        log.warning("fusion feed %s", "started" if body.on else "stopped")
        return {"feed_on": world.feed_on}

    @app.post("/api/robots/{robot_id}/fault")
    async def fault(robot_id: str, body: FaultBody) -> dict[str, object]:
        if robot_id not in world.faults:
            raise HTTPException(404, f"unknown robot {robot_id}")
        world.set_fault(robot_id, RobotFault(connected=body.connected, stalled=body.stalled))
        return {"robot_id": robot_id, **body.model_dump()}

    return app
