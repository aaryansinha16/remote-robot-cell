import pytest
from onprem.jobs import JobSpec
from onprem.policy import RobotView, evaluate
from robotcell_protocol import RejectReason

ROBOT = RobotView(enrolled=True, stations=frozenset("ABC"), queue_length=0)


def job(pick="A", place="B", deadline=1_000):
    return JobSpec("j", "robot-1", pick, place, deadline)


@pytest.mark.parametrize(
    ("spec", "robot", "now", "expected"),
    [
        (job(), ROBOT, 0, None),
        (job(), None, 0, RejectReason.UNKNOWN_ROBOT),
        (
            job(),
            RobotView(enrolled=False, stations=frozenset("ABC"), queue_length=0),
            0,
            RejectReason.ROBOT_NOT_ENROLLED,
        ),
        (job("A", "A"), ROBOT, 0, RejectReason.SAME_STATION),
        (job("A", "D"), ROBOT, 0, RejectReason.STATION_NOT_REACHABLE),
        (job(deadline=10), ROBOT, 11, RejectReason.EXPIRED),
        (
            job(),
            RobotView(enrolled=True, stations=frozenset("ABC"), queue_length=5),
            0,
            RejectReason.QUEUE_FULL,
        ),
    ],
)
def test_policy(spec, robot, now, expected):
    assert evaluate(spec, robot, now=now, max_queue=5) == expected
