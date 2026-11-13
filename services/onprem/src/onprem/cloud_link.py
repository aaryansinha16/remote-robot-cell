import asyncio
import logging
import random
import time
from collections.abc import Callable

import aiomqtt
from paho.mqtt.packettypes import PacketTypes
from paho.mqtt.properties import Properties
from robotcell_protocol import (
    CloudStatus,
    Heartbeat,
    ProtocolError,
    RobotSnapshot,
    SiteStatus,
    decode,
    encode,
    now_ms,
    topics,
)

from .config import Settings
from .controller import SiteController
from .outbox import Outbox

log = logging.getLogger(__name__)


class CloudLink:
    """The site's only connection to the cloud. It always dials out, so no inbound port is
    needed at the customer site.

    The session is persistent (fixed client id, clean_start off, long session expiry), so the
    broker keeps QoS 1 commands for the site while it is down and delivers them on reconnect.
    """

    def __init__(
        self,
        settings: Settings,
        controller: SiteController,
        outbox: Outbox,
        *,
        boot_id: str,
        version: str,
        on_connected: Callable[[], None],
    ) -> None:
        self._settings = settings
        self._controller = controller
        self._outbox = outbox
        self._boot_id = boot_id
        self._version = version
        self._on_connected = on_connected
        self._started_at = now_ms()
        self._started_mono = time.monotonic()
        self._site_id = settings.site_id
        self._client: aiomqtt.Client | None = None
        self._connected = asyncio.Event()

    @property
    def connected(self) -> bool:
        return self._connected.is_set()

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
                log.warning("cloud link down: %s", exc)
            finally:
                self._client = None
                self._connected.clear()
            delay = random.uniform(0, min(30.0, 2.0**attempt))
            attempt += 1
            await asyncio.sleep(delay)

    async def announce_offline(self) -> None:
        if self._client is None:
            return
        try:
            await self._client.publish(
                topics.status(self._site_id),
                encode(SiteStatus(online=False, boot_id=self._boot_id, version=self._version)),
                qos=1,
                retain=True,
                timeout=2.0,
            )
            log.info("announced offline to cloud")
        except aiomqtt.MqttError as exc:
            log.warning("could not announce offline: %s", exc)

    async def drain_outbox(self) -> None:
        while True:
            await self._connected.wait()
            items = await self._outbox.pending()
            if not items:
                await self._outbox.wait(1.0)
                continue
            for item in items:
                if not await self._publish(topics.events(self._site_id), item.payload, qos=1):
                    await asyncio.sleep(0.5)
                    break
                await self._outbox.mark_sent(item.id)

    async def heartbeat(self) -> None:
        while True:
            await asyncio.sleep(self._settings.heartbeat_interval_s)
            beat = Heartbeat(
                boot_id=self._boot_id,
                uptime_s=round(time.monotonic() - self._started_mono, 1),
            )
            await self._publish(topics.heartbeat(self._site_id), encode(beat), qos=0)

    async def publish_robot(self, snapshot: RobotSnapshot) -> bool:
        return await self._publish(
            topics.robot_state(self._site_id, snapshot.robot_id),
            encode(snapshot),
            qos=1,
            retain=True,
        )

    def _make_client(self) -> aiomqtt.Client:
        settings = self._settings
        properties = Properties(PacketTypes.CONNECT)
        properties.SessionExpiryInterval = settings.session_expiry_s
        will = aiomqtt.Will(
            topics.status(self._site_id),
            encode(SiteStatus(online=False, boot_id=self._boot_id, version=self._version)),
            qos=1,
            retain=True,
        )
        return aiomqtt.Client(
            settings.cloud_broker_host,
            settings.cloud_broker_port,
            username=self._site_id,
            password=settings.cloud_broker_password,
            identifier=self._site_id,
            protocol=aiomqtt.ProtocolVersion.V5,
            clean_start=False,
            properties=properties,
            will=will,
            keepalive=10,
            timeout=10,
        )

    async def _on_connect(self, client: aiomqtt.Client) -> None:
        await client.subscribe(topics.commands(self._site_id), qos=1)
        await client.subscribe(topics.CLOUD_STATUS, qos=1)
        birth = SiteStatus(
            online=True, boot_id=self._boot_id, version=self._version, started_at=self._started_at
        )
        await client.publish(topics.status(self._site_id), encode(birth), qos=1, retain=True)
        self._client = client
        self._connected.set()
        log.info("connected to cloud broker as %s", self._site_id)
        self._outbox.wake()
        self._on_connected()

    async def _dispatch(self, message: aiomqtt.Message) -> None:
        payload = message.payload
        if not isinstance(payload, bytes | bytearray):
            return
        try:
            decoded = decode(payload)
        except ProtocolError as exc:
            log.warning("dropping bad message on %s: %s", message.topic, exc)
            return
        if isinstance(decoded.message, CloudStatus):
            log.info("cloud is %s", "online" if decoded.message.online else "offline")
            if decoded.message.online:
                self._on_connected()
            return
        try:
            await self._controller.handle(decoded.message)
        except Exception:
            log.exception("command failed type=%s msg_id=%s", decoded.message.kind, decoded.msg_id)

    async def _publish(self, topic: str, payload: bytes, *, qos: int, retain: bool = False) -> bool:
        client = self._client
        if client is None:
            return False
        try:
            await client.publish(topic, payload, qos=qos, retain=retain, timeout=5.0)
        except aiomqtt.MqttError as exc:
            log.debug("publish failed topic=%s: %s", topic, exc)
            return False
        return True
