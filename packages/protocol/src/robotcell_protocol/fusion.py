from pydantic import BaseModel, ConfigDict, Field


class FusionObject(BaseModel):
    """One tracked object as published by the site fusion service on fusion/objects."""

    model_config = ConfigDict(populate_by_name=True, extra="ignore")

    entity_id: int
    gid: int
    class_: str = Field(alias="class")
    kf_pos: tuple[float, float]
    avg_pos: tuple[float, float] | None = None
    velocity: tuple[float, float] = (0.0, 0.0)
    speed: float = 0.0
    motion_dir: int = 0
    yaw_heading: float = 0.0
    yaw_heading_known: bool = False
    yaw_body: float = 0.0
    yaw_body_known: bool = False
    yaw_rate: float = 0.0
    imu: bool = False
    attributes: dict[str, int] = Field(default_factory=dict)


class FusionFrame(BaseModel):
    model_config = ConfigDict(extra="ignore")

    ts: int
    t_ms: int
    objects: list[FusionObject]

    def to_json(self) -> bytes:
        return self.model_dump_json(by_alias=True).encode()
