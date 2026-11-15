import asyncio
import contextlib
import json
import logging
import math
import random
import sys
import time
from collections.abc import AsyncIterator, Awaitable, Callable
from pathlib import Path
from typing import Self

import aiomqtt
import yaml
from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, model_validator
from robotcell_protocol import (
    ProtocolError,
    RobotFault,
    RobotTelemetry,
    SiteMap,
    decode,
    encode,
    new_id,
    now_ms,
    topics,
)

from .config import Settings
from .journal import Entry, Journal
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


class SiteBus:
    """The simulator's session on the site broker: fusion frames and faults out, arm telemetry
    in."""

    def __init__(self, settings: Settings, world: World, journal: Journal) -> None:
        self._settings = settings
        self._world = world
        self._journal = journal
        self._heard: dict[str, float] = {}
        self._site_live: bool | None = None
        self.connected = False
        self.frames = 0
        self.robots: dict[str, RobotTelemetry] = {}

    def live(self, robot_id: str) -> bool:
        heard = self._heard.get(robot_id)
        return heard is not None and time.monotonic() - heard < self._settings.telemetry_timeout_s

    async def run(self) -> None:
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
                    await client.subscribe(topics.ROBOT_TELEMETRY, qos=0)
                    log.info("publishing fusion/objects at %.1f Hz", self._settings.rate_hz)
                    listener = asyncio.create_task(self._listen(client))
                    try:
                        await self._loop(client)
                    finally:
                        listener.cancel()
                        with contextlib.suppress(asyncio.CancelledError, aiomqtt.MqttError):
                            await listener
            except aiomqtt.MqttError as exc:
                log.warning("site broker unavailable: %s", exc)
            finally:
                self.connected = False
            await asyncio.sleep(1.0)

    async def _listen(self, client: aiomqtt.Client) -> None:
        async for message in client.messages:
            if not isinstance(message.payload, bytes | bytearray):
                continue
            try:
                telemetry = decode(message.payload).message
            except ProtocolError:
                continue
            if isinstance(telemetry, RobotTelemetry):
                self.robots[telemetry.robot_id] = telemetry
                self._heard[telemetry.robot_id] = time.monotonic()
                self._journal.observe_robot(telemetry)

    async def _loop(self, client: aiomqtt.Client) -> None:
        tick = 1.0 / self._settings.step_hz
        frame_every = 1.0 / self._settings.rate_hz
        last = next_frame = time.monotonic()
        while True:
            now = time.monotonic()
            self._world.step(now - last)
            last = now
            self._journal.observe_people(self._world.actors)
            self._watch_site()
            for robot_id in list(self._world.dirty_faults):
                await client.publish(
                    topics.sim_robot_fault(robot_id),
                    encode(self._world.faults[robot_id]),
                    qos=1,
                    retain=True,
                )
                self._world.dirty_faults.discard(robot_id)
            if now >= next_frame:
                next_frame = max(next_frame + frame_every, now)
                if self._world.feed_on:
                    await client.publish(
                        topics.FUSION_OBJECTS, self._world.frame(now_ms()).to_json(), qos=0
                    )
                    self.frames += 1
            await asyncio.sleep(max(0.0, tick - (time.monotonic() - now)))

    def _watch_site(self) -> None:
        live = any(self.live(robot_id) for robot_id in self._world.faults)
        if self._site_live is not None and live != self._site_live:
            if live:
                self._journal.note("robot", "arm telemetry from the site server is back")
            else:
                self._journal.note("fault", "no arm telemetry from the site server")
        self._site_live = live


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()
    logging.basicConfig(
        level=settings.log_level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )
    site = SiteMap.model_validate(yaml.safe_load(settings.site_map_path.read_text()))
    world = World(site, ambient=settings.ambient_traffic, rng=random.Random())
    journal = Journal(site)
    bus = SiteBus(settings, world, journal)
    boot = new_id()

    @contextlib.asynccontextmanager
    async def lifespan(_: FastAPI) -> AsyncIterator[None]:
        task = asyncio.create_task(bus.run())
        try:
            yield
        finally:
            task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await task

    app = FastAPI(title="Site simulator", lifespan=lifespan)
    app.mount("/static", StaticFiles(directory=STATIC), name="static")

    @app.middleware("http")
    async def revalidate(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("Cache-Control", "no-cache")
        return response

    def view(events: list[Entry]) -> dict[str, object]:
        return {
            "boot": boot,
            "connected": bus.connected,
            "feed_on": world.feed_on,
            "rate_hz": settings.rate_hz,
            "frames": bus.frames,
            "faults": {rid: fault.model_dump() for rid, fault in world.faults.items()},
            "actors": [
                {
                    "gid": actor.gid,
                    "kind": actor.kind,
                    "x": round(actor.position[0], 3),
                    "y": round(actor.position[1], 3),
                    "heading": round(actor.heading, 1),
                    "moving": math.hypot(*actor.velocity) > 0.05,
                    "ambient": actor.ambient,
                    "leaving": actor.vanish_at_end,
                    "cells": site.cells_at(actor.position),
                }
                for actor in world.actors
            ],
            "robots": {
                robot_id: {**telemetry.model_dump(mode="json"), "live": bus.live(robot_id)}
                for robot_id, telemetry in bus.robots.items()
            },
            "events": [entry.as_dict() for entry in events],
        }

    @app.get("/", include_in_schema=False)
    async def index() -> FileResponse:
        return FileResponse(STATIC / "index.html")

    @app.get("/api/site")
    async def site_map() -> dict[str, object]:
        return site.model_dump(mode="json")

    @app.get("/api/world")
    async def state() -> dict[str, object]:
        return {"site": site.model_dump(mode="json"), **view(journal.recent())}

    @app.get("/api/live")
    async def live() -> StreamingResponse:
        async def stream() -> AsyncIterator[str]:
            sent = 0
            while True:
                events = journal.recent(after=sent)
                if events:
                    sent = events[-1].id
                yield f"data: {json.dumps(view(events), separators=(',', ':'))}\n\n"
                await asyncio.sleep(1.0 / settings.view_hz)

        return StreamingResponse(
            stream(), media_type="text/event-stream", headers={"Cache-Control": "no-store"}
        )

    @app.post("/api/people", status_code=201)
    async def spawn(body: SpawnPerson) -> dict[str, int]:
        if body.cell is not None and body.cell not in site.cells:
            raise HTTPException(404, f"unknown cell {body.cell}")
        if body.station is not None and body.station not in site.stations:
            raise HTTPException(404, f"unknown station {body.station}")
        if body.x is not None and body.y is not None:
            target = (body.x, body.y)
            where = f"({body.x:.1f}, {body.y:.1f})"
        else:
            target = world.target_for(cell=body.cell, station=body.station)
            where = body.cell or f"station {body.station}"
        actor = world.spawn_person(target, walk=body.walk)
        journal.note("action", f"person {actor.gid} heading to {where}")
        log.info("person %d heading to (%.1f, %.1f)", actor.gid, *target)
        return {"gid": actor.gid}

    @app.post("/api/people/leave")
    async def leave_all() -> dict[str, int]:
        leaving = world.leave_all()
        if leaving:
            journal.note("action", "everyone heading out")
        return {"leaving": leaving}

    @app.post("/api/people/{gid}/leave")
    async def leave(gid: int) -> dict[str, bool]:
        if not world.leave(gid):
            raise HTTPException(404, f"no movable person {gid}")
        journal.note("action", f"person {gid} heading out")
        return {"leaving": True}

    @app.delete("/api/people/{gid}")
    async def vanish(gid: int) -> dict[str, bool]:
        if not world.remove(gid):
            raise HTTPException(404, f"no movable person {gid}")
        return {"removed": True}

    @app.post("/api/feed")
    async def feed(body: FeedToggle) -> dict[str, bool]:
        if world.feed_on != body.on:
            journal.note("feed", "fusion feed started" if body.on else "fusion feed stopped")
        world.feed_on = body.on
        log.warning("fusion feed %s", "started" if body.on else "stopped")
        return {"feed_on": world.feed_on}

    @app.post("/api/robots/{robot_id}/fault")
    async def fault(robot_id: str, body: FaultBody) -> dict[str, object]:
        before = world.faults.get(robot_id)
        if before is None:
            raise HTTPException(404, f"unknown robot {robot_id}")
        if before.connected != body.connected:
            link = "plugged back in" if body.connected else "unplugged"
            journal.note("fault", f"{robot_id} {link}")
        if before.stalled != body.stalled:
            motion = "stalled" if body.stalled else "moving freely again"
            journal.note("fault", f"{robot_id} {motion}")
        world.set_fault(robot_id, RobotFault(connected=body.connected, stalled=body.stalled))
        return {"robot_id": robot_id, **body.model_dump()}

    return app
