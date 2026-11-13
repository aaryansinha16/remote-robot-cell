import logging
from collections.abc import Callable

from robotcell_protocol import (
    CommandRefusal,
    CommandResult,
    FailReason,
    JobCancel,
    JobStatus,
    JobSubmit,
    Message,
    RejectReason,
    RobotEnroll,
    RobotUnenroll,
    SyncRequest,
    now_ms,
)

from .enrollments import EnrollmentStore
from .jobs import JobRecord, JobStore
from .outbox import Outbox
from .policy import evaluate
from .robots import Robot

log = logging.getLogger(__name__)


class SiteController:
    """Where the site decides. The cloud only ever asks; every answer comes from here."""

    def __init__(
        self,
        store: JobStore,
        enrollments: EnrollmentStore,
        outbox: Outbox,
        robots: dict[str, Robot],
        *,
        max_queue: int,
        on_resync: Callable[[], None],
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._store = store
        self._enrollments = enrollments
        self._outbox = outbox
        self._robots = robots
        self._max_queue = max_queue
        self._on_resync = on_resync
        self._clock = clock

    async def recover(self) -> None:
        """Bring the site back to a consistent state after a restart.

        A job that was running when the process died may have moved the arm halfway, so it is
        failed rather than run again. Queued jobs go back into their robot's queue in order.
        """
        for robot_id, serial in (await self._enrollments.enrolled()).items():
            robot = self._robots.get(robot_id)
            if robot is not None and robot.spec.serial == serial:
                robot.set_enrolled(True)

        for record in await self._store.with_status(JobStatus.RUNNING):
            await self._store.transition(
                record.job_id,
                JobStatus.FAILED,
                expect=JobStatus.RUNNING,
                reason=FailReason.INTERRUPTED,
            )

        for record in await self._store.with_status(JobStatus.RECEIVED):
            await self._decide(record)

        restored = 0
        for record in await self._store.with_status(JobStatus.QUEUED):
            robot = self._robots.get(record.robot_id or "")
            if robot is None:
                await self._store.transition(
                    record.job_id,
                    JobStatus.FAILED,
                    expect=JobStatus.QUEUED,
                    reason=RejectReason.UNKNOWN_ROBOT,
                )
                continue
            robot.enqueue(record.spec)
            restored += 1
        if restored:
            log.info("restored %d queued jobs", restored)

    async def handle(self, message: Message) -> None:
        match message:
            case JobSubmit():
                await self._submit(message)
            case JobCancel():
                await self._cancel(message)
            case RobotEnroll():
                await self._enroll(message)
            case RobotUnenroll():
                await self._unenroll(message)
            case SyncRequest():
                await self._sync(message)
            case _:
                log.warning("ignoring unexpected command type=%s", message.kind)

    async def _submit(self, cmd: JobSubmit) -> None:
        record, created = await self._store.receive(cmd)
        if not created:
            log.info("duplicate job ignored job_id=%s status=%s", cmd.job_id, record.status)
            return
        log.info(
            "job received job_id=%s robot=%s %s to %s",
            cmd.job_id,
            cmd.robot_id,
            cmd.pick,
            cmd.place,
        )
        await self._decide(record)

    async def _decide(self, record: JobRecord) -> None:
        spec = record.spec
        robot = self._robots.get(spec.robot_id)
        reason = evaluate(
            spec,
            robot.view() if robot else None,
            now=self._clock(),
            max_queue=self._max_queue,
        )
        if reason is not None:
            await self._store.transition(
                record.job_id, JobStatus.REJECTED, expect=JobStatus.RECEIVED, reason=reason
            )
            return
        queued = await self._store.transition(
            record.job_id, JobStatus.QUEUED, expect=JobStatus.RECEIVED
        )
        if queued is not None and robot is not None:
            robot.enqueue(queued.spec)

    async def _cancel(self, cmd: JobCancel) -> None:
        record = await self._store.get(cmd.job_id)
        if record is None:
            await self._store.cancel_unknown(cmd.job_id)
            log.info("cancel arrived before job, recorded tombstone job_id=%s", cmd.job_id)
            return
        if record.status in (JobStatus.RECEIVED, JobStatus.QUEUED):
            cancelled = await self._store.transition(
                cmd.job_id, JobStatus.CANCELLED, expect=record.status
            )
            if cancelled is not None:
                robot = self._robots.get(record.robot_id or "")
                if robot is not None:
                    robot.remove(cmd.job_id)
                return
            record = await self._store.get(cmd.job_id) or record
        refusal = (
            CommandRefusal.ALREADY_RUNNING
            if record.status is JobStatus.RUNNING
            else CommandRefusal.ALREADY_FINISHED
        )
        await self._reply(cmd, cmd.job_id, refusal)
        await self._store.reemit(cmd.job_id)

    async def _enroll(self, cmd: RobotEnroll) -> None:
        robot = self._robots.get(cmd.robot_id)
        if robot is None:
            await self._reply(cmd, cmd.robot_id, CommandRefusal.UNKNOWN_ROBOT)
            return
        if robot.spec.serial != cmd.serial:
            log.warning(
                "enrollment refused robot=%s expected_serial=%s got=%s",
                cmd.robot_id,
                robot.spec.serial,
                cmd.serial,
            )
            await self._reply(cmd, cmd.robot_id, CommandRefusal.SERIAL_MISMATCH)
            return
        await self._enrollments.enroll(cmd.robot_id, cmd.serial)
        robot.set_enrolled(True)
        log.info("robot enrolled robot=%s serial=%s", cmd.robot_id, cmd.serial)
        await self._reply(cmd, cmd.robot_id)

    async def _unenroll(self, cmd: RobotUnenroll) -> None:
        robot = self._robots.get(cmd.robot_id)
        if robot is None:
            await self._reply(cmd, cmd.robot_id, CommandRefusal.UNKNOWN_ROBOT)
            return
        if robot.busy:
            await self._reply(cmd, cmd.robot_id, CommandRefusal.ROBOT_BUSY)
            return
        await self._enrollments.unenroll(cmd.robot_id)
        robot.set_enrolled(False)
        log.info("robot unenrolled robot=%s", cmd.robot_id)
        await self._reply(cmd, cmd.robot_id)

    async def _sync(self, cmd: SyncRequest) -> None:
        found = 0
        for job_id in cmd.job_ids:
            found += await self._store.reemit(job_id)
        log.info("sync requested jobs=%d known=%d", len(cmd.job_ids), found)
        self._on_resync()

    async def _reply(
        self, cmd: Message, target: str, refusal: CommandRefusal | None = None
    ) -> None:
        await self._outbox.emit(
            CommandResult(command=cmd.kind, target=target, ok=refusal is None, reason=refusal)
        )
