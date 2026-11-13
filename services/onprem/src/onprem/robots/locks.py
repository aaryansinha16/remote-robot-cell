import asyncio
from collections.abc import AsyncIterator, Iterable
from contextlib import asynccontextmanager


class StationLocks:
    """Mutual exclusion per station. Matters for station C, which both arms can reach.

    An arm holds at most one station at a time, so two arms can never deadlock.
    """

    def __init__(self, stations: Iterable[str]) -> None:
        self._locks = {station: asyncio.Lock() for station in stations}
        self._holders: dict[str, str] = {}

    def holder(self, station: str) -> str | None:
        return self._holders.get(station)

    def is_busy(self, station: str) -> bool:
        return self._locks[station].locked()

    @asynccontextmanager
    async def hold(self, station: str, owner: str) -> AsyncIterator[None]:
        async with self._locks[station]:
            self._holders[station] = owner
            try:
                yield
            finally:
                self._holders.pop(station, None)
