import asyncio

from robotcell_protocol import (
    CommandResult,
    JobCancel,
    JobStatus,
    JobStatusUpdate,
    RobotEnroll,
    now_ms,
)

from .conftest import Site, submit


async def enroll(site: Site, robot_id: str = "robot-1") -> None:
    serial = site.robots[robot_id].spec.serial
    await site.controller.handle(RobotEnroll(robot_id=robot_id, serial=serial))


async def statuses(site: Site, job_id: str) -> list[tuple[str, int]]:
    return [
        (event.status.value, event.seq)
        for event in await site.sent_events()
        if isinstance(event, JobStatusUpdate) and event.job_id == job_id
    ]


async def wait_for(site: Site, job_id: str, status: JobStatus, within: float = 3.0) -> None:
    async def poll():
        while (record := await site.store.get(job_id)) is None or record.status != status:
            await asyncio.sleep(0.01)

    await asyncio.wait_for(poll(), within)


async def test_duplicate_submit_is_stored_once_and_reports_current_status(site):
    await enroll(site)

    await site.controller.handle(submit("j1"))
    await site.controller.handle(submit("j1"))

    record = await site.store.get("j1")
    assert record.status == JobStatus.QUEUED
    assert await statuses(site, "j1") == [("received", 1), ("queued", 2), ("queued", 2)]
    assert len(site.robots["robot-1"]._queue) == 1


async def test_unenrolled_robot_rejects(site):
    await site.controller.handle(submit("j1"))

    record = await site.store.get("j1")
    assert (record.status, record.reason) == (JobStatus.REJECTED, "robot_not_enrolled")


async def test_enrollment_checks_serial(site):
    await site.controller.handle(RobotEnroll(robot_id="robot-1", serial="WRONG"))

    events = await site.sent_events()
    assert events[-1] == CommandResult(
        command="robot.enroll", target="robot-1", ok=False, reason="serial_mismatch"
    )
    assert not site.robots["robot-1"].enrolled


async def test_cancel_before_submit_leaves_a_tombstone(site):
    await enroll(site)

    await site.controller.handle(JobCancel(job_id="j1"))
    await site.controller.handle(submit("j1"))

    record = await site.store.get("j1")
    assert record.status == JobStatus.CANCELLED
    assert record.robot_id == "robot-1"
    assert not site.robots["robot-1"]._queue


async def test_cancel_queued_job_removes_it_from_the_queue(site):
    await enroll(site)
    await site.controller.handle(submit("j1"))

    await site.controller.handle(JobCancel(job_id="j1"))

    assert (await site.store.get("j1")).status == JobStatus.CANCELLED
    assert not site.robots["robot-1"]._queue


async def test_job_runs_exactly_once(site):
    await enroll(site)
    site.clear_pauses()
    worker = asyncio.create_task(site.robots["robot-1"].run())
    try:
        await site.controller.handle(submit("j1"))
        await wait_for(site, "j1", JobStatus.DONE)
        await site.controller.handle(submit("j1"))
        await asyncio.sleep(0.2)
    finally:
        worker.cancel()

    record = await site.store.get("j1")
    assert record.status == JobStatus.DONE
    assert record.executions == 1


async def test_running_job_is_not_cancellable(site):
    await enroll(site)
    site.clear_pauses()
    worker = asyncio.create_task(site.robots["robot-1"].run())
    try:
        await site.controller.handle(submit("j1"))
        await wait_for(site, "j1", JobStatus.RUNNING)
        await site.controller.handle(JobCancel(job_id="j1"))
        results = [e for e in await site.sent_events() if isinstance(e, CommandResult)]
    finally:
        worker.cancel()

    assert results[-1].reason == "already_running"


async def test_recovery_fails_interrupted_jobs_and_requeues_the_rest(db):
    first = Site(db)
    await enroll(first)
    await first.controller.handle(submit("running"))
    await first.controller.handle(submit("waiting"))
    await first.store.transition("running", JobStatus.RUNNING, expect=JobStatus.QUEUED)

    restarted = Site(db)
    await restarted.controller.recover()

    interrupted = await restarted.store.get("running")
    assert (interrupted.status, interrupted.reason) == (JobStatus.FAILED, "interrupted")
    assert [job.job_id for job in restarted.robots["robot-1"]._queue] == ["waiting"]
    assert restarted.robots["robot-1"].enrolled


async def test_job_past_its_deadline_is_rejected(site):
    await enroll(site)

    await site.controller.handle(submit("j1", start_deadline=1))

    record = await site.store.get("j1")
    assert (record.status, record.reason) == (JobStatus.REJECTED, "expired")


async def test_job_that_expires_while_waiting_fails_instead_of_starting(site):
    await enroll(site)
    worker = asyncio.create_task(site.robots["robot-1"].run())
    try:
        await site.controller.handle(submit("j1", start_deadline=now_ms() + 50))
        await asyncio.sleep(0.1)
        site.clear_pauses()
        await wait_for(site, "j1", JobStatus.FAILED)
    finally:
        worker.cancel()

    record = await site.store.get("j1")
    assert (record.reason, record.executions) == ("expired", 0)
