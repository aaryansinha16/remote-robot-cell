import asyncio
import logging
import time
from collections import deque
from collections.abc import Callable

from robotcell_protocol import (
    CurrentJob,
    Enrollment,
    FailReason,
    JobStatus,
    PauseInfo,
    PauseReason,
    RobotActivity,
    RobotSnapshot,
    RobotSpec,
    RobotTelemetry,
    now_ms,
)

from ..jobs import JobSpec, JobStore
from ..policy import RobotView
from .arm import JobFailure, SimulatedArm

log = logging.getLogger(__name__)

ChangeListener = Callable[[str, bool], None]


class Robot:
    """One robot as the site sees it: its queue, its safety state and the worker that runs it.

    Jobs start only when the robot is enrolled, reachable and not paused. A robot boots paused
    until the safety monitor has seen the fusion feed.
    """

    def __init__(
        self,
        spec: RobotSpec,
        arm: SimulatedArm,
        store: JobStore,
        *,
        on_change: ChangeListener,
        heartbeat_timeout_s: float,
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self.spec = spec
        self.arm = arm
        self._store = store
        self._on_change = on_change
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._clock = clock

        self.enrolled = False
        self.connected = True
        self._queue: deque[JobSpec] = deque()
        self._current: JobSpec | None = None
        self._accepting = True
        self._wake = asyncio.Event()
        self._idle = asyncio.Event()
        self._idle.set()
        self._pause: PauseInfo | None = PauseInfo(
            reason=PauseReason.AWAITING_SAFETY_FEED, since=clock()
        )
        arm.halt()
        arm.on_change = self._notify

    @property
    def id(self) -> str:
        return self.spec.id

    @property
    def cell(self) -> str:
        return self.spec.cell

    @property
    def pause(self) -> PauseInfo | None:
        return self._pause

    @property
    def busy(self) -> bool:
        return self._current is not None or bool(self._queue)

    def view(self) -> RobotView:
        return RobotView(
            enrolled=self.enrolled,
            stations=frozenset(self.spec.stations),
            queue_length=len(self._queue),
        )

    def enqueue(self, job: JobSpec) -> None:
        self._queue.append(job)
        self._wake.set()
        self._notify(True)

    def remove(self, job_id: str) -> bool:
        for job in self._queue:
            if job.job_id == job_id:
                self._queue.remove(job)
                self._notify(True)
                return True
        return False

    def set_enrolled(self, enrolled: bool) -> None:
        if self.enrolled != enrolled:
            self.enrolled = enrolled
            self._wake.set()
            self._notify(True)

    def set_pause(self, pause: PauseInfo | None) -> None:
        current = self._pause
        if pause is None:
            if current is None:
                return
            self._pause = None
            self.arm.release()
            log.info("robot resumed robot=%s after=%s", self.id, current.reason)
        else:
            if pause.same_cause(current):
                return
            if current is not None and current.reason == pause.reason:
                pause = pause.model_copy(
                    update={"since": current.since, "latency_ms": current.latency_ms}
                )
            self._pause = pause
            self.arm.halt()
            log.warning(
                "robot paused robot=%s reason=%s cells=%s tracks=%s latency_ms=%s",
                self.id,
                pause.reason,
                ",".join(pause.cells) or "-",
                ",".join(map(str, pause.track_ids)) or "-",
                pause.latency_ms,
            )
        self._wake.set()
        self._notify(True)

    def stop_accepting(self) -> None:
        self._accepting = False

    async def wait_idle(self) -> None:
        await self._idle.wait()

    def snapshot(self) -> RobotSnapshot:
        return RobotSnapshot(
            robot_id=self.id,
            serial=self.spec.serial,
            model=self.spec.model,
            cell=self.spec.cell,
            stations=list(self.spec.stations),
            enrollment=Enrollment.ENROLLED if self.enrolled else Enrollment.UNENROLLED,
            connected=self.connected,
            state=self._activity(),
            pause=self._pause,
            current=self._current_job(),
            queue=[job.job_id for job in self._queue],
            waiting_for=self.arm.waiting_for,
            updated_at=self._clock(),
        )

    def telemetry(self) -> RobotTelemetry:
        x, y = self.arm.tool
        return RobotTelemetry(
            robot_id=self.id,
            state=self._activity(),
            tool=(round(x, 3), round(y, 3)),
            holding=self.arm.holding,
            current=self._current_job(),
            waiting_for=self.arm.waiting_for,
            pause=self._pause,
            at=self._clock(),
        )

    def _activity(self) -> RobotActivity:
        if not self.connected:
            return RobotActivity.OFFLINE
        if self._pause is not None:
            return RobotActivity.PAUSED
        if self._current is not None:
            return RobotActivity.BUSY
        return RobotActivity.IDLE

    def _current_job(self) -> CurrentJob | None:
        if self._current is None or self.arm.step is None:
            return None
        return CurrentJob(
            job_id=self._current.job_id,
            pick=self._current.pick,
            place=self._current.place,
            step=self.arm.step,
            progress=round(self.arm.progress, 3),
        )

    async def run(self) -> None:
        while True:
            job = await self._next_job()
            self._idle.clear()
            try:
                await self._execute(job)
            finally:
                self._current = None
                self._idle.set()
                self._notify(True)

    async def supervise(self, interval_s: float = 0.5) -> None:
        while True:
            alive = time.monotonic() - self.arm.last_heartbeat <= self._heartbeat_timeout_s
            if alive != self.connected:
                self.connected = alive
                if alive:
                    log.info("robot heartbeat restored robot=%s", self.id)
                else:
                    log.warning("robot heartbeat lost robot=%s", self.id)
                    if self._current is not None:
                        self.arm.abort(FailReason.ROBOT_DISCONNECTED)
                self._wake.set()
                self._notify(True)
            await asyncio.sleep(interval_s)

    def _can_move(self) -> bool:
        return self.connected and self._pause is None

    def _can_start(self) -> bool:
        return self._accepting and self.enrolled and self._can_move()

    async def _next_job(self) -> JobSpec:
        while not (self._can_start() and self._queue):
            self._wake.clear()
            if self._can_move() and not self.arm.at_rest:
                await self._retract_until_woken()
            else:
                await self._wake.wait()
        job = self._queue.popleft()
        self._notify(True)
        return job

    async def _retract_until_woken(self) -> None:
        retract = asyncio.create_task(self.arm.retract())
        woken = asyncio.create_task(self._wake.wait())
        try:
            await asyncio.wait((retract, woken), return_when=asyncio.FIRST_COMPLETED)
        finally:
            retract.cancel()
            woken.cancel()
            await asyncio.gather(retract, woken, return_exceptions=True)

    async def _execute(self, job: JobSpec) -> None:
        if self._clock() > job.start_deadline:
            await self._store.transition(
                job.job_id, JobStatus.FAILED, expect=JobStatus.QUEUED, reason=FailReason.EXPIRED
            )
            return
        started = await self._store.transition(
            job.job_id, JobStatus.RUNNING, expect=JobStatus.QUEUED
        )
        if started is None:
            return
        self._current = job
        self._notify(True)
        status, reason = JobStatus.DONE, None
        try:
            await self.arm.run(job)
        except JobFailure as failure:
            status, reason = JobStatus.FAILED, failure.reason
        await self._store.transition(job.job_id, status, expect=JobStatus.RUNNING, reason=reason)

    def _notify(self, urgent: bool) -> None:
        self._on_change(self.id, urgent)
