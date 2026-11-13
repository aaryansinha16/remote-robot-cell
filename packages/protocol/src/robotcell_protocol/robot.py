from enum import StrEnum

from pydantic import BaseModel, ConfigDict


class RobotActivity(StrEnum):
    IDLE = "idle"
    BUSY = "busy"
    PAUSED = "paused"
    OFFLINE = "offline"


class Enrollment(StrEnum):
    UNENROLLED = "unenrolled"
    ENROLLED = "enrolled"


class PauseReason(StrEnum):
    PERSON_IN_CELL = "person_in_cell"
    SAFETY_FEED_LOST = "safety_feed_lost"
    AWAITING_SAFETY_FEED = "awaiting_safety_feed"


class JobStep(StrEnum):
    MOVE_TO_PICK = "move_to_pick"
    PICK = "pick"
    MOVE_TO_PLACE = "move_to_place"
    PLACE = "place"


class PauseInfo(BaseModel):
    model_config = ConfigDict(frozen=True)

    reason: PauseReason
    since: int
    cells: tuple[str, ...] = ()
    track_ids: tuple[int, ...] = ()
    latency_ms: int | None = None

    def same_cause(self, other: "PauseInfo | None") -> bool:
        return (
            other is not None
            and other.reason == self.reason
            and other.cells == self.cells
            and other.track_ids == self.track_ids
        )


class CurrentJob(BaseModel):
    model_config = ConfigDict(frozen=True)

    job_id: str
    pick: str
    place: str
    step: JobStep
    progress: float
