import asyncio
import contextlib
import logging
import random

import aiomqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from robotcell_protocol import (
    CloudStatus,
    CommandResult,
    Heartbeat,
    JobStatusUpdate,
    ProtocolError,
    RobotSnapshot,
    SiteStatus,
    decode,
    encode,
    topics,
)

from .config import Settings
from .fleet import FleetService
from .jobs import JobService
from .reconciler import Reconciler

log = logging.getLogger(__name__)


class Bridge:
    """The cloud side of the MQTT link. Like the site, it keeps a persistent session so reports
    sent while the cloud API was down are delivered when it comes back."""

    def __init__(
        self,
        settings: Settings,
        fleet: FleetService,
        jobs: JobService,
        reconciler: Reconciler,
    ) -> None:
        self._settings = settings
        self._fleet = fleet
        self._jobs = jobs
        self._reconciler = reconciler
        self._client: aiomqtt.Client | None = None

    @property
    def connected(self) -> bool:
        return self._client is not None

    async def run(self) -> None:
        attempt = 0
        while True:
            try:
                async with self._make_client() as client:
                    attempt = 0
                    await self._on_connect(client)
                    async for message in client.messages:
                        await self._dispatch(message)
            except aiomqtt.MqttError as exc:
                log.warning("broker connection lost: %s", exc)
            finally:
                self._client = None
            delay = random.uniform(0, min(30.0, 2.0**attempt))
            attempt += 1
            await asyncio.sleep(delay)

    async def publish(self, topic: str, payload: bytes, *, qos: int = 1) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            await client.publish(topic, payload, qos=qos, timeout=5.0)
        except aiomqtt.MqttError as exc:
            log.warning("publish failed topic=%s: %s", topic, exc)
            return False
        return True

    async def announce_offline(self) -> None:
        client = self._client
        if client is None:
            return
        with contextlib.suppress(aiomqtt.MqttError):
            await client.publish(
                topics.CLOUD_STATUS, encode(CloudStatus(online=False)), qos=1, retain=True
            )

    def _make_client(self) -> aiomqtt.Client:
        settings = self._settings
        properties = Properties(PacketTypes.CONNECT)
        properties.SessionExpiryInterval = settings.session_expiry_s
        return aiomqtt.Client(
            settings.broker_host,
            settings.broker_port,
            username=settings.broker_username,
            password=settings.broker_password,
            identifier=settings.client_id,
            protocol=aiomqtt.ProtocolVersion.V5,
            clean_start=False,
            properties=properties,
            will=aiomqtt.Will(
                topics.CLOUD_STATUS, encode(CloudStatus(online=False)), qos=1, retain=True
            ),
            keepalive=10,
            timeout=10,
        )

    async def _on_connect(self, client: aiomqtt.Client) -> None:
        for topic in topics.CLOUD_SUBSCRIPTIONS:
            await client.subscribe(topic, qos=1)
        await client.publish(
            topics.CLOUD_STATUS, encode(CloudStatus(online=True)), qos=1, retain=True
        )
        self._client = client
        log.info("connected to broker as %s", self._settings.client_id)
        for site_id in await self._fleet.online_sites():
            await self._reconciler.resync(site_id)

    async def _dispatch(self, message: aiomqtt.Message) -> None:
        parsed = topics.parse_site_topic(message.topic.value)
        payload = message.payload
        if parsed is None or not isinstance(payload, bytes | bytearray):
            return
        try:
            decoded = decode(payload)
        except ProtocolError as exc:
            log.warning("dropping bad message on %s: %s", message.topic, exc)
            return
        try:
            await self._apply(parsed.site_id, decoded.message)
        except Exception:
            log.exception("failed to apply %s from site %s", decoded.message.kind, parsed.site_id)

    async def _apply(self, site_id: str, message: object) -> None:
        match message:
            case JobStatusUpdate():
                await self._jobs.apply_status(message)
            case RobotSnapshot():
                await self._fleet.apply_robot(site_id, message)
            case SiteStatus():
                if await self._fleet.apply_site_status(site_id, message):
                    await self._reconciler.resync(site_id)
            case Heartbeat():
                if await self._fleet.apply_heartbeat(site_id, message):
                    await self._reconciler.resync(site_id)
            case CommandResult(command="job.cancel"):
                if not message.ok:
                    await self._jobs.refuse_cancel(message.target, message.reason)
            case CommandResult():
                await self._fleet.apply_enrollment_result(message)
