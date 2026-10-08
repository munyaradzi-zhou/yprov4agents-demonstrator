"""
envelope.py — the on-the-wire message format for the streaming provenance bus.

Mirrors Sana's `ProvRecordEnvelope` from the `feature/streaming-bus-mvp` branch
(github.com/HPCI-Lab/yprov4agenticai): a small, JSON-serialisable envelope that
carries a slice of a W3C PROV document across the bus, tagged with a session,
a sequence number, the producer, and a lifecycle event type.

This is the single shared contract every container speaks — producers, operational
agents, and meta agents all publish and consume `ProvRecordEnvelope`s. Keeping the
format here (not per-agent) is what lets any box talk to any other box.
"""

from __future__ import annotations

import datetime
import json
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from typing import Any


class ProvEventType(str, Enum):
    """Lifecycle events that punctuate a provenance stream on the bus."""

    STREAM_START = "STREAM_START"  # a new document begins (carries prefix/metadata)
    RECORD_CHUNK = "RECORD_CHUNK"  # a slice of the document (activities/entities/…)
    STREAM_END = "STREAM_END"  # the document is complete → consumer may act
    HEARTBEAT = "HEARTBEAT"  # liveness ping (unused in R1, reserved)
    ERROR = "ERROR"  # a producer/agent signalled a failure


def _utc_now() -> str:
    return datetime.datetime.now(datetime.UTC).isoformat()


@dataclass
class ProvRecordEnvelope:
    """One message on the bus. `payload` holds a slice of a PROV-JSON document."""

    session_id: str
    sequence_no: int
    producer_id: str
    event_type: str = ProvEventType.RECORD_CHUNK.value
    payload: dict[str, Any] = field(default_factory=dict)
    timestamp: str = field(default_factory=_utc_now)
    envelope_id: str = field(default_factory=lambda: str(uuid.uuid4()))

    # ── serialization: this IS the broker boundary ─────────────────────────────
    def to_json(self) -> str:
        return json.dumps(asdict(self))

    def to_bytes(self) -> bytes:
        return self.to_json().encode("utf-8")

    @classmethod
    def from_dict(cls, d: dict[str, Any]) -> ProvRecordEnvelope:
        return cls(
            session_id=d["session_id"],
            sequence_no=int(d.get("sequence_no", 0)),
            producer_id=d.get("producer_id", "unknown"),
            event_type=d.get("event_type", ProvEventType.RECORD_CHUNK.value),
            payload=d.get("payload", {}),
            timestamp=d.get("timestamp", _utc_now()),
            envelope_id=d.get("envelope_id", str(uuid.uuid4())),
        )

    @classmethod
    def from_bytes(cls, data: bytes) -> ProvRecordEnvelope:
        return cls.from_dict(json.loads(data.decode("utf-8")))
