import asyncio
import contextlib
from dataclasses import dataclass

import aiosqlite
from robotcell_protocol import Message, encode, now_ms

from .db import Database


@dataclass(frozen=True, slots=True)
class OutboxItem:
    id: int
    payload: bytes


class Outbox:
    """Events for the cloud, written in the same transaction as the state change they describe.

    The payload is encoded once at insert time so a retry republishes the exact same msg_id.
    """

    def __init__(self, db: Database) -> None:
        self._db = db
        self._wake = asyncio.Event()

    async def add(self, conn: aiosqlite.Connection, message: Message) -> None:
        await conn.execute(
            "INSERT INTO outbox (payload, created_at) VALUES (?, ?)",
            (encode(message), now_ms()),
        )

    async def emit(self, message: Message) -> None:
        async with self._db.transaction() as conn:
            await self.add(conn, message)
        self.wake()

    def wake(self) -> None:
        self._wake.set()

    async def wait(self, seconds: float) -> None:
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(self._wake.wait(), seconds)
        self._wake.clear()

    async def pending(self, limit: int = 100) -> list[OutboxItem]:
        rows = await self._db.fetchall(
            "SELECT id, payload FROM outbox WHERE sent_at IS NULL ORDER BY id LIMIT ?", (limit,)
        )
        return [OutboxItem(row["id"], row["payload"]) for row in rows]

    async def mark_sent(self, item_id: int) -> None:
        async with self._db.transaction() as conn:
            await conn.execute("UPDATE outbox SET sent_at = ? WHERE id = ?", (now_ms(), item_id))

    async def backlog(self) -> int:
        row = await self._db.fetchone("SELECT COUNT(*) AS n FROM outbox WHERE sent_at IS NULL")
        return row["n"] if row else 0

    async def prune(self, keep_ms: int) -> None:
        async with self._db.transaction() as conn:
            await conn.execute(
                "DELETE FROM outbox WHERE sent_at IS NOT NULL AND sent_at < ?",
                (now_ms() - keep_ms,),
            )
