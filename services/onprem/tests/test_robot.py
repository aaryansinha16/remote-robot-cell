import asyncio
import random
from collections.abc import Callable

from onprem.robots import ArmTiming, SimulatedArm, StationLocks
from robotcell_protocol import (
    JobStatus,
    JobStep,
    PauseInfo,
    PauseReason,
    RobotActivity,
    RobotEnroll,
    RobotTelemetry,
)

from .conftest import SITE, Site, submit
from .test_jobs import wait_for


async def start(site: Site, *robot_ids: str) -> list[asyncio.Task]:
    for robot_id in robot_ids:
        serial = site.robots[robot_id].spec.serial
        await site.controller.handle(RobotEnroll(robot_id=robot_id, serial=serial))
    site.clear_pauses()
    return [asyncio.create_task(site.robots[r].run()) for r in robot_ids]


async def eventually(condition: Callable[[], bool], within: float = 2.0) -> None:
    async def poll():
        while not condition():
            await asyncio.sleep(0.005)

    await asyncio.wait_for(poll(), within)


def record_telemetry(site: Site, robot_id: str) -> list[RobotTelemetry]:
    robot = site.robots[robot_id]
    seen: list[RobotTelemetry] = []
    notify = robot.arm.on_change

    def record(urgent: bool) -> None:
        seen.append(robot.telemetry())
        notify(urgent)

    robot.arm.on_change = record
    return seen


async def test_pause_freezes_progress_and_resume_continues(site):
    tasks = await start(site, "robot-1")
    robot = site.robots["robot-1"]
    try:
        await site.controller.handle(submit("j1"))
        await wait_for(site, "j1", JobStatus.RUNNING)
        await asyncio.sleep(0.03)

        robot.set_pause(PauseInfo(reason=PauseReason.PERSON_IN_CELL, since=0, cells=("cell-1",)))
        frozen = robot.arm.progress
        await asyncio.sleep(0.15)
        assert robot.arm.progress == frozen
        assert robot.snapshot().state == RobotActivity.PAUSED
        assert (await site.store.get("j1")).status == JobStatus.RUNNING

        robot.set_pause(None)
        await wait_for(site, "j1", JobStatus.DONE)
    finally:
        for task in tasks:
            task.cancel()


async def test_paused_robot_keeps_jobs_queued(site):
    tasks = await start(site, "robot-1")
    try:
        site.pause("robot-1", PauseInfo(reason=PauseReason.PERSON_IN_CELL, since=0))
        await site.controller.handle(submit("j1"))
        await asyncio.sleep(0.1)
        assert (await site.store.get("j1")).status == JobStatus.QUEUED
    finally:
        for task in tasks:
            task.cancel()


async def test_both_robots_never_hold_station_c_together():
    locks = StationLocks(SITE.stations)
    inside: set[str] = set()
    most = 0

    async def use_c(owner: str) -> None:
        nonlocal most
        for _ in range(5):
            async with locks.hold("C", owner):
                inside.add(owner)
                most = max(most, len(inside))
                await asyncio.sleep(0.001)
                inside.discard(owner)

    await asyncio.gather(use_c("robot-1"), use_c("robot-2"))
    assert most == 1


async def test_jobs_on_both_robots_through_station_c_all_finish(site):
    tasks = await start(site, "robot-1", "robot-2")
    try:
        await site.controller.handle(submit("a", "robot-1", "A", "C"))
        await site.controller.handle(submit("b", "robot-2", "C", "E"))
        await site.controller.handle(submit("c", "robot-1", "C", "B"))
        for job_id in ("a", "b", "c"):
            await wait_for(site, job_id, JobStatus.DONE)
    finally:
        for task in tasks:
            task.cancel()


async def test_failures_are_reported_with_a_reason(db):
    site = Site(db, failure_rate=1.0)
    tasks = await start(site, "robot-1")
    try:
        await site.controller.handle(submit("j1"))
        await wait_for(site, "j1", JobStatus.FAILED)
    finally:
        for task in tasks:
            task.cancel()

    record = await site.store.get("j1")
    assert record.reason in {"gripper_fault", "part_missing", "path_blocked"}


async def test_arm_carries_the_part_to_the_place_station_and_then_rests(site):
    tasks = await start(site, "robot-1")
    robot = site.robots["robot-1"]
    seen = record_telemetry(site, "robot-1")
    try:
        await site.controller.handle(submit("j1", pick="A", place="C"))
        await wait_for(site, "j1", JobStatus.DONE)
        await eventually(lambda: robot.arm.at_rest)
    finally:
        for task in tasks:
            task.cancel()

    tools = [t.tool for t in seen]
    assert SITE.stations["A"] in tools
    assert SITE.stations["C"] in tools
    carrying = [t for t in seen if t.holding]
    assert carrying
    assert {t.current.step for t in carrying if t.current} <= {JobStep.MOVE_TO_PLACE, JobStep.PLACE}
    assert robot.telemetry().state == RobotActivity.IDLE


async def test_halted_arm_stays_put_on_its_way_back_to_rest():
    timing = ArmTiming(speed_mps=20.0, min_move_s=0.03, tick_s=0.005)
    arm = SimulatedArm(
        SITE.robots[0],
        SITE.stations,
        StationLocks(SITE.stations),
        timing=timing,
        failure_rate=0.0,
        rng=random.Random(1),
    )
    arm.tool = SITE.stations["C"]
    retract = asyncio.create_task(arm.retract())

    await asyncio.sleep(0.05)
    arm.halt()
    frozen = arm.tool
    await asyncio.sleep(0.1)
    assert arm.tool == frozen
    assert not arm.at_rest

    arm.release()
    await asyncio.wait_for(retract, 1.0)
    assert arm.at_rest


async def test_new_job_does_not_wait_for_the_arm_to_reach_rest(site):
    robot = site.robots["robot-1"]
    robot.arm.tool = SITE.stations["C"]
    robot.arm.stalled = True
    tasks = await start(site, "robot-1")
    try:
        await asyncio.sleep(0.05)
        assert not robot.arm.at_rest

        await site.controller.handle(submit("j1", pick="B", place="A"))
        await wait_for(site, "j1", JobStatus.RUNNING)
        assert not robot.arm.at_rest
    finally:
        for task in tasks:
            task.cancel()
