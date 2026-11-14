"""Submit a batch of random pick and place jobs through the cloud API.

uv run python scripts/submit_jobs.py --count 6
uv run python scripts/submit_jobs.py --invalid
"""

import argparse
import random
import sys

import httpx


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--api", default="http://localhost:8000/api")
    parser.add_argument("--count", type=int, default=6)
    parser.add_argument("--robot", help="only send to this robot")
    parser.add_argument(
        "--invalid",
        action="store_true",
        help="send jobs the site must reject because a station is out of reach",
    )
    args = parser.parse_args()

    with httpx.Client(base_url=args.api, timeout=5) as client:
        robots = [r for r in client.get("/robots").json() if r["enrollment"] == "enrolled"]
        if args.robot:
            robots = [r for r in robots if r["robot_id"] == args.robot]
        if not robots:
            print("no enrolled robots, run `make enroll` first", file=sys.stderr)
            return 1

        if args.invalid:
            jobs = [
                {"robot_id": "robot-1", "pick": "A", "place": "D"},
                {"robot_id": "robot-2", "pick": "B", "place": "E"},
            ]
        else:
            jobs = []
            for i in range(args.count):
                robot = robots[i % len(robots)]
                pick, place = random.sample(robot["stations"], 2)
                jobs.append({"robot_id": robot["robot_id"], "pick": pick, "place": place})

        for body in jobs:
            response = client.post("/jobs", json=body)
            if response.is_success:
                job = response.json()
                print(f"#{job['id']:<4} {job['robot_id']}  {job['pick']} to {job['place']}")
            else:
                print(f"refused by the cloud: {response.json()['detail']}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
