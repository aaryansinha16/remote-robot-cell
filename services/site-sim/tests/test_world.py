import random
from pathlib import Path

import yaml
from robotcell_protocol import FusionFrame, SiteMap
from sitesim.world import World

SITE = SiteMap.model_validate(
    yaml.safe_load((Path(__file__).parents[3] / "config" / "site.yaml").read_text())
)


def run(world: World, seconds: float, dt: float = 0.2) -> None:
    for _ in range(int(seconds / dt)):
        world.step(dt)


def cells_of(frame: FusionFrame, gid: int) -> list[str]:
    obj = next(o for o in frame.objects if o.gid == gid)
    return SITE.cells_at(obj.kf_pos)


def test_person_walks_from_the_aisle_into_the_cell_and_back_out():
    world = World(SITE, ambient=False, rng=random.Random(1))
    person = world.spawn_person(world.target_for(cell="cell-1", station=None))

    assert cells_of(world.frame(0), person.gid) == []
    run(world, 5)
    assert cells_of(world.frame(0), person.gid) == ["cell-1"]

    world.leave(person.gid)
    run(world, 30)
    assert all(o.gid != person.gid for o in world.frame(0).objects)


def test_person_at_station_c_is_inside_both_cells():
    world = World(SITE, ambient=False, rng=random.Random(1))
    person = world.spawn_person(world.target_for(cell=None, station="C"), walk=False)

    assert cells_of(world.frame(0), person.gid) == ["cell-1", "cell-2"]


def test_ambient_traffic_never_enters_a_cell():
    world = World(SITE, ambient=True, rng=random.Random(1))

    for _ in range(300):
        world.step(0.2)
        frame = world.frame(0)
        assert all(not SITE.cells_at(o.kf_pos) for o in frame.objects)


def test_frames_use_the_site_field_names():
    world = World(SITE, ambient=True)
    world.step(0.2)

    body = world.frame(1763030400123).model_dump(by_alias=True)

    assert body["ts"] - body["t_ms"] == 30
    assert {"gid", "class", "kf_pos", "velocity", "speed", "attributes"} <= set(body["objects"][0])
