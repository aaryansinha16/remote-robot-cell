import asyncio
import contextlib
import json
import logging
from typing import Any

from fastapi import WebSocket, WebSocketDisconnect
from pydantic import BaseModel

log = logging.getLogger(__name__)

_CLOSE = "__close__"


class Hub:
    """Fans live updates out to connected browsers.

    Each client gets its own bounded queue so one slow tab cannot stall the others. A client
    that falls too far behind is dropped and will reconnect and reload the snapshot.
    """

    def __init__(self, max_backlog: int = 500) -> None:
        self._clients: set[asyncio.Queue[str]] = set()
        self._max_backlog = max_backlog

    @property
    def client_count(self) -> int:
        return len(self._clients)

    def publish(self, kind: str, data: BaseModel | dict[str, Any]) -> None:
        payload = data.model_dump(mode="json") if isinstance(data, BaseModel) else data
        message = json.dumps({"type": kind, "data": payload})
        for queue in list(self._clients):
            try:
                queue.put_nowait(message)
            except asyncio.QueueFull:
                log.warning("dropping a websocket client that fell behind")
                self._clients.discard(queue)
                while not queue.empty():
                    queue.get_nowait()
                queue.put_nowait(_CLOSE)

    def notice(self, level: str, message: str) -> None:
        self.publish("notice", {"level": level, "message": message})

    async def serve(self, websocket: WebSocket, snapshot: BaseModel) -> None:
        await websocket.accept()
        queue: asyncio.Queue[str] = asyncio.Queue(self._max_backlog)
        self._clients.add(queue)
        try:
            await websocket.send_text(
                json.dumps({"type": "snapshot", "data": snapshot.model_dump(mode="json")})
            )
            reader = asyncio.create_task(self._drain_incoming(websocket))
            try:
                while not reader.done():
                    getter = asyncio.create_task(queue.get())
                    done, _ = await asyncio.wait(
                        {getter, reader}, return_when=asyncio.FIRST_COMPLETED
                    )
                    if getter not in done:
                        getter.cancel()
                        break
                    message = getter.result()
                    if message == _CLOSE:
                        await websocket.close(code=1013)
                        break
                    await websocket.send_text(message)
            finally:
                reader.cancel()
                with contextlib.suppress(asyncio.CancelledError, WebSocketDisconnect):
                    await reader
        except WebSocketDisconnect:
            pass
        finally:
            self._clients.discard(queue)

    @staticmethod
    async def _drain_incoming(websocket: WebSocket) -> None:
        while True:
            await websocket.receive_text()
