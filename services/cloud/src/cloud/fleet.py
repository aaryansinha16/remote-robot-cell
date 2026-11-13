import logging
from collections.abc import Callable

from robotcell_protocol import (
    CommandResult,
    Enrollment,
    Heartbeat,
    RobotSnapshot,
    SiteStatus,
    now_ms,
)

from .config import Settings
from .db import Database
from .errors import NotFound
from .hub import Hub
from .schemas import RobotView, SiteView

log = logging.getLogger(__name__)


class FleetService:
    """What the cloud knows about the site and its robots: always the last thing it was told."""

    def __init__(
        self,
        db: Database,
        hub: Hub,
        settings: Settings,
        *,
        wake: Callable[[], None],
        clock: Callable[[], int] = now_ms,
    ) -> None:
        self._db = db
        self._hub = hub
        self._settings = settings
        self._wake = wake
        self._clock = clock

    async def site(self) -> SiteView:
        row = await self._db.fetchone(
            "SELECT * FROM sites WHERE site_id = ?", (self._settings.site_id,)
        )
        return (
            SiteView.from_row(row)
            if row
            else SiteView(site_id=self._settings.site_id, online=False)
        )

    async def online_sites(self) -> set[str]:
        rows = await self._db.fetchall("SELECT site_id FROM sites WHERE online = 1")
        return {row["site_id"] for row in rows}

    async def robots(self) -> list[RobotView]:
        rows = await self._db.fetchall("SELECT * FROM robots ORDER BY robot_id")
        return [RobotView.from_row(row) for row in rows]

    async def robot(self, robot_id: str) -> RobotView:
        row = await self._db.fetchone("SELECT * FROM robots WHERE robot_id = ?", (robot_id,))
        if row is None:
            raise NotFound(f"unknown robot {robot_id}")
        return RobotView.from_row(row)

    async def apply_site_status(self, site_id: str, status: SiteStatus) -> bool:
        """Returns True when the site has just come online, so the caller can resync it."""
        now = self._clock()
        async with self._db.transaction() as conn:
            async with conn.execute("SELECT * FROM sites WHERE site_id = ?", (site_id,)) as cur:
                row = await cur.fetchone()
            if (
                not status.online
                and row is not None
                and row["boot_id"]
                and status.boot_id
                and status.boot_id != row["boot_id"]
            ):
                log.info("ignoring offline notice from an earlier boot site=%s", site_id)
                return False
            was_online = bool(row and row["online"])
            new_boot = status.online and (row is None or row["boot_id"] != status.boot_id)
            await conn.execute(
                """
                INSERT INTO sites (site_id, online, boot_id, version, last_seen, changed_at)
                VALUES (?, ?, ?, ?, ?, ?)
                ON CONFLICT (site_id) DO UPDATE SET
                    online = excluded.online,
                    boot_id = COALESCE(excluded.boot_id, sites.boot_id),
                    version = COALESCE(excluded.version, sites.version),
                    last_seen = CASE WHEN excluded.online THEN excluded.last_seen
                                     ELSE sites.last_seen END,
                    changed_at = CASE WHEN sites.online != excluded.online THEN excluded.changed_at
                                      ELSE sites.changed_at END
                """,
                (site_id, status.online, status.boot_id, status.version, now, now),
            )
        if was_online != status.online:
            log.info("site %s is %s", site_id, "online" if status.online else "offline")
        self._hub.publish("site", await self.site())
        return status.online and (not was_online or new_boot)

    async def apply_heartbeat(self, site_id: str, beat: Heartbeat) -> bool:
        row = await self._db.fetchone(
            "SELECT online, boot_id FROM sites WHERE site_id = ?", (site_id,)
        )
        if row is None or not row["online"] or row["boot_id"] != beat.boot_id:
            return await self.apply_site_status(
                site_id, SiteStatus(online=True, boot_id=beat.boot_id)
            )
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE sites SET last_seen = ? WHERE site_id = ?", (self._clock(), site_id)
            )
        self._hub.publish("site", await self.site())
        return False

    async def expire_heartbeats(self) -> None:
        cutoff = self._clock() - int(self._settings.heartbeat_timeout_s * 1000)
        rows = await self._db.fetchall(
            "SELECT site_id FROM sites WHERE online = 1 AND last_seen < ?", (cutoff,)
        )
        for row in rows:
            log.warning("no heartbeat from site %s, marking offline", row["site_id"])
            await self.apply_site_status(row["site_id"], SiteStatus(online=False))

    async def apply_robot(self, site_id: str, snapshot: RobotSnapshot) -> None:
        enrolled = snapshot.enrollment is Enrollment.ENROLLED
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO robots (robot_id, site_id, snapshot, updated_at)
                VALUES (?, ?, ?, ?)
                ON CONFLICT (robot_id) DO UPDATE SET
                    site_id = excluded.site_id,
                    snapshot = excluded.snapshot,
                    updated_at = excluded.updated_at,
                    request = CASE
                        WHEN request = 'enroll' AND ? THEN NULL
                        WHEN request = 'unenroll' AND NOT ? THEN NULL
                        ELSE request END
                """,
                (
                    snapshot.robot_id,
                    site_id,
                    snapshot.model_dump_json(),
                    self._clock(),
                    enrolled,
                    enrolled,
                ),
            )
        self._hub.publish("robot", await self.robot(snapshot.robot_id))

    async def request_enrollment(self, robot_id: str, enroll: bool) -> RobotView:
        robot = await self.robot(robot_id)
        want = Enrollment.ENROLLED if enroll else Enrollment.UNENROLLED
        if robot.enrollment is want and robot.request is None:
            return robot
        now = self._clock()
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                UPDATE robots SET request = ?, request_attempts = 0, request_next_at = ?,
                                  last_error = NULL
                WHERE robot_id = ?
                """,
                ("enroll" if enroll else "unenroll", now, robot_id),
            )
        view = await self.robot(robot_id)
        self._hub.publish("robot", view)
        self._wake()
        return view

    async def apply_enrollment_result(self, result: CommandResult) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "UPDATE robots SET request = NULL, last_error = ? WHERE robot_id = ?",
                (None if result.ok else result.reason, result.target),
            )
        if not result.ok:
            verb = "enroll" if result.command == "robot.enroll" else "unenroll"
            self._hub.notice("warning", f"Site refused to {verb} {result.target}: {result.reason}")
        self._hub.publish("robot", await self.robot(result.target))
