import logging
import sqlite3
from collections.abc import Callable

from robotcell_protocol import (
    TERMINAL_STATUSES,
    Enrollment,
    JobStatus,
    JobStatusUpdate,
    RobotSnapshot,
    new_id,
    now_ms,
)

from .config import Settings
from .db import Database
from .errors import Conflict, NotFound
from .hub import Hub
from .schemas import JobCreate, JobDetail, JobEventView, JobView

log = logging.getLogger(__name__)

_TERMINAL = tuple(status.value for status in TERMINAL_STATUSES)


class JobService:
    """The cloud's record of what operators asked for and what the site reported back.

    The cloud only ever writes `pending`. Every later status comes from the site and is
    applied only if its per-job sequence number is newer than what is stored.
    """

    def __init__(
        self,
        db: Database,
        hub: Hub,
        settings: Settings,
        *,
        wake: Callable[[], None],
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._db = db
        self._hub = hub
        self._settings = settings
        self._wake = wake
        self._clock = clock

    async def create(self, body: JobCreate, request_id: str | None) -> tuple[JobView, bool]:
        if request_id and (existing := await self._by_request(request_id)):
            return existing, False
        robot = await self._db.fetchone(
            "SELECT site_id, snapshot FROM robots WHERE robot_id = ?", (body.robot_id,)
        )
        if robot is None:
            raise NotFound(f"unknown robot {body.robot_id}")
        snapshot = RobotSnapshot.model_validate_json(robot["snapshot"])
        if snapshot.enrollment is not Enrollment.ENROLLED:
            raise Conflict(f"{body.robot_id} is not enrolled")

        now = self._clock()
        ttl_s = body.valid_for_s or self._settings.default_job_ttl_s
        job_id = new_id()
        try:
            async with self._db.transaction() as conn:
                await conn.execute(
                    """
                    INSERT INTO jobs (job_id, request_id, site_id, robot_id, pick, place, status,
                                      created_at, updated_at, start_deadline, next_send_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        job_id,
                        request_id,
                        robot["site_id"],
                        body.robot_id,
                        body.pick,
                        body.place,
                        JobStatus.PENDING,
                        now,
                        now,
                        now + ttl_s * 1000,
                        now,
                    ),
                )
                await conn.execute(
                    "INSERT INTO job_events (job_id, seq, status, at, received_at) "
                    "VALUES (?, 0, ?, ?, ?)",
                    (job_id, JobStatus.PENDING, now, now),
                )
        except sqlite3.IntegrityError:
            if request_id and (existing := await self._by_request(request_id)):
                return existing, False
            raise
        view = await self._view(job_id)
        log.info(
            "job created job_id=%s robot=%s %s to %s",
            job_id,
            body.robot_id,
            body.pick,
            body.place,
        )
        self._hub.publish("job", view)
        self._wake()
        return view, True

    async def request_cancel(self, job_id: str) -> JobView:
        current = await self._view(job_id)
        if current.status.is_terminal:
            raise Conflict(f"job is already {current.status}")
        if current.status is JobStatus.RUNNING:
            raise Conflict("a running job cannot be cancelled")
        now = self._clock()
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                UPDATE jobs
                SET cancel_requested_at = COALESCE(cancel_requested_at, ?),
                    cancel_next_at = ?, cancel_attempts = 0, cancel_error = NULL, updated_at = ?
                WHERE job_id = ?
                """,
                (now, now, now, job_id),
            )
        view = await self._view(job_id)
        self._hub.publish("job", view)
        self._wake()
        return view

    async def apply_status(self, update: JobStatusUpdate) -> None:
        now = self._clock()
        async with self._db.transaction() as conn:
            async with conn.execute(
                "SELECT seq, status FROM jobs WHERE job_id = ?", (update.job_id,)
            ) as cursor:
                row = await cursor.fetchone()
            if row is None:
                log.warning("status for unknown job job_id=%s", update.job_id)
                return
            await conn.execute(
                """
                INSERT INTO job_events (job_id, seq, status, reason, at, received_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (job_id, seq) DO NOTHING
                """,
                (update.job_id, update.seq, update.status, update.reason, update.at, now),
            )
            if update.seq <= row["seq"]:
                return
            await conn.execute(
                """
                UPDATE jobs
                SET status = ?, seq = ?, reason = ?, updated_at = ?,
                    cancel_requested_at = CASE WHEN ? THEN NULL ELSE cancel_requested_at END
                WHERE job_id = ?
                """,
                (
                    update.status,
                    update.seq,
                    update.reason,
                    now,
                    update.status.is_terminal,
                    update.job_id,
                ),
            )
        self._hub.publish("job", await self._view(update.job_id))

    async def refuse_cancel(self, job_id: str, reason: str | None) -> None:
        now = self._clock()
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE jobs SET cancel_requested_at = NULL, cancel_error = ?, updated_at = ? "
                "WHERE job_id = ?",
                (reason, now, job_id),
            )
        view = await self._view(job_id)
        self._hub.publish("job", view)
        self._hub.notice("warning", f"Job #{view.id} was not cancelled: {reason}")

    async def recent(self, limit: int = 200) -> list[JobView]:
        rows = await self._db.fetchall("SELECT * FROM jobs ORDER BY id DESC LIMIT ?", (limit,))
        return [JobView.from_row(row) for row in rows]

    async def detail(self, job_id: str) -> JobDetail:
        view = await self._view(job_id)
        events = await self._db.fetchall(
            "SELECT seq, status, reason, at, received_at FROM job_events "
            "WHERE job_id = ? ORDER BY seq",
            (job_id,),
        )
        return JobDetail(
            **view.model_dump(),
            events=[JobEventView.model_validate(dict(row)) for row in events],
        )

    async def open_ids(self, site_id: str) -> list[str]:
        placeholders = ",".join("?" * len(_TERMINAL))
        rows = await self._db.fetchall(
            f"SELECT job_id FROM jobs WHERE site_id = ? AND status != ? "
            f"AND status NOT IN ({placeholders})",
            (site_id, JobStatus.PENDING, *_TERMINAL),
        )
        return [row["job_id"] for row in rows]

    async def _view(self, job_id: str) -> JobView:
        row = await self._db.fetchone("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        if row is None:
            raise NotFound(f"unknown job {job_id}")
        return JobView.from_row(row)

    async def _by_request(self, request_id: str) -> JobView | None:
        row = await self._db.fetchone("SELECT * FROM jobs WHERE request_id = ?", (request_id,))
        return JobView.from_row(row) if row else None
