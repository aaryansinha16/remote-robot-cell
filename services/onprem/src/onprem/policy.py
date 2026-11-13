from dataclasses import dataclass

from robotcell_protocol import RejectReason

from .jobs import JobSpec


@dataclass(frozen=True, slots=True)
class RobotView:
    enrolled: bool
    stations: frozenset[str]
    queue_length: int


def evaluate(
    job: JobSpec, robot: RobotView | None, *, now: int, max_queue: int
) -> RejectReason | None:
    """Decide whether the site takes a job. Returns the reason to reject it, or None to accept."""
    if robot is None:
        return RejectReason.UNKNOWN_ROBOT
    if not robot.enrolled:
        return RejectReason.ROBOT_NOT_ENROLLED
    if job.pick == job.place:
        return RejectReason.SAME_STATION
    if job.pick not in robot.stations or job.place not in robot.stations:
        return RejectReason.STATION_NOT_REACHABLE
    if now > job.start_deadline:
        return RejectReason.EXPIRED
    if robot.queue_length >= max_queue:
        return RejectReason.QUEUE_FULL
    return None
