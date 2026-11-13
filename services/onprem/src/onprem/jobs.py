import logging
from collections.abc import Callable
from dataclasses import dataclass

import aiosqlite
from robotcell_protocol import JobStatus, JobStatusUpdate, JobSubmit, can_transition, now_ms

from .db import Database
from .outbox import Outbox

log = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class JobSpec:
    job_id: str
    robot_id: str
    pick: str
    place: str
    start_deadline: int


@dataclass(frozen=True, slots=True)
class JobRecord:
    job_id: str
    robot_id: str | None
    pick: str | None
    place: str | None
    start_deadline: int | None
    status: JobStatus
    seq: int
    reason: str | None
    updated_at: int
    executions: int

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> "JobRecord":
        return cls(
            job_id=row["job_id"],
            robot_id=row["robot_id"],
            pick=row["pick"],
            place=row["place"],
            start_deadline=row["start_deadline"],
            status=JobStatus(row["status"]),
            seq=row["seq"],
            reason=row["reason"],
            updated_at=row["updated_at"],
            executions=row["executions"],
        )

    @property
    def spec(self) -> JobSpec:
        if None in (self.robot_id, self.pick, self.place, self.start_deadline):
            raise ValueError(f"job {self.job_id} has no execution details")
        return JobSpec(self.job_id, self.robot_id, self.pick, self.place, self.start_deadline)  # type: ignore[arg-type]

    def as_update(self) -> JobStatusUpdate:
        return JobStatusUpdate(
            job_id=self.job_id,
            robot_id=self.robot_id,
            status=self.status,
            seq=self.seq,
            at=self.updated_at,
            reason=self.reason,
        )


class JobStore:
    """Durable job state on the site. Every status change also lands in the outbox."""

    def __init__(self, db: Database, outbox: Outbox, clock: Callable[[], int] = now_ms) -> None:
        self._db = db
        self._outbox = outbox
        self._clock = clock

    async def receive(self, cmd: JobSubmit) -> tuple[JobRecord, bool]:
        """Store a submitted job. Returns the record and whether it was new.

        A duplicate delivery changes nothing and re-reports the current status, which is how
        the cloud catches up when an earlier report was lost.
        """
        now = self._clock()
        async with self._db.transaction() as conn:
            cursor = await conn.execute(
                """
                INSERT INTO jobs (job_id, robot_id, pick, place, created_at, start_deadline,
                                  status, seq, received_at, updated_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, 1, ?, ?)
                ON CONFLICT (job_id) DO NOTHING
                """,
                (
                    cmd.job_id,
                    cmd.robot_id,
                    cmd.pick,
                    cmd.place,
                    cmd.created_at,
                    cmd.start_deadline,
                    JobStatus.RECEIVED,
                    now,
                    now,
                ),
            )
            created = cursor.rowcount == 1
            if not created:
                await conn.execute(
                    """
                    UPDATE jobs SET robot_id = ?, pick = ?, place = ?, created_at = ?,
                                    start_deadline = ?
                    WHERE job_id = ? AND robot_id IS NULL
                    """,
                    (
                        cmd.robot_id,
                        cmd.pick,
                        cmd.place,
                        cmd.created_at,
                        cmd.start_deadline,
                        cmd.job_id,
                    ),
                )
            record = await self._load(conn, cmd.job_id)
            await self._outbox.add(conn, record.as_update())
        self._outbox.wake()
        return record, created

    async def cancel_unknown(self, job_id: str) -> JobRecord:
        """Record a cancel that arrived before its job, so the job is born cancelled."""
        now = self._clock()
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO jobs (job_id, status, seq, received_at, updated_at, finished_at)
                VALUES (?, ?, 1, ?, ?, ?)
                ON CONFLICT (job_id) DO NOTHING
                """,
                (job_id, JobStatus.CANCELLED, now, now, now),
            )
            record = await self._load(conn, job_id)
            await self._outbox.add(conn, record.as_update())
        self._outbox.wake()
        return record

    async def transition(
        self,
        job_id: str,
        target: JobStatus,
        *,
        expect: JobStatus,
        reason: str | None = None,
    ) -> JobRecord | None:
        """Compare-and-set a status change. Returns None when the job is no longer in `expect`."""
        if not can_transition(expect, target):
            raise ValueError(f"illegal transition {expect} to {target}")
        now = self._clock()
        async with self._db.transaction() as conn:
            queue_pos = None
            if target is JobStatus.QUEUED:
                async with conn.execute(
                    "SELECT COALESCE(MAX(queue_pos), 0) + 1 FROM jobs"
                ) as cursor:
                    queue_pos = (await cursor.fetchone())[0]
            cursor = await conn.execute(
                """
                UPDATE jobs
                SET status = ?, seq = seq + 1, reason = ?, updated_at = ?,
                    queue_pos = COALESCE(?, queue_pos),
                    started_at = CASE WHEN ? THEN ? ELSE started_at END,
                    finished_at = CASE WHEN ? THEN ? ELSE finished_at END,
                    executions = executions + ?
                WHERE job_id = ? AND status = ?
                """,
                (
                    target,
                    reason,
                    now,
                    queue_pos,
                    target is JobStatus.RUNNING,
                    now,
                    target.is_terminal,
                    now,
                    1 if target is JobStatus.RUNNING else 0,
                    job_id,
                    expect,
                ),
            )
            if cursor.rowcount != 1:
                return None
            record = await self._load(conn, job_id)
            await self._outbox.add(conn, record.as_update())
        self._outbox.wake()
        log.info(
            "job %s job_id=%s robot=%s seq=%d%s",
            target,
            job_id,
            record.robot_id,
            record.seq,
            f" reason={reason}" if reason else "",
        )
        return record

    async def reemit(self, job_id: str) -> bool:
        async with self._db.transaction() as conn:
            async with conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)) as cursor:
                row = await cursor.fetchone()
            if row is None:
                return False
            await self._outbox.add(conn, JobRecord.from_row(row).as_update())
        self._outbox.wake()
        return True

    async def get(self, job_id: str) -> JobRecord | None:
        row = await self._db.fetchone("SELECT * FROM jobs WHERE job_id = ?", (job_id,))
        return JobRecord.from_row(row) if row else None

    async def with_status(self, status: JobStatus) -> list[JobRecord]:
        rows = await self._db.fetchall(
            "SELECT * FROM jobs WHERE status = ? ORDER BY queue_pos, received_at", (status,)
        )
        return [JobRecord.from_row(row) for row in rows]

    @staticmethod
    async def _load(conn: aiosqlite.Connection, job_id: str) -> JobRecord:
        async with conn.execute("SELECT * FROM jobs WHERE job_id = ?", (job_id,)) as cursor:
            row = await cursor.fetchone()
        if row is None:
            raise LookupError(job_id)
        return JobRecord.from_row(row)
