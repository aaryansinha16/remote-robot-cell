import asyncio
import logging
import random
import time
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass

from robotcell_protocol import FailReason, JobStep, Point, RobotSpec, distance

from ..jobs import JobSpec
from .locks import StationLocks

log = logging.getLogger(__name__)

_FAILURES_BY_STEP: dict[JobStep, tuple[FailReason, ...]] = {
    JobStep.MOVE_TO_PICK: (FailReason.PATH_BLOCKED,),
    JobStep.PICK: (FailReason.PART_MISSING, FailReason.GRIPPER_FAULT),
    JobStep.MOVE_TO_PLACE: (FailReason.PATH_BLOCKED,),
    JobStep.PLACE: (FailReason.GRIPPER_FAULT,),
}

_MOVES = {JobStep.MOVE_TO_PICK, JobStep.MOVE_TO_PLACE}


class JobFailure(Exception):
    def __init__(self, reason: FailReason) -> None:
        super().__init__(reason.value)
        self.reason = reason


@dataclass(frozen=True, slots=True)
class ArmTiming:
    speed_mps: float = 1.5
    grip_s: float = 0.8
    min_move_s: float = 0.6
    tick_s: float = 0.05
    max_runtime_s: float = 30.0


@dataclass(frozen=True, slots=True)
class _PlannedFailure:
    step: JobStep
    reason: FailReason


class SimulatedArm:
    """Stand-in for a robot controller: runs a pick and place cycle in small motion ticks.

    A halt takes effect on the next tick, so the arm stops within `tick_s`. Time spent halted
    does not count against the runtime limit, and a resumed cycle continues where it stopped.
    """

    def __init__(
        self,
        spec: RobotSpec,
        stations: dict[str, Point],
        locks: StationLocks,
        *,
        timing: ArmTiming,
        failure_rate: float,
        rng: random.Random,
    ) -> None:
        self.robot_id = spec.id
        self._stations = stations
        self._locks = locks
        self._timing = timing
        self._failure_rate = failure_rate
        self._rng = rng
        self._position: Point = spec.base
        self._abort: FailReason | None = None
        self._active_s = 0.0

        self.halted = False
        self.link_up = True
        self.stalled = False
        self.last_heartbeat = time.monotonic()
        self.waiting_for: str | None = None
        self.step: JobStep | None = None
        self.progress = 0.0
        self.on_change: Callable[[bool], None] = lambda urgent: None

    def halt(self) -> None:
        self.halted = True

    def release(self) -> None:
        self.halted = False

    def abort(self, reason: FailReason) -> None:
        self._abort = reason

    def apply_fault(self, *, connected: bool, stalled: bool) -> None:
        self.link_up = connected
        self.stalled = stalled

    async def heartbeat(self, interval_s: float = 1.0) -> None:
        while True:
            if self.link_up:
                self.last_heartbeat = time.monotonic()
            await asyncio.sleep(interval_s)

    async def run(self, job: JobSpec) -> None:
        self._abort = None
        self._active_s = 0.0
        failure = self._draw_failure()
        pick_at = self._stations[job.pick]
        place_at = self._stations[job.place]
        move_in = self._travel(self._position, pick_at)
        move_over = self._travel(pick_at, place_at)
        grip = self._timing.grip_s
        plan = (
            (job.pick, pick_at, ((JobStep.MOVE_TO_PICK, move_in), (JobStep.PICK, grip))),
            (job.place, place_at, ((JobStep.MOVE_TO_PLACE, move_over), (JobStep.PLACE, grip))),
        )
        total = move_in + move_over + 2 * grip
        done = 0.0
        try:
            for station, location, steps in plan:
                self.step = steps[0][0]
                async with self._at_station(station):
                    for step, duration in steps:
                        await self._perform(step, duration, failure, done, total)
                        done += duration
                        if step in _MOVES:
                            self._position = location
        finally:
            self.step = None
            self.progress = 0.0
            self.waiting_for = None

    async def _perform(
        self,
        step: JobStep,
        duration: float,
        failure: _PlannedFailure | None,
        done_before: float,
        total: float,
    ) -> None:
        self.step = step
        self.on_change(True)
        fail_after = duration * 0.5 if failure and failure.step is step else None
        tick = self._timing.tick_s
        elapsed = 0.0
        while elapsed < duration:
            await asyncio.sleep(tick)
            if self._abort is not None:
                raise JobFailure(self._abort)
            if self.halted or not self.link_up:
                continue
            self._active_s += tick
            if self._active_s > self._timing.max_runtime_s:
                raise JobFailure(FailReason.TIMEOUT)
            if self.stalled:
                continue
            elapsed = min(duration, elapsed + tick)
            if fail_after is not None and elapsed >= fail_after:
                raise JobFailure(failure.reason)  # type: ignore[union-attr]
            self.progress = (done_before + elapsed) / total
            self.on_change(False)

    @asynccontextmanager
    async def _at_station(self, station: str) -> AsyncIterator[None]:
        if self._locks.is_busy(station):
            log.info(
                "waiting for station robot=%s station=%s held_by=%s",
                self.robot_id,
                station,
                self._locks.holder(station),
            )
            self.waiting_for = station
            self.on_change(True)
        async with self._locks.hold(station, self.robot_id):
            if self.waiting_for is not None:
                self.waiting_for = None
                self.on_change(True)
            yield

    def _travel(self, a: Point, b: Point) -> float:
        return max(self._timing.min_move_s, distance(a, b) / self._timing.speed_mps)

    def _draw_failure(self) -> _PlannedFailure | None:
        if self._rng.random() >= self._failure_rate:
            return None
        step = self._rng.choice(list(JobStep))
        return _PlannedFailure(step, self._rng.choice(_FAILURES_BY_STEP[step]))
