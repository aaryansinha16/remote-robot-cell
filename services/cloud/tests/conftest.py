import asyncio

import pytest
from cloud.config import Settings
from cloud.db import Database
from cloud.fleet import FleetService
from cloud.hub import Hub
from cloud.jobs import JobService
from cloud.reconciler import Reconciler
from robotcell_protocol import Enrollment, RobotActivity, RobotSnapshot, decode


def snapshot(robot_id: str = "robot-1", enrolled: bool = True, **extra) -> RobotSnapshot:
    return RobotSnapshot(
        robot_id=robot_id,
        serial=f"SER-{robot_id}",
        model="sim",
        cell="cell-1",
        stations=["A", "B", "C"],
        enrollment=Enrollment.ENROLLED if enrolled else Enrollment.UNENROLLED,
        connected=True,
        state=RobotActivity.IDLE,
        updated_at=1,
        **extra,
    )


class FakeBroker:
    def __init__(self) -> None:
        self.sent: list = []
        self.up = True

    async def publish(self, topic: str, payload: bytes, *, qos: int = 1) -> bool:
        if not self.up:
            return False
        self.sent.append(decode(payload).message)
        return True

    def kinds(self) -> list[str]:
        return [message.kind for message in self.sent]


class Cloud:
    def __init__(self, db: Database, settings: Settings, clock) -> None:
        self.hub = Hub()
        self.wake = asyncio.Event()
        self.fleet = FleetService(db, self.hub, settings, wake=self.wake.set, clock=clock)
        self.jobs = JobService(db, self.hub, settings, wake=self.wake.set, clock=clock)
        self.reconciler = Reconciler(
            db, self.fleet, self.jobs, settings, wake=self.wake, clock=clock
        )
        self.broker = FakeBroker()
        self.reconciler.attach(self.broker)


class Clock:
    def __init__(self) -> None:
        self.now = 1_000_000

    def __call__(self) -> int:
        return self.now


@pytest.fixture
def settings(tmp_path):
    return Settings(db_path=tmp_path / "cloud.db")


@pytest.fixture
async def db(settings):
    database = Database(settings.db_path)
    await database.open()
    yield database
    await database.close()


@pytest.fixture
def clock():
    return Clock()


@pytest.fixture
async def cloud(db, settings, clock):
    return Cloud(db, settings, clock)
