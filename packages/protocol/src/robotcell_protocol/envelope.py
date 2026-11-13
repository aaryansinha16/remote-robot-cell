import time
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, ValidationError

from .messages import REGISTRY, Message

PROTOCOL_VERSION = 1


class ProtocolError(ValueError):
    pass


class Envelope(BaseModel):
    v: int
    type: str
    msg_id: str
    ts: int
    data: dict[str, Any]


@dataclass(frozen=True, slots=True)
class Decoded:
    msg_id: str
    ts: int
    message: Message


def now_ms() -> int:
    return time.time_ns() // 1_000_000


def new_id() -> str:
    return uuid.uuid4().hex


def encode(message: Message, *, msg_id: str | None = None, ts: int | None = None) -> bytes:
    envelope = Envelope(
        v=PROTOCOL_VERSION,
        type=message.kind,
        msg_id=msg_id or new_id(),
        ts=ts if ts is not None else now_ms(),
        data=message.model_dump(mode="json"),
    )
    return envelope.model_dump_json().encode()


def decode(payload: bytes | str) -> Decoded:
    try:
        envelope = Envelope.model_validate_json(payload)
    except ValidationError as exc:
        raise ProtocolError(f"malformed envelope: {exc.error_count()} errors") from exc
    if envelope.v != PROTOCOL_VERSION:
        raise ProtocolError(f"unsupported protocol version {envelope.v}")
    model = REGISTRY.get(envelope.type)
    if model is None:
        raise ProtocolError(f"unknown message type {envelope.type!r}")
    try:
        message = model.model_validate(envelope.data)
    except ValidationError as exc:
        raise ProtocolError(f"invalid {envelope.type} payload: {exc.error_count()} errors") from exc
    return Decoded(msg_id=envelope.msg_id, ts=envelope.ts, message=message)
