from cloud.schemas import JobCreate
from robotcell_protocol import JobStatusUpdate, SiteStatus

from .conftest import snapshot

BODY = JobCreate(robot_id="robot-1", pick="A", place="B")


async def online(cloud, boot_id="boot-1"):
    if await cloud.fleet.apply_site_status("site-001", SiteStatus(online=True, boot_id=boot_id)):
        await cloud.reconciler.resync("site-001")


async def test_new_job_is_sent_even_while_site_is_offline(cloud):
    await cloud.fleet.apply_robot("site-001", snapshot())
    await cloud.jobs.create(BODY, None)

    await cloud.reconciler.tick()

    assert cloud.broker.kinds() == ["job.submit"]


async def test_pending_job_is_not_resent_while_site_is_offline(cloud, clock):
    await cloud.fleet.apply_robot("site-001", snapshot())
    await cloud.jobs.create(BODY, None)
    await cloud.reconciler.tick()

    clock.now += 600_000
    await cloud.reconciler.tick()

    assert cloud.broker.kinds() == ["job.submit"]


async def test_site_coming_online_triggers_resend_and_sync(cloud, clock):
    await cloud.fleet.apply_robot("site-001", snapshot())
    job, _ = await cloud.jobs.create(BODY, None)
    await cloud.reconciler.tick()

    await online(cloud)
    await cloud.reconciler.tick()

    assert cloud.broker.kinds() == ["job.submit", "sync.request", "job.submit"]
    assert cloud.broker.sent[-1].job_id == job.job_id


async def test_resend_backs_off_until_the_site_answers(cloud, clock):
    await online(cloud)
    await cloud.fleet.apply_robot("site-001", snapshot())
    job, _ = await cloud.jobs.create(BODY, None)

    await cloud.reconciler.tick()
    clock.now += 1_000
    await cloud.reconciler.tick()
    clock.now += 1_500
    await cloud.reconciler.tick()
    assert cloud.broker.kinds().count("job.submit") == 2

    await cloud.jobs.apply_status(
        JobStatusUpdate(job_id=job.job_id, robot_id="robot-1", status="received", seq=1, at=1)
    )
    clock.now += 600_000
    await cloud.reconciler.tick()
    assert cloud.broker.kinds().count("job.submit") == 2


async def test_failed_publish_is_retried_without_counting_an_attempt(cloud):
    await cloud.fleet.apply_robot("site-001", snapshot())
    job, _ = await cloud.jobs.create(BODY, None)
    cloud.broker.up = False
    await cloud.reconciler.tick()

    cloud.broker.up = True
    await cloud.reconciler.tick()

    assert cloud.broker.kinds() == ["job.submit"]
    assert (await cloud.jobs.detail(job.job_id)).send_attempts == 1


async def test_enrollment_request_is_sent_with_the_reported_serial(cloud):
    await online(cloud)
    await cloud.fleet.apply_robot("site-001", snapshot(enrolled=False))

    await cloud.fleet.request_enrollment("robot-1", enroll=True)
    await cloud.reconciler.tick()

    enroll = cloud.broker.sent[-1]
    assert (enroll.kind, enroll.serial) == ("robot.enroll", "SER-robot-1")

    await cloud.fleet.apply_robot("site-001", snapshot(enrolled=True))
    assert (await cloud.fleet.robot("robot-1")).request is None
