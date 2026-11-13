import random

import pytest
from onprem.controller import SiteController
from onprem.db import Database
from onprem.enrollments import EnrollmentStore
from onprem.jobs import JobStore
from onprem.outbox import Outbox
from onprem.robots import ArmTiming, Robot, SimulatedArm, StationLocks
from robotcell_protocol import JobSubmit, PauseInfo, SiteMap, decode, now_ms

SITE = SiteMap.model_validate(
    {
        "cells": {"cell-1": {"x": [0, 10], "y": [0, 8]}, "cell-2": {"x": [8, 18], "y": [0, 8]}},
        "stations": {"A": [2.5, 6], "B": [2.5, 2], "C": [9, 4], "D": [15.5, 6], "E": [15.5, 2]},
        "robots": [
            {
                "id": "robot-1",
                "serial": "S1",
                "model": "m",
                "cell": "cell-1",
                "base": [5.5, 4],
                "stations": ["A", "B", "C"],
            },
            {
                "id": "robot-2",
                "serial": "S2",
                "model": "m",
                "cell": "cell-2",
                "base": [12.5, 4],
                "stations": ["C", "D", "E"],
            },
        ],
        "aisle": {"y": 11, "x": [-2, 20]},
    }
)

FAST = ArmTiming(speed_mps=200.0, grip_s=0.03, min_move_s=0.03, tick_s=0.005, max_runtime_s=5)


def submit(job_id: str, robot_id: str = "robot-1", pick: str = "A", place: str = "B", **kw):
    now = now_ms()
    return JobSubmit(
        job_id=job_id,
        robot_id=robot_id,
        pick=pick,
        place=place,
        created_at=kw.get("created_at", now),
        start_deadline=kw.get("start_deadline", now + 60_000),
    )


class Site:
    def __init__(self, db: Database, failure_rate: float = 0.0) -> None:
        self.db = db
        self.outbox = Outbox(db)
        self.store = JobStore(db, self.outbox)
        self.enrollments = EnrollmentStore(db)
        locks = StationLocks(SITE.stations)
        rng = random.Random(7)
        self.robots = {
            spec.id: Robot(
                spec,
                SimulatedArm(
                    spec,
                    SITE.stations,
                    locks,
                    timing=FAST,
                    failure_rate=failure_rate,
                    rng=rng,
                ),
                self.store,
                on_change=lambda robot_id, urgent: None,
                heartbeat_timeout_s=3,
            )
            for spec in SITE.robots
        }
        self.controller = SiteController(
            self.store,
            self.enrollments,
            self.outbox,
            self.robots,
            max_queue=3,
            on_resync=lambda: None,
        )

    def clear_pauses(self) -> None:
        for robot in self.robots.values():
            robot.set_pause(None)

    def pause(self, robot_id: str, info: PauseInfo) -> None:
        self.robots[robot_id].set_pause(info)

    async def sent_events(self):
        rows = await self.db.fetchall("SELECT payload FROM outbox ORDER BY id")
        return [decode(row["payload"]).message for row in rows]


@pytest.fixture
async def db(tmp_path):
    database = Database(tmp_path / "onprem.db")
    await database.open()
    yield database
    await database.close()


@pytest.fixture
async def site(db):
    return Site(db)
