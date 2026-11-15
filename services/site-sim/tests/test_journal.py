import random

from robotcell_protocol import (
    CurrentJob,
    JobStep,
    PauseInfo,
    PauseReason,
    RobotActivity,
    RobotTelemetry,
)
from sitesim.journal import Journal
from sitesim.world import World

from .test_world import SITE, run


def texts(journal: Journal) -> list[str]:
    return [entry.text for entry in journal.recent()]


def telemetry(state: RobotActivity, **changes) -> RobotTelemetry:
    return RobotTelemetry(robot_id="robot-1", state=state, tool=(5.5, 5.0), at=0, **changes)


def test_people_entering_and_leaving_cells_are_logged_once():
    world = World(SITE, ambient=False, rng=random.Random(1))
    journal = Journal(SITE)
    person = world.spawn_person(world.target_for(cell=None, station="C"))

    for _ in range(40):
        run(world, 0.2)
        journal.observe_people(world.actors)
    world.leave(person.gid)
    for _ in range(60):
        run(world, 0.2)
        journal.observe_people(world.actors)

    gid = person.gid
    assert texts(journal) == [
        f"person {gid} entered cell-1",
        f"person {gid} entered cell-2",
        f"person {gid} left cell-1",
        f"person {gid} left cell-2",
    ]


def test_a_track_dropped_inside_a_cell_is_called_out():
    world = World(SITE, ambient=False, rng=random.Random(1))
    journal = Journal(SITE)
    person = world.spawn_person((3.0, 3.0), walk=False)
    journal.observe_people(world.actors)

    world.remove(person.gid)
    journal.observe_people(world.actors)

    assert texts(journal)[-1] == f"track {person.gid} dropped inside cell-1"


def test_pause_resume_and_offline_come_from_the_site_telemetry():
    journal = Journal(SITE)
    job = CurrentJob(job_id="j1", pick="A", place="C", step=JobStep.MOVE_TO_PICK, progress=0.1)
    pause = PauseInfo(
        reason=PauseReason.PERSON_IN_CELL,
        since=0,
        cells=("cell-1",),
        track_ids=(1041,),
        latency_ms=2,
    )

    journal.observe_robot(telemetry(RobotActivity.IDLE))
    journal.observe_robot(telemetry(RobotActivity.BUSY, current=job))
    journal.observe_robot(telemetry(RobotActivity.PAUSED, current=job, pause=pause))
    journal.observe_robot(telemetry(RobotActivity.PAUSED, current=job, pause=pause))
    journal.observe_robot(telemetry(RobotActivity.BUSY, current=job))
    journal.observe_robot(telemetry(RobotActivity.OFFLINE))
    journal.observe_robot(telemetry(RobotActivity.IDLE))

    assert texts(journal) == [
        "robot-1 started A to C",
        "robot-1 paused: person in cell-1, stopped 2 ms after detection",
        "robot-1 resumed",
        "robot-1 offline, heartbeat lost",
        "robot-1 back online",
    ]


def test_recent_only_returns_entries_after_a_given_id():
    journal = Journal(SITE)
    for index in range(5):
        journal.note("action", f"event {index}")

    assert [entry.text for entry in journal.recent(after=3)] == ["event 3", "event 4"]
