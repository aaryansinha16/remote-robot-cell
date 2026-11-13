from enum import StrEnum


class JobStatus(StrEnum):
    PENDING = "pending"
    RECEIVED = "received"
    QUEUED = "queued"
    RUNNING = "running"
    DONE = "done"
    FAILED = "failed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"

    @property
    def is_terminal(self) -> bool:
        return self in TERMINAL_STATUSES


TERMINAL_STATUSES = frozenset(
    {JobStatus.DONE, JobStatus.FAILED, JobStatus.REJECTED, JobStatus.CANCELLED}
)

_TRANSITIONS: dict[JobStatus, frozenset[JobStatus]] = {
    JobStatus.PENDING: frozenset({JobStatus.RECEIVED, JobStatus.CANCELLED}),
    JobStatus.RECEIVED: frozenset({JobStatus.QUEUED, JobStatus.REJECTED, JobStatus.CANCELLED}),
    JobStatus.QUEUED: frozenset({JobStatus.RUNNING, JobStatus.CANCELLED, JobStatus.FAILED}),
    JobStatus.RUNNING: frozenset({JobStatus.DONE, JobStatus.FAILED}),
}


def can_transition(current: JobStatus, target: JobStatus) -> bool:
    return target in _TRANSITIONS.get(current, frozenset())


class RejectReason(StrEnum):
    UNKNOWN_ROBOT = "unknown_robot"
    ROBOT_NOT_ENROLLED = "robot_not_enrolled"
    STATION_NOT_REACHABLE = "station_not_reachable"
    SAME_STATION = "same_station"
    EXPIRED = "expired"
    QUEUE_FULL = "queue_full"


class FailReason(StrEnum):
    GRIPPER_FAULT = "gripper_fault"
    PART_MISSING = "part_missing"
    PATH_BLOCKED = "path_blocked"
    TIMEOUT = "timeout"
    ROBOT_DISCONNECTED = "robot_disconnected"
    INTERRUPTED = "interrupted"
    EXPIRED = "expired"


class CommandRefusal(StrEnum):
    UNKNOWN_ROBOT = "unknown_robot"
    SERIAL_MISMATCH = "serial_mismatch"
    ROBOT_BUSY = "robot_busy"
    ALREADY_RUNNING = "already_running"
    ALREADY_FINISHED = "already_finished"
