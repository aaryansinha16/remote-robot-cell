import asyncio
import logging
import time
from collections.abc import Callable, Iterable
from typing import Protocol

from robotcell_protocol import FusionFrame, PauseInfo, PauseReason, Rect, now_ms

log = logging.getLogger(__name__)


class Pausable(Protocol):
    @property
    def cell(self) -> str: ...

    def set_pause(self, pause: PauseInfo | None) -> None: ...


class SafetyMonitor:
    """Turns the fusion object stream into pause decisions, entirely on the site.

    - A robot pauses on the first frame that shows a person inside its cell.
    - It resumes only after its cell has been clear for `resume_after_s`. The same hold covers
      a track that drops out for a moment while the person is still there.
    - No frame for `feed_timeout_s` pauses every robot, and robots start paused until the
      first frame arrives. Missing safety data is treated as unsafe.
    """

    def __init__(
        self,
        cells: dict[str, Rect],
        robots: Iterable[Pausable],
        *,
        resume_after_s: float,
        feed_timeout_s: float,
        classes: Iterable[str] = ("person",),
        monotonic: Callable[[], float] = time.monotonic,
        wall_ms: Callable[[], int] = now_ms,
    ) -> None:
        self._cells = cells
        self._robots = list(robots)
        self._resume_after_s = resume_after_s
        self._feed_timeout_s = feed_timeout_s
        self._classes = frozenset(classes)
        self._monotonic = monotonic
        self._wall_ms = wall_ms

        self._last_frame_at: float | None = None
        self._feed_lost_logged = False
        self._last_seen: dict[str, float] = {}
        self._tracks: dict[str, tuple[int, ...]] = {}
        self._latency_ms: int | None = None

    def on_frame(self, frame: FusionFrame) -> None:
        now = self._monotonic()
        self._last_frame_at = now
        occupants: dict[str, list[int]] = {}
        for obj in frame.objects:
            if obj.class_ not in self._classes:
                continue
            for name, rect in self._cells.items():
                if rect.contains(obj.kf_pos):
                    occupants.setdefault(name, []).append(obj.gid)
        for name, gids in occupants.items():
            self._last_seen[name] = now
            self._tracks[name] = tuple(sorted(gids))
        self._latency_ms = max(0, self._wall_ms() - frame.ts)
        self.evaluate()

    def evaluate(self) -> None:
        now = self._monotonic()
        for robot in self._robots:
            robot.set_pause(self._decide(robot.cell, now))

    async def watchdog(self, interval_s: float = 0.1) -> None:
        while True:
            await asyncio.sleep(interval_s)
            self.evaluate()

    def _decide(self, cell: str, now: float) -> PauseInfo | None:
        since = self._wall_ms()
        if self._last_frame_at is None:
            return PauseInfo(reason=PauseReason.AWAITING_SAFETY_FEED, since=since)
        if now - self._last_frame_at > self._feed_timeout_s:
            if not self._feed_lost_logged:
                log.error("fusion feed lost, pausing all robots")
                self._feed_lost_logged = True
            return PauseInfo(reason=PauseReason.SAFETY_FEED_LOST, since=since)
        self._feed_lost_logged = False
        seen = self._last_seen.get(cell)
        if seen is not None and now - seen < self._resume_after_s:
            return PauseInfo(
                reason=PauseReason.PERSON_IN_CELL,
                since=since,
                cells=(cell,),
                track_ids=self._tracks.get(cell, ()),
                latency_ms=self._latency_ms,
            )
        return None
