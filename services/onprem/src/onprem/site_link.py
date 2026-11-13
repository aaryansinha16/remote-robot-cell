import asyncio
import logging
import random

import aiomqtt
from pydantic import ValidationError
from robotcell_protocol import FusionFrame, ProtocolError, RobotFault, decode, topics

from .config import Settings
from .robots import Robot
from .safety import SafetyMonitor

log = logging.getLogger(__name__)


class SiteLink:
    """Connection to the site LAN broker: fusion frames in, simulated robot faults in.

    Nothing here touches the cloud. If this link drops, frames stop and the safety monitor
    pauses every robot on its own.
    """

    def __init__(self, settings: Settings, safety: SafetyMonitor, robots: dict[str, Robot]) -> None:
        self._settings = settings
        self._safety = safety
        self._robots = robots
        self._bad_frames = 0

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                async with aiomqtt.Client(
                    self._settings.site_broker_host,
                    self._settings.site_broker_port,
                    identifier=f"onprem-{self._settings.site_id}",
                    keepalive=5,
                ) as client:
                    attempt = 0
                    await client.subscribe(topics.FUSION_OBJECTS, qos=0)
                    await client.subscribe(topics.SIM_ROBOT_FAULTS, qos=1)
                    log.info("connected to site broker")
                    async for message in client.messages:
                        self._dispatch(message)
            except aiomqtt.MqttError as exc:
                log.warning("site link down: %s", exc)
            delay = random.uniform(0, min(5.0, 0.5 * 2**attempt))
            attempt += 1
            await asyncio.sleep(delay)

    def _dispatch(self, message: aiomqtt.Message) -> None:
        payload = message.payload
        if not isinstance(payload, bytes | bytearray):
            return
        if message.topic.matches(topics.FUSION_OBJECTS):
            try:
                frame = FusionFrame.model_validate_json(payload)
            except ValidationError:
                self._bad_frames += 1
                if self._bad_frames % 50 == 1:
                    log.warning("ignoring malformed fusion frames, count=%d", self._bad_frames)
                return
            self._safety.on_frame(frame)
            return
        robot_id = topics.robot_from_fault_topic(message.topic.value)
        robot = self._robots.get(robot_id or "")
        if robot is None:
            return
        try:
            fault = decode(payload).message
        except ProtocolError:
            return
        if isinstance(fault, RobotFault):
            robot.arm.apply_fault(connected=fault.connected, stalled=fault.stalled)
            log.info(
                "simulated fault robot=%s connected=%s stalled=%s",
                robot_id,
                fault.connected,
                fault.stalled,
            )
