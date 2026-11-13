from typing import ClassVar

from pydantic import BaseModel, ConfigDict, Field

from .lifecycle import JobStatus
from .robot import CurrentJob, Enrollment, PauseInfo, RobotActivity


class Message(BaseModel):
    model_config = ConfigDict(frozen=True, extra="ignore")

    kind: ClassVar[str]


REGISTRY: dict[str, type[Message]] = {}


def register[M: type[Message]](cls: M) -> M:
    if cls.kind in REGISTRY:
        raise ValueError(f"message kind {cls.kind!r} registered twice")
    REGISTRY[cls.kind] = cls
    return cls


@register
class JobSubmit(Message):
    kind = "job.submit"

    job_id: str
    robot_id: str
    pick: str
    place: str
    created_at: int
    start_deadline: int


@register
class JobCancel(Message):
    kind = "job.cancel"

    job_id: str


@register
class RobotEnroll(Message):
    kind = "robot.enroll"

    robot_id: str
    serial: str


@register
class RobotUnenroll(Message):
    kind = "robot.unenroll"

    robot_id: str


@register
class SyncRequest(Message):
    kind = "sync.request"

    job_ids: list[str] = Field(default_factory=list)


@register
class JobStatusUpdate(Message):
    kind = "job.status"

    job_id: str
    robot_id: str | None
    status: JobStatus
    seq: int
    at: int
    reason: str | None = None


@register
class CommandResult(Message):
    kind = "command.result"

    command: str
    target: str
    ok: bool
    reason: str | None = None


@register
class SiteStatus(Message):
    kind = "site.status"

    online: bool
    boot_id: str | None = None
    version: str | None = None
    started_at: int | None = None


@register
class Heartbeat(Message):
    kind = "site.heartbeat"

    boot_id: str
    uptime_s: float


@register
class RobotSnapshot(Message):
    kind = "robot.state"

    robot_id: str
    serial: str
    model: str
    cell: str
    stations: list[str]
    enrollment: Enrollment
    connected: bool
    state: RobotActivity
    pause: PauseInfo | None = None
    current: CurrentJob | None = None
    queue: list[str] = Field(default_factory=list)
    waiting_for: str | None = None
    updated_at: int


@register
class CloudStatus(Message):
    kind = "cloud.status"

    online: bool


@register
class RobotFault(Message):
    kind = "sim.robot_fault"

    connected: bool = True
    stalled: bool = False


COMMANDS: tuple[type[Message], ...] = (
    JobSubmit,
    JobCancel,
    RobotEnroll,
    RobotUnenroll,
    SyncRequest,
)
