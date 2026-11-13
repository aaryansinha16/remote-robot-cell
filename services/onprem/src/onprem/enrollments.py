from robotcell_protocol import now_ms

from .db import Database


class EnrollmentStore:
    def __init__(self, db: Database) -> None:
        self._db = db

    async def enrolled(self) -> dict[str, str]:
        rows = await self._db.fetchall("SELECT robot_id, serial FROM enrollments")
        return {row["robot_id"]: row["serial"] for row in rows}

    async def enroll(self, robot_id: str, serial: str) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                """
                INSERT INTO enrollments (robot_id, serial, enrolled_at) VALUES (?, ?, ?)
                ON CONFLICT (robot_id) DO UPDATE SET serial = excluded.serial
                """,
                (robot_id, serial, now_ms()),
            )

    async def unenroll(self, robot_id: str) -> None:
        async with self._db.transaction() as conn:
            await conn.execute("DELETE FROM enrollments WHERE robot_id = ?", (robot_id,))
