from dataclasses import dataclass
from enum import StrEnum

ROOT = "rc/v1"

CLOUD_STATUS = f"{ROOT}/cloud/status"
FUSION_OBJECTS = "fusion/objects"
SIM_ROBOT_FAULTS = "sim/robots/+/fault"
ROBOT_TELEMETRY = "robots/+/telemetry"


class Channel(StrEnum):
    COMMANDS = "commands"
    EVENTS = "events"
    STATUS = "status"
    HEARTBEAT = "heartbeat"
    ROBOT_STATE = "robot_state"


def _site(site_id: str) -> str:
    return f"{ROOT}/sites/{site_id}"


def commands(site_id: str) -> str:
    return f"{_site(site_id)}/commands"


def events(site_id: str) -> str:
    return f"{_site(site_id)}/events"


def status(site_id: str) -> str:
    return f"{_site(site_id)}/status"


def heartbeat(site_id: str) -> str:
    return f"{_site(site_id)}/heartbeat"


def robot_state(site_id: str, robot_id: str) -> str:
    return f"{_site(site_id)}/robots/{robot_id}/state"


def sim_robot_fault(robot_id: str) -> str:
    return f"sim/robots/{robot_id}/fault"


def robot_telemetry(robot_id: str) -> str:
    return f"robots/{robot_id}/telemetry"


CLOUD_SUBSCRIPTIONS = (
    events("+"),
    status("+"),
    heartbeat("+"),
    robot_state("+", "+"),
)


@dataclass(frozen=True, slots=True)
class SiteTopic:
    site_id: str
    channel: Channel
    robot_id: str | None = None


def parse_site_topic(topic: str) -> SiteTopic | None:
    parts = topic.split("/")
    if len(parts) < 4 or "/".join(parts[:3]) != f"{ROOT}/sites":
        return None
    site_id, rest = parts[3], parts[4:]
    match rest:
        case ["commands"]:
            return SiteTopic(site_id, Channel.COMMANDS)
        case ["events"]:
            return SiteTopic(site_id, Channel.EVENTS)
        case ["status"]:
            return SiteTopic(site_id, Channel.STATUS)
        case ["heartbeat"]:
            return SiteTopic(site_id, Channel.HEARTBEAT)
        case ["robots", robot_id, "state"]:
            return SiteTopic(site_id, Channel.ROBOT_STATE, robot_id)
    return None


def robot_from_fault_topic(topic: str) -> str | None:
    parts = topic.split("/")
    if len(parts) == 4 and parts[0] == "sim" and parts[1] == "robots" and parts[3] == "fault":
        return parts[2]
    return None
