"""End to end checks against the running compose stack.

Proves the claims the design rests on: jobs run on both robots, a job sent while the site is
down runs exactly once when it returns, duplicate deliveries never run twice, a person in a
cell pauses that robot without the cloud, and losing the fusion feed pauses everything.

    docker compose up --build -d --wait
    uv run python scripts/e2e.py
"""

import json
import subprocess
import sys
import time
import uuid
from collections.abc import Callable
from typing import Any

import httpx
import paho.mqtt.client as mqtt
from robotcell_protocol import JobSubmit, encode, now_ms, topics

API = "http://localhost:8000/api"
SIM = "http://localhost:8090/api"
SITE_ID = "site-001"
BROKER_AUTH = ("cloud-api", "cloud-dev-secret")
TERMINAL = {"done", "failed", "rejected", "cancelled"}

api = httpx.Client(base_url=API, timeout=5)
sim = httpx.Client(base_url=SIM, timeout=5)


class CheckFailed(Exception):
    pass


def wait_for(what: str, probe: Callable[[], Any], timeout: float = 30.0) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        try:
            result = probe()
        except httpx.HTTPError:
            result = None
        if result:
            return result
        time.sleep(0.2)
    raise CheckFailed(f"timed out after {timeout:.0f}s waiting for {what}")


def expect(condition: bool, message: str) -> None:
    if not condition:
        raise CheckFailed(message)


def compose(*args: str) -> str:
    return subprocess.run(
        ["docker", "compose", *args], check=True, capture_output=True, text=True
    ).stdout


def site_executions() -> dict[str, int]:
    script = (
        "import json, sqlite3; c = sqlite3.connect('/data/onprem.db'); "
        "print(json.dumps(dict(c.execute('SELECT job_id, executions FROM jobs').fetchall())))"
    )
    return json.loads(compose("exec", "-T", "onprem", "python", "-c", script))


def robot(robot_id: str) -> dict[str, Any]:
    return next(r for r in api.get("/robots").json() if r["robot_id"] == robot_id)


def job(job_id: str) -> dict[str, Any]:
    return api.get(f"/jobs/{job_id}").json()


def submit(robot_id: str, pick: str, place: str) -> str:
    response = api.post("/jobs", json={"robot_id": robot_id, "pick": pick, "place": place})
    response.raise_for_status()
    return response.json()["job_id"]


def finished(job_id: str) -> dict[str, Any] | None:
    current = job(job_id)
    return current if current["status"] in TERMINAL else None


def site_online(expected: bool) -> bool:
    return api.get("/site").json()["online"] is expected


def check_ready() -> None:
    wait_for("cloud api", lambda: api.get("/health").json()["broker_connected"], 60)
    wait_for("site online", lambda: site_online(True), 60)
    wait_for("both robots reported", lambda: len(api.get("/robots").json()) == 2, 30)
    sim.post("/people/leave")
    sim.post("/feed", json={"on": True})
    for robot_id in ("robot-1", "robot-2"):
        sim.post(f"/robots/{robot_id}/fault", json={"connected": True, "stalled": False})


def check_enrollment() -> None:
    for robot_id in ("robot-1", "robot-2"):
        api.post(f"/robots/{robot_id}/enroll").raise_for_status()
    for robot_id in ("robot-1", "robot-2"):
        wait_for(f"{robot_id} enrolled", lambda r=robot_id: robot(r)["enrollment"] == "enrolled")
        wait_for(f"{robot_id} idle", lambda r=robot_id: robot(r)["state"] == "idle", 60)


def check_jobs_on_both_robots() -> None:
    ids = [
        submit("robot-1", "A", "C"),
        submit("robot-2", "C", "E"),
        submit("robot-1", "B", "A"),
        submit("robot-2", "D", "E"),
    ]
    rejected = submit("robot-1", "A", "D")
    for job_id in ids:
        wait_for(f"job {job_id[:8]} to finish", lambda j=job_id: finished(j), 90)
    outcome = wait_for("unreachable job to be decided", lambda: finished(rejected))
    expect(outcome["status"] == "rejected", f"A to D on robot-1 ended {outcome['status']}")
    expect(outcome["reason"] == "station_not_reachable", f"wrong reason {outcome['reason']}")


def check_offline_site_runs_each_job_once() -> None:
    compose("stop", "onprem")
    wait_for("cloud to see the site offline", lambda: site_online(False), 20)
    ids = [submit("robot-1", "A", "B"), submit("robot-2", "E", "D")]
    time.sleep(2)
    expect(all(job(j)["status"] == "pending" for j in ids), "jobs left pending while offline")

    compose("start", "onprem")
    wait_for("site back online", lambda: site_online(True), 60)
    for job_id in ids:
        wait_for(f"job {job_id[:8]} to finish", lambda j=job_id: finished(j), 90)

    executions = site_executions()
    for job_id in ids:
        detail = job(job_id)
        running = [e for e in detail["events"] if e["status"] == "running"]
        if detail["status"] != "rejected":
            expect(executions.get(job_id) == 1, f"{job_id[:8]} ran {executions.get(job_id)} times")
            expect(len(running) == 1, f"{job_id[:8]} reported running {len(running)} times")


