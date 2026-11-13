import math
import random
from collections import deque
from dataclasses import dataclass, field

from robotcell_protocol import FusionFrame, FusionObject, Point, RobotFault, SiteMap

WALK_MPS = 1.3
FORKLIFT_MPS = 1.6


@dataclass
class Actor:
    gid: int
    kind: str
    position: Point
    speed: float
    route: deque[Point] = field(default_factory=deque)
    patrol: tuple[Point, ...] = ()
    vanish_at_end: bool = False
    ambient: bool = False
    velocity: Point = (0.0, 0.0)
    smoothed: Point = (0.0, 0.0)
    heading: float = 0.0

    def __post_init__(self) -> None:
        self.smoothed = self.position


class World:
    """A small model of the shop floor that renders itself as fusion/objects frames."""

    def __init__(self, site: SiteMap, *, ambient: bool, rng: random.Random | None = None) -> None:
        self.site = site
        self.feed_on = True
        self.faults: dict[str, RobotFault] = {robot.id: RobotFault() for robot in site.robots}
        self.dirty_faults: set[str] = set(self.faults)
        self._actors: dict[int, Actor] = {}
        self._rng = rng or random.Random()
        self._next_gid = 1040
        if ambient:
            self._add_ambient()

    @property
    def actors(self) -> list[Actor]:
        return list(self._actors.values())

    def spawn_person(self, target: Point, *, walk: bool = True) -> Actor:
        aisle_y = self.site.aisle.y
        start = (target[0], aisle_y) if walk else target
        actor = Actor(self._gid(), "person", start, WALK_MPS, route=deque([target]))
        self._actors[actor.gid] = actor
        return actor

    def target_for(self, *, cell: str | None, station: str | None) -> Point:
        if station is not None:
            x, y = self.site.stations[station]
            return (x, y + 1.0)
        if cell is not None:
            rect = self.site.cells[cell]
            return (rect.center[0], rect.y[1] - 1.5)
        raise ValueError("need a cell or a station")

    def leave(self, gid: int) -> bool:
        actor = self._actors.get(gid)
        if actor is None or actor.ambient:
            return False
        aisle = self.site.aisle
        actor.route = deque([(actor.position[0], aisle.y), (aisle.x[1] + 2.0, aisle.y)])
        actor.vanish_at_end = True
        return True

    def leave_all(self) -> int:
        return sum(self.leave(actor.gid) for actor in self.actors if not actor.ambient)

    def remove(self, gid: int) -> bool:
        actor = self._actors.get(gid)
        if actor is None or actor.ambient:
            return False
        del self._actors[gid]
        return True

    def set_fault(self, robot_id: str, fault: RobotFault) -> None:
        self.faults[robot_id] = fault
        self.dirty_faults.add(robot_id)

    def step(self, dt: float) -> None:
        for actor in self.actors:
            self._move(actor, dt)

    def frame(self, now_ms: int) -> FusionFrame:
        objects = [self._observe(index, actor) for index, actor in enumerate(self.actors)]
        return FusionFrame(ts=now_ms, t_ms=now_ms - 30, objects=objects)

    def _move(self, actor: Actor, dt: float) -> None:
        if not actor.route and actor.patrol:
            actor.route = deque(actor.patrol)
        if not actor.route:
            actor.velocity = (0.0, 0.0)
            if actor.vanish_at_end:
                self._actors.pop(actor.gid, None)
            return
        tx, ty = actor.route[0]
        x, y = actor.position
        dx, dy = tx - x, ty - y
        dist = math.hypot(dx, dy)
        step = actor.speed * dt
        if dist <= step:
            actor.position = (tx, ty)
            actor.route.popleft()
        else:
            actor.position = (x + dx / dist * step, y + dy / dist * step)
        actor.velocity = ((actor.position[0] - x) / dt, (actor.position[1] - y) / dt)
        if math.hypot(*actor.velocity) > 0.05:
            actor.heading = math.degrees(math.atan2(actor.velocity[1], actor.velocity[0])) % 360
        sx, sy = actor.smoothed
        actor.smoothed = (sx + 0.4 * (actor.position[0] - sx), sy + 0.4 * (actor.position[1] - sy))

    def _observe(self, index: int, actor: Actor) -> FusionObject:
        noise = self._rng.gauss
        speed = math.hypot(*actor.velocity)
        moving = speed > 0.05
        person = actor.kind == "person"
        attributes = (
            {"vest": 1, "is_loaded": -1, "is_parked": -1, "is_vehicle_module_on": -1}
            if person
            else {"vest": -1, "is_loaded": 1, "is_parked": 0, "is_vehicle_module_on": 1}
        )
        return FusionObject(
            entity_id=index,
            gid=actor.gid,
            class_=actor.kind,
            kf_pos=(
                round(actor.position[0] + noise(0, 0.02), 2),
                round(actor.position[1] + noise(0, 0.02), 2),
            ),
            avg_pos=(round(actor.smoothed[0], 2), round(actor.smoothed[1], 2)),
            velocity=(round(actor.velocity[0], 2), round(actor.velocity[1], 2)),
            speed=round(speed, 2),
            motion_dir=1 if moving else 0,
            yaw_heading=round(actor.heading, 1),
            yaw_heading_known=moving,
            yaw_body=0.0 if person else round(actor.heading, 1),
            yaw_body_known=not person,
            yaw_rate=0.0,
            imu=not person,
            attributes=attributes,
        )

    def _add_ambient(self) -> None:
        aisle = self.site.aisle
        lane = aisle.y + 0.8
        forklift = Actor(
            1038,
            "forklift",
            (aisle.x[0], lane),
            FORKLIFT_MPS,
            patrol=((aisle.x[1], lane), (aisle.x[0], lane)),
            ambient=True,
        )
        walker = Actor(
            1039,
            "person",
            (aisle.x[1] - 4, aisle.y - 0.6),
            WALK_MPS * 0.8,
            patrol=((aisle.x[0] + 2, aisle.y - 0.6), (aisle.x[1] - 2, aisle.y - 0.6)),
            ambient=True,
        )
        self._actors[forklift.gid] = forklift
        self._actors[walker.gid] = walker

    def _gid(self) -> int:
        self._next_gid += 1
        return self._next_gid
