import pytest
from cloud.errors import Conflict, NotFound
from cloud.schemas import JobCreate
from robotcell_protocol import JobStatus, JobStatusUpdate, SiteStatus

from .conftest import snapshot

BODY = JobCreate(robot_id="robot-1", pick="A", place="B")


async def test_same_idempotency_key_creates_one_job(cloud):
    await cloud.fleet.apply_robot("site-001", snapshot())

    first, created = await cloud.jobs.create(BODY, "key-1")
    again, created_again = await cloud.jobs.create(BODY, "key-1")

    assert created and not created_again
    assert again.job_id == first.job_id
    assert len(await cloud.jobs.recent()) == 1


async def test_jobs_need_a_known_enrolled_robot(cloud):
    with pytest.raises(NotFound):
        await cloud.jobs.create(BODY, None)

    await cloud.fleet.apply_robot("site-001", snapshot(enrolled=False))
    with pytest.raises(Conflict):
        await cloud.jobs.create(BODY, None)


async def test_status_updates_apply_in_sequence_order(cloud):
    await cloud.fleet.apply_robot("site-001", snapshot())
    job, _ = await cloud.jobs.create(BODY, None)

    def update(status, seq):
        return JobStatusUpdate(
            job_id=job.job_id, robot_id="robot-1", status=status, seq=seq, at=seq
        )

    await cloud.jobs.apply_status(update(JobStatus.QUEUED, 2))
    await cloud.jobs.apply_status(update(JobStatus.RECEIVED, 1))
    await cloud.jobs.apply_status(update(JobStatus.QUEUED, 2))

    detail = await cloud.jobs.detail(job.job_id)
    assert detail.status == JobStatus.QUEUED
    assert [(e.seq, e.status) for e in detail.events] == [
        (0, JobStatus.PENDING),
        (1, JobStatus.RECEIVED),
        (2, JobStatus.QUEUED),
    ]


async def test_running_and_finished_jobs_cannot_be_cancelled(cloud):
    await cloud.fleet.apply_robot("site-001", snapshot())
    job, _ = await cloud.jobs.create(BODY, None)
    await cloud.jobs.apply_status(
        JobStatusUpdate(job_id=job.job_id, robot_id="robot-1", status="running", seq=3, at=3)
    )

    with pytest.raises(Conflict):
        await cloud.jobs.request_cancel(job.job_id)


async def test_offline_notice_from_previous_boot_is_ignored(cloud):
    await cloud.fleet.apply_site_status("site-001", SiteStatus(online=True, boot_id="new"))

    came_online = await cloud.fleet.apply_site_status(
        "site-001", SiteStatus(online=False, boot_id="old")
    )

    assert not came_online
    assert (await cloud.fleet.site()).online