def check_duplicates_are_ignored() -> None:
    job_id = submit("robot-2", "C", "D")
    record = job(job_id)
    duplicate = encode(
        JobSubmit(
            job_id=job_id,
            robot_id="robot-2",
            pick="C",
            place="D",
            created_at=record["created_at"],
            start_deadline=record["start_deadline"],
        )
    )
    client = mqtt.Client(mqtt.CallbackAPIVersion.VERSION2, client_id=f"e2e-{uuid.uuid4().hex[:8]}")
    client.username_pw_set(*BROKER_AUTH)
    client.connect("localhost", 1883)
    client.loop_start()
    try:
        for _ in range(3):
            client.publish(topics.commands(SITE_ID), duplicate, qos=1).wait_for_publish(5)
    finally:
        client.disconnect()
        client.loop_stop()

    wait_for("duplicated job to finish", lambda: finished(job_id), 60)
    wait_for(
        "the site to log the duplicates",
        lambda: f"duplicate job ignored job_id={job_id}" in compose("logs", "onprem"),
        10,
    )
    expect(site_executions().get(job_id) == 1, "a duplicated job ran more than once")


def check_safety_pause() -> None:
    for attempt in range(3):
        job_id = submit("robot-1", "A", "C")
        wait_for("job to start", lambda j=job_id: job(j)["status"] in TERMINAL | {"running"})
        if job(job_id)["status"] == "running":
            break
        if attempt == 2:
            raise CheckFailed("could not get a job running on robot-1")

    gid = sim.post("/people", json={"cell": "cell-1", "walk": False}).json()["gid"]
    started = time.monotonic()
    paused = wait_for("robot-1 to pause", lambda: robot("robot-1")["pause"], 5)
    took = time.monotonic() - started
    expect(paused["reason"] == "person_in_cell", f"paused for {paused['reason']}")
    expect(gid in paused["track_ids"], "pause does not name the person")
    expect(robot("robot-2")["pause"] is None, "robot-2 paused for a person in cell 1")

    before = robot("robot-1")["current"]
    time.sleep(1.5)
    after = robot("robot-1")["current"]
    if before and after:
        expect(before["progress"] == after["progress"], "arm kept moving while paused")
    expect(job(job_id)["status"] == "running", "pause changed the job status")

    sim.delete(f"/people/{gid}")
    wait_for("robot-1 to resume", lambda: robot("robot-1")["pause"] is None, 10)
    wait_for("paused job to finish", lambda: finished(job_id), 60)
    print(f"    paused {took * 1000:.0f} ms after the person appeared (polling the cloud)")
    print(f"    site reported {paused['latency_ms']} ms from fusion frame to halt")


def check_feed_loss_pauses_everything() -> None:
    sim.post("/feed", json={"on": False})
    try:
        for robot_id in ("robot-1", "robot-2"):
            wait_for(
                f"{robot_id} to pause on feed loss",
                lambda r=robot_id: (robot(r)["pause"] or {}).get("reason") == "safety_feed_lost",
                5,
            )
    finally:
        sim.post("/feed", json={"on": True})
    for robot_id in ("robot-1", "robot-2"):
        wait_for(f"{robot_id} to resume", lambda r=robot_id: robot(r)["pause"] is None, 10)


CHECKS = [
    ("stack is up", check_ready),
    ("robots enroll through the site", check_enrollment),
    ("jobs run on both robots and bad ones are rejected", check_jobs_on_both_robots),
    ("jobs sent while the site is down run exactly once", check_offline_site_runs_each_job_once),
    ("duplicate deliveries never run twice", check_duplicates_are_ignored),
    ("a person in cell 1 pauses robot-1 and only robot-1", check_safety_pause),
    ("losing the fusion feed pauses every robot", check_feed_loss_pauses_everything),
]


def main() -> int:
    started = now_ms()
    failed = 0
    for name, check in CHECKS:
        t0 = time.monotonic()
        try:
            check()
        except (CheckFailed, httpx.HTTPError, subprocess.CalledProcessError) as exc:
            failed += 1
            print(f"FAIL  {name}: {exc}")
            continue
        print(f"ok    {name} ({time.monotonic() - t0:.1f}s)")
    total = (now_ms() - started) / 1000
    print(f"\n{len(CHECKS) - failed}/{len(CHECKS)} checks passed in {total:.0f}s")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
