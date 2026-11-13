import asyncio
import contextlib
from collections.abc import Awaitable, Callable, Iterable

from robotcell_protocol import RobotSnapshot


class StatePublisher:
    """Coalesces robot state changes into retained snapshots for the cloud.

    Urgent changes (pause, job start, queue changes) go out right away. Progress ticks are
    batched to at most one snapshot per robot every `interval_s`.
    """

    def __init__(self, interval_s: float = 0.5) -> None:
        self._interval_s = interval_s
        self._dirty: set[str] = set()
        self._urgent = asyncio.Event()

    def mark(self, robot_id: str, urgent: bool) -> None:
        self._dirty.add(robot_id)
        if urgent:
            self._urgent.set()

    def mark_all(self, robot_ids: Iterable[str]) -> None:
        self._dirty.update(robot_ids)
        self._urgent.set()

    async def run(
        self,
        snapshot: Callable[[str], RobotSnapshot],
        publish: Callable[[RobotSnapshot], Awaitable[bool]],
    ) -> None:
        while True:
            with contextlib.suppress(TimeoutError):
                await asyncio.wait_for(self._urgent.wait(), self._interval_s)
            self._urgent.clear()
            for robot_id in sorted(self._dirty):
                if not await publish(snapshot(robot_id)):
                    break
                self._dirty.discard(robot_id)
