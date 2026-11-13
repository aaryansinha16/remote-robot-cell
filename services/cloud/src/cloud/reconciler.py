import asyncio
import contextlib
import logging
from collections.abc import Callable
from typing import Protocol

from robotcell_protocol import (
    TERMINAL_STATUSES,
    JobCancel,
    JobStatus,
    JobSubmit,
    Message,
    RobotEnroll,
    RobotSnapshot,
    RobotUnenroll,
    SyncRequest,
    encode,
    now_ms,
    topics,
)

from .config import Settings
from .db import Database
from .fleet import FleetService
from .jobs import JobService

log = logging.getLogger(__name__)

_TERMINAL = tuple(status.value for status in TERMINAL_STATUSES)


class Publisher(Protocol):
    async def publish(self, topic: str, payload: bytes, *, qos: int = 1) -> bool: ...


class Reconciler:
    """Keeps sending what the cloud wants until the site has answered it.

    The cloud never trusts a broker acknowledgement as proof that the site has a job. A job
    stays pending, and keeps being re-sent with backoff while the site is online, until the
    site reports a status for it. Duplicates are safe because the site dedups by job id.
    """

    def __init__(
        self,
        db: Database,
        fleet: FleetService,
        jobs: JobService,
        settings: Settings,
        *,
        wake: asyncio.Event,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._db = db
        self._fleet = fleet
        self._jobs = jobs
        self._settings = settings
        self._wake = wake
        self._clock = clock
        self._publisher: Publisher | None = None

    def attach(self, publisher: Publisher) -> None:
        self._publisher = publisher

    def kick(self) -> None:
        self._wake.set()

    async def run(self) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._wake.wait(), 1.0)
            self._wake.clear()
            try:
                await self.tick()
            except Exception:
                log.exception("reconcile pass failed")

    async def tick(self) -> None:
        await self._fleet.expire_heartbeats()
        if self._publisher is None:
            return
        online = await self._fleet.online_sites()
        await self._send_jobs(online)
        await self._send_cancels(online)
        await self._send_enrollments(online)

    async def resync(self, site_id: str) -> None:
        """Called when a site (re)appears: resend everything it has not answered yet and ask it
        for the latest status of every job the cloud still considers open."""
        now = self._clock()
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE jobs SET next_send_at = ? WHERE site_id = ? AND status = ?",
                (now, site_id, JobStatus.PENDING),
            )
            await conn.execute(
                "UPDATE jobs SET cancel_next_at = ? "
                "WHERE site_id = ? AND cancel_requested_at IS NOT NULL",
                (now, site_id),
            )
            await conn.execute(
                "UPDATE robots SET request_next_at = ? WHERE site_id = ? AND request IS NOT NULL",
                (now, site_id),
            )
        open_ids = await self._jobs.open_ids(site_id)
        log.info("resyncing site %s, open jobs=%d", site_id, len(open_ids))
        await self._send(site_id, SyncRequest(job_ids=open_ids))
        self.kick()

    async def _send_jobs(self, online: set[str]) -> None:
        rows = await self._db.fetchall(
            "SELECT * FROM jobs WHERE status = ? AND next_send_at <= ? ORDER BY id",
            (JobStatus.PENDING, self._clock()),
        )
        for row in rows:
            if row["send_attempts"] > 0 and row["site_id"] not in online:
                continue
            command = JobSubmit(
                job_id=row["job_id"],
                robot_id=row["robot_id"],
                pick=row["pick"],
                place=row["place"],
                created_at=row["created_at"],
                start_deadline=row["start_deadline"],
            )
            if not await self._send(row["site_id"], command):
                return
            attempts = row["send_attempts"] + 1
            await self._schedule(
                "UPDATE jobs SET send_attempts = ?, next_send_at = ? WHERE job_id = ?",
                attempts,
                row["job_id"],
            )
            if attempts > 1:
                log.info("re-sent job job_id=%s attempt=%d", row["job_id"], attempts)

    async def _send_cancels(self, online: set[str]) -> None:
        placeholders = ",".join("?" * len(_TERMINAL))
        rows = await self._db.fetchall(
            f"SELECT * FROM jobs WHERE cancel_requested_at IS NOT NULL AND cancel_next_at <= ? "
            f"AND status NOT IN ({placeholders}) ORDER BY id",
            (self._clock(), *_TERMINAL),
        )
        for row in rows:
            if row["cancel_attempts"] > 0 and row["site_id"] not in online:
                continue
            if not await self._send(row["site_id"], JobCancel(job_id=row["job_id"])):
                return
            await self._schedule(
                "UPDATE jobs SET cancel_attempts = ?, cancel_next_at = ? WHERE job_id = ?",
                row["cancel_attempts"] + 1,
                row["job_id"],
            )

    async def _send_enrollments(self, online: set[str]) -> None:
        rows = await self._db.fetchall(
            "SELECT * FROM robots WHERE request IS NOT NULL AND request_next_at <= ?",
            (self._clock(),),
        )
        for row in rows:
            if row["request_attempts"] > 0 and row["site_id"] not in online:
                continue
            snapshot = RobotSnapshot.model_validate_json(row["snapshot"])
            command: Message = (
                RobotEnroll(robot_id=snapshot.robot_id, serial=snapshot.serial)
                if row["request"] == "enroll"
                else RobotUnenroll(robot_id=snapshot.robot_id)
            )
            if not await self._send(row["site_id"], command):
                return
            await self._schedule(
                "UPDATE robots SET request_attempts = ?, request_next_at = ? WHERE robot_id = ?",
                row["request_attempts"] + 1,
                row["robot_id"],
            )

    async def _schedule(self, sql: str, attempts: int, key: str) -> None:
        delay_s = min(self._settings.retry_max_s, self._settings.retry_base_s * 2 ** (attempts - 1))
        async with self._db.transaction() as conn:
            await conn.execute(sql, (attempts, self._clock() + int(delay_s * 1000), key))

    async def _send(self, site_id: str, message: Message) -> bool:
        if self._publisher is None:
            return False
        return await self._publisher.publish(topics.commands(site_id), encode(message), qos=1)
