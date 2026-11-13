import math
from typing import Self

from pydantic import BaseModel, ConfigDict, model_validator

Point = tuple[float, float]


class Rect(BaseModel):
    model_config = ConfigDict(frozen=True)

    x: tuple[float, float]
    y: tuple[float, float]

    def contains(self, point: Point) -> bool:
        px, py = point
        return self.x[0] <= px <= self.x[1] and self.y[0] <= py <= self.y[1]

    @property
    def center(self) -> Point:
        return ((self.x[0] + self.x[1]) / 2, (self.y[0] + self.y[1]) / 2)


class RobotSpec(BaseModel):
    model_config = ConfigDict(frozen=True)

    id: str
    serial: str
    model: str
    cell: str
    base: Point
    stations: tuple[str, ...]


class Aisle(BaseModel):
    model_config = ConfigDict(frozen=True)

    y: float
    x: tuple[float, float]


class SiteMap(BaseModel):
    """Site geometry in metres, in the same frame as fusion kf_pos."""

    model_config = ConfigDict(frozen=True)

    cells: dict[str, Rect]
    stations: dict[str, Point]
    robots: tuple[RobotSpec, ...]
    aisle: Aisle

    @model_validator(mode="after")
    def _check_references(self) -> Self:
        for robot in self.robots:
            if robot.cell not in self.cells:
                raise ValueError(f"{robot.id} is in unknown cell {robot.cell}")
            missing = set(robot.stations) - set(self.stations)
            if missing:
                raise ValueError(f"{robot.id} references unknown stations {sorted(missing)}")
        return self

    def cells_at(self, point: Point) -> list[str]:
        return [name for name, rect in self.cells.items() if rect.contains(point)]


def distance(a: Point, b: Point) -> float:
    return math.hypot(b[0] - a[0], b[1] - a[1])
