from collections import deque
from collections.abc import Callable, Iterable
from dataclasses import asdict, dataclass

from robotcell_protocol import (
    PauseInfo,
    PauseReason,
    RobotActivity,
    RobotTelemetry,
    SiteMap,
    now_ms,
)

from .world import Actor


@dataclass(frozen=True, slots=True)
class Entry:
    id: int
    at: int
    kind: str
    text: str

    def as_dict(self) -> dict[str, object]:
        return asdict(self)


def describe_pause(pause: PauseInfo) -> str:
    match pause.reason:
        case PauseReason.PERSON_IN_CELL:
            text = f"person in {' and '.join(pause.cells) or 'the cell'}"
            if pause.latency_ms is not None:
                text += f", stopped {pause.latency_ms} ms after detection"
            return text
        case PauseReason.SAFETY_FEED_LOST:
            return "fusion feed lost"
        case PauseReason.AWAITING_SAFETY_FEED:
            return "waiting for the fusion feed"


class Journal:
    """A short, readable history of the floor for the control page.

    People entering and leaving cells come from the simulator's own world. Pauses, resumes and
    station waits come from the arms' telemetry, so they show what the site decided and carry
    the site's timestamps.
    """

    def __init__(self, site: SiteMap, *, size: int = 80, clock: Callable[[], int] = now_ms):
        self._site = site
        self._clock = clock
        self._entries: deque[Entry] = deque(maxlen=size)
        self._next_id = 0
        self.generation = 0
        self._cells: dict[int, tuple[str, ...]] = {}
        self._robots: dict[str, RobotTelemetry] = {}

    def note(self, kind: str, text: str, *, at: int | None = None) -> None:
        self._next_id += 1
        self._entries.append(Entry(self._next_id, self._clock() if at is None else at, kind, text))

    def recent(self, after: int = 0, limit: int = 40) -> list[Entry]:
        return [entry for entry in self._entries if entry.id > after][-limit:]

    def clear(self) -> None:
        """Empty the log. Ids keep counting, and the bumped generation tells pages to reset."""
        self._entries.clear()
        self.generation += 1

    def observe_people(self, actors: Iterable[Actor]) -> None:
        seen: dict[int, tuple[str, ...]] = {}
        for actor in actors:
            if actor.kind != "person":
                continue
            cells = tuple(self._site.cells_at(actor.position))
            before = self._cells.get(actor.gid, ())
            for cell in cells:
                if cell not in before:
                    self.note("person", f"person {actor.gid} entered {cell}")
            for cell in before:
                if cell not in cells:
                    self.note("person", f"person {actor.gid} left {cell}")
            seen[actor.gid] = cells
        for gid, cells in self._cells.items():
            if gid not in seen and cells:
                self.note("person", f"track {gid} dropped inside {' and '.join(cells)}")
        self._cells = seen

    def observe_robot(self, now: RobotTelemetry) -> None:
        before = self._robots.get(now.robot_id)
        self._robots[now.robot_id] = now
        if before is None:
            return
        robot = now.robot_id
        if now.state is RobotActivity.OFFLINE and before.state is not RobotActivity.OFFLINE:
            self.note("fault", f"{robot} offline, heartbeat lost", at=now.at)
        elif before.state is RobotActivity.OFFLINE and now.state is not RobotActivity.OFFLINE:
            self.note("robot", f"{robot} back online", at=now.at)
        if now.pause and (before.pause is None or before.pause.reason != now.pause.reason):
            self.note("safety", f"{robot} paused: {describe_pause(now.pause)}", at=now.pause.since)
        elif before.pause and now.pause is None:
            self.note("resume", f"{robot} resumed", at=now.at)
        job = now.current
        if job and (before.current is None or before.current.job_id != job.job_id):
            self.note("robot", f"{robot} started {job.pick} to {job.place}", at=now.at)
        if now.waiting_for and now.waiting_for != before.waiting_for:
            self.note("robot", f"{robot} waiting for station {now.waiting_for}", at=now.at)
