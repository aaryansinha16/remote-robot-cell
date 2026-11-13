from onprem.safety import SafetyMonitor
from robotcell_protocol import FusionFrame, PauseInfo, PauseReason, Rect

CELLS = {"cell-1": Rect(x=(0, 10), y=(0, 8)), "cell-2": Rect(x=(8, 18), y=(0, 8))}


class FakeRobot:
    def __init__(self, cell: str) -> None:
        self.cell = cell
        self.pause: PauseInfo | None = None

    def set_pause(self, pause: PauseInfo | None) -> None:
        self.pause = pause


class Clock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


def frame(*people: tuple[int, float, float], cls: str = "person") -> FusionFrame:
    return FusionFrame.model_validate(
        {
            "ts": 0,
            "t_ms": 0,
            "objects": [
                {"entity_id": i, "gid": gid, "class": cls, "kf_pos": [x, y]}
                for i, (gid, x, y) in enumerate(people)
            ],
        }
    )


def make():
    clock = Clock()
    robots = {"robot-1": FakeRobot("cell-1"), "robot-2": FakeRobot("cell-2")}
    monitor = SafetyMonitor(
        CELLS,
        robots.values(),
        resume_after_s=2.0,
        feed_timeout_s=1.0,
        monotonic=clock,
        wall_ms=lambda: 0,
    )
    return monitor, robots, clock


def reason(robot: FakeRobot) -> PauseReason | None:
    return robot.pause.reason if robot.pause else None


def test_robots_wait_for_the_feed_before_moving():
    monitor, robots, _ = make()

    monitor.evaluate()

    assert reason(robots["robot-1"]) == PauseReason.AWAITING_SAFETY_FEED


def test_person_in_cell_pauses_only_that_robot_on_the_same_frame():
    monitor, robots, _ = make()

    monitor.on_frame(frame((1042, 3.0, 3.0)))

    assert reason(robots["robot-1"]) == PauseReason.PERSON_IN_CELL
    assert robots["robot-1"].pause.track_ids == (1042,)
    assert robots["robot-2"].pause is None


def test_person_at_shared_station_pauses_both_robots():
    monitor, robots, _ = make()

    monitor.on_frame(frame((7, 9.0, 4.0)))

    assert reason(robots["robot-1"]) == PauseReason.PERSON_IN_CELL
    assert reason(robots["robot-2"]) == PauseReason.PERSON_IN_CELL


def test_resume_waits_until_cell_has_been_clear_long_enough():
    monitor, robots, clock = make()
    monitor.on_frame(frame((1, 3.0, 3.0)))

    clock.now += 0.2
    monitor.on_frame(frame())
    assert reason(robots["robot-1"]) == PauseReason.PERSON_IN_CELL

    clock.now += 1.9
    monitor.on_frame(frame())
    assert robots["robot-1"].pause is None


def test_forklifts_do_not_pause_robots():
    monitor, robots, _ = make()

    monitor.on_frame(frame((9, 3.0, 3.0), cls="forklift"))

    assert robots["robot-1"].pause is None


def test_stale_feed_pauses_everything():
    monitor, robots, clock = make()
    monitor.on_frame(frame())

    clock.now += 1.5
    monitor.evaluate()

    assert reason(robots["robot-1"]) == PauseReason.SAFETY_FEED_LOST
    assert reason(robots["robot-2"]) == PauseReason.SAFETY_FEED_LOST

    monitor.on_frame(frame())
    assert robots["robot-1"].pause is None
