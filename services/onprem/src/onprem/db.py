import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS jobs (
    job_id          TEXT PRIMARY KEY,
    robot_id        TEXT,
    pick            TEXT,
    place           TEXT,
    created_at      INTEGER,
    start_deadline  INTEGER,
    status          TEXT NOT NULL,
    seq             INTEGER NOT NULL,
    reason          TEXT,
    queue_pos       INTEGER,
    received_at     INTEGER NOT NULL,
    updated_at      INTEGER NOT NULL,
    started_at      INTEGER,
    finished_at     INTEGER,
    executions      INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs (status, queue_pos);

CREATE TABLE IF NOT EXISTS outbox (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    payload     BLOB NOT NULL,
    created_at  INTEGER NOT NULL,
    sent_at     INTEGER
);
CREATE INDEX IF NOT EXISTS outbox_unsent ON outbox (id) WHERE sent_at IS NULL;

CREATE TABLE IF NOT EXISTS enrollments (
    robot_id     TEXT PRIMARY KEY,
    serial       TEXT NOT NULL,
    enrolled_at  INTEGER NOT NULL
);
"""


class Database:
    """Single SQLite connection; every write goes through one serialized transaction."""

    def __init__(self, path: Path | str) -> None:
        self._path = str(path)
        self._conn: aiosqlite.Connection | None = None
        self._lock = asyncio.Lock()

    async def open(self) -> None:
        if self._path != ":memory:":
            Path(self._path).parent.mkdir(parents=True, exist_ok=True)
        self._conn = await aiosqlite.connect(self._path, isolation_level=None)
        self._conn.row_factory = aiosqlite.Row
        await self._conn.execute("PRAGMA journal_mode=WAL")
        await self._conn.execute("PRAGMA synchronous=FULL")
        await self._conn.executescript(SCHEMA)

    async def close(self) -> None:
        if self._conn is not None:
            await self._conn.close()
            self._conn = None

    @property
    def conn(self) -> aiosqlite.Connection:
        if self._conn is None:
            raise RuntimeError("database is not open")
        return self._conn

    @asynccontextmanager
    async def transaction(self) -> AsyncIterator[aiosqlite.Connection]:
        async with self._lock:
            await self.conn.execute("BEGIN IMMEDIATE")
            try:
                yield self.conn
            except BaseException:
                await self.conn.execute("ROLLBACK")
                raise
            await self.conn.execute("COMMIT")

    async def fetchall(self, sql: str, params: Iterable[Any] = ()) -> list[aiosqlite.Row]:
        async with self._lock, self.conn.execute(sql, tuple(params)) as cursor:
            return list(await cursor.fetchall())

    async def fetchone(self, sql: str, params: Iterable[Any] = ()) -> aiosqlite.Row | None:
        async with self._lock, self.conn.execute(sql, tuple(params)) as cursor:
            return await cursor.fetchone()
