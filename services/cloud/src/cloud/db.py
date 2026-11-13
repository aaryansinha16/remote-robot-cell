import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import aiosqlite

SCHEMA = """
CREATE TABLE IF NOT EXISTS sites (
    site_id     TEXT PRIMARY KEY,
    online      INTEGER NOT NULL DEFAULT 0,
    boot_id     TEXT,
    version     TEXT,
    last_seen   INTEGER,
    changed_at  INTEGER
);

CREATE TABLE IF NOT EXISTS robots (
    robot_id            TEXT PRIMARY KEY,
    site_id             TEXT NOT NULL,
    snapshot            TEXT NOT NULL,
    request             TEXT,
    request_attempts    INTEGER NOT NULL DEFAULT 0,
    request_next_at     INTEGER,
    last_error          TEXT,
    updated_at          INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs (
    id                   INTEGER PRIMARY KEY AUTOINCREMENT,
    job_id               TEXT NOT NULL UNIQUE,
    request_id           TEXT UNIQUE,
    site_id              TEXT NOT NULL,
    robot_id             TEXT NOT NULL,
    pick                 TEXT NOT NULL,
    place                TEXT NOT NULL,
    status               TEXT NOT NULL,
    seq                  INTEGER NOT NULL DEFAULT 0,
    reason               TEXT,
    created_at           INTEGER NOT NULL,
    updated_at           INTEGER NOT NULL,
    start_deadline       INTEGER NOT NULL,
    send_attempts        INTEGER NOT NULL DEFAULT 0,
    next_send_at         INTEGER,
    cancel_requested_at  INTEGER,
    cancel_attempts      INTEGER NOT NULL DEFAULT 0,
    cancel_next_at       INTEGER,
    cancel_error         TEXT
);
CREATE INDEX IF NOT EXISTS jobs_by_status ON jobs (status);

CREATE TABLE IF NOT EXISTS job_events (
    job_id       TEXT NOT NULL,
    seq          INTEGER NOT NULL,
    status       TEXT NOT NULL,
    reason       TEXT,
    at           INTEGER NOT NULL,
    received_at  INTEGER NOT NULL,
    PRIMARY KEY (job_id, seq)
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
