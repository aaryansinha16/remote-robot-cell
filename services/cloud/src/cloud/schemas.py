from typing import Self

import aiosqlite
from pydantic import BaseModel, Field, model_validator
from robotcell_protocol import JobStatus, RobotSnapshot

STATION_PATTERN = r"^[A-Z0-9][A-Z0-9_-]{0,15}$"


class JobCreate(BaseModel):
    robot_id: str = Field(min_length=1, max_length=64)
    pick: str = Field(pattern=STATION_PATTERN)
    place: str = Field(pattern=STATION_PATTERN)
    valid_for_s: int | None = Field(default=None, ge=10, le=86_400)

    @model_validator(mode="after")
    def _distinct_stations(self) -> Self:
        if self.pick == self.place:
            raise ValueError("pick and place must be different stations")
        return self


class JobView(BaseModel):
    id: int
    job_id: str
    site_id: str
    robot_id: str
    pick: str
    place: str
    status: JobStatus
    seq: int
    reason: str | None
    created_at: int
    updated_at: int
    start_deadline: int
    send_attempts: int
    cancel_requested: bool
    cancel_error: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Self:
        return cls(
            id=row["id"],
            job_id=row["job_id"],
            site_id=row["site_id"],
            robot_id=row["robot_id"],
            pick=row["pick"],
            place=row["place"],
            status=JobStatus(row["status"]),
            seq=row["seq"],
            reason=row["reason"],
            created_at=row["created_at"],
            updated_at=row["updated_at"],
            start_deadline=row["start_deadline"],
            send_attempts=row["send_attempts"],
            cancel_requested=row["cancel_requested_at"] is not None,
            cancel_error=row["cancel_error"],
        )


class JobEventView(BaseModel):
    seq: int
    status: JobStatus
    reason: str | None
    at: int
    received_at: int


class JobDetail(JobView):
    events: list[JobEventView]


class RobotView(RobotSnapshot):
    site_id: str
    request: str | None
    last_error: str | None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Self:
        snapshot = RobotSnapshot.model_validate_json(row["snapshot"])
        return cls(
            **snapshot.model_dump(),
            site_id=row["site_id"],
            request=row["request"],
            last_error=row["last_error"],
        )


class SiteView(BaseModel):
    site_id: str
    online: bool
    boot_id: str | None = None
    version: str | None = None
    last_seen: int | None = None
    changed_at: int | None = None

    @classmethod
    def from_row(cls, row: aiosqlite.Row) -> Self:
        return cls(
            site_id=row["site_id"],
            online=bool(row["online"]),
            boot_id=row["boot_id"],
            version=row["version"],
            last_seen=row["last_seen"],
            changed_at=row["changed_at"],
        )


class Snapshot(BaseModel):
    site: SiteView
    robots: list[RobotView]
    jobs: list[JobView]
