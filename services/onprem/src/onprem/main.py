import asyncio
import logging
import random
import signal
import sys
import time
from importlib.metadata import version
from pathlib import Path

import yaml
from robotcell_protocol import SiteMap, new_id

from .cloud_link import CloudLink
from .config import Settings
from .controller import SiteController
from .db import Database
from .enrollments import EnrollmentStore
from .jobs import JobStore
from .outbox import Outbox
from .robots import ArmTiming, Robot, SimulatedArm, StationLocks
from .safety import SafetyMonitor
from .site_link import SiteLink
from .state import StatePublisher

log = logging.getLogger("onprem")

DAY_MS = 24 * 3600 * 1000


def load_site_map(path: Path) -> SiteMap:
    return SiteMap.model_validate(yaml.safe_load(path.read_text()))


def configure_logging(level: str) -> None:
    logging.basicConfig(
        level=level,
        format="%(asctime)s %(levelname)-7s %(name)s %(message)s",
        stream=sys.stdout,
    )
    logging.getLogger("aiosqlite").setLevel(logging.WARNING)


async def serve(settings: Settings) -> int:
    site_map = load_site_map(settings.site_map_path)
    db = Database(settings.db_path)
    await db.open()
    outbox = Outbox(db)
    await outbox.prune(keep_ms=DAY_MS)
    store = JobStore(db, outbox)
    publisher = StatePublisher()

    locks = StationLocks(site_map.stations)
    rng = random.Random(settings.sim_seed)
    timing = ArmTiming(
        speed_mps=settings.arm_speed_mps,
        grip_s=settings.grip_s,
        max_runtime_s=settings.max_job_runtime_s,
    )
    robots: dict[str, Robot] = {}
    for spec in site_map.robots:
        arm = SimulatedArm(
            spec,
            site_map.stations,
            locks,
            timing=timing,
            failure_rate=settings.failure_rate,
            rng=rng,
        )
        robots[spec.id] = Robot(
            spec,
            arm,
            store,
            on_change=publisher.mark,
            heartbeat_timeout_s=settings.robot_heartbeat_timeout_s,
        )

    def resync() -> None:
        publisher.mark_all(robots)

    controller = SiteController(
        store,
        EnrollmentStore(db),
        outbox,
        robots,
        max_queue=settings.max_queue,
        on_resync=resync,
    )
    await controller.recover()

    safety = SafetyMonitor(
        site_map.cells,
        robots.values(),
        resume_after_s=settings.safety_resume_after_s,
        feed_timeout_s=settings.safety_feed_timeout_s,
        classes=settings.safety_classes,
    )
    cloud = CloudLink(
        settings,
        controller,
        outbox,
        boot_id=new_id(),
        version=version("robotcell-onprem"),
        on_connected=resync,
    )
    site = SiteLink(settings, safety, robots)

    workers = [
        cloud.run(),
        cloud.drain_outbox(),
        cloud.heartbeat(),
        publisher.run(lambda robot_id: robots[robot_id].snapshot(), cloud.publish_robot),
        site.run(),
        safety.watchdog(),
    ]
    for robot in robots.values():
        workers += [robot.run(), robot.supervise(), robot.arm.heartbeat()]
    tasks = [asyncio.create_task(worker) for worker in workers]

    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, stop.set)
    stopper = asyncio.create_task(stop.wait())
    log.info("site %s up with robots %s", settings.site_id, ", ".join(robots))

    done, _ = await asyncio.wait([*tasks, stopper], return_when=asyncio.FIRST_COMPLETED)
    exit_code = 0
    if stopper not in done:
        exit_code = 1
        for task in done:
            if task.exception() is not None:
                log.error("worker crashed", exc_info=task.exception())
    else:
        await _drain(settings, robots, outbox)
    await cloud.announce_offline()

    stopper.cancel()
    for task in tasks:
        task.cancel()
    await asyncio.gather(*tasks, return_exceptions=True)
    await db.close()
    log.info("stopped")
    return exit_code


async def _drain(settings: Settings, robots: dict[str, Robot], outbox: Outbox) -> None:
    log.info("shutting down, letting running jobs finish")
    for robot in robots.values():
        robot.stop_accepting()
    try:
        await asyncio.wait_for(
            asyncio.gather(*(robot.wait_idle() for robot in robots.values())),
            settings.drain_timeout_s,
        )
    except TimeoutError:
        log.warning("drain timed out, unfinished jobs will be marked interrupted on next start")
    deadline = time.monotonic() + 2.0
    while await outbox.backlog() and time.monotonic() < deadline:
        await asyncio.sleep(0.1)


def main() -> None:
    settings = Settings()
    configure_logging(settings.log_level)
    sys.exit(asyncio.run(serve(settings)))
