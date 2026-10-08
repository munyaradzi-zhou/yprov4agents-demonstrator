"""
base_agent.py — the shared machinery every streaming agent reuses.

A `StreamingAgent`:
  1. connects to the bus and subscribes to an input subject,
  2. buffers the incoming chunk stream per session (STREAM_START..RECORD_CHUNK..STREAM_END),
  3. on STREAM_END, calls `process(session, merged_doc, source_subject)` — the ONLY method
     each concrete agent implements — which returns (result_prov_json, log_line),
  4. re-emits that result as its own chunk stream on its output subject,
  5. announces readiness at startup so the producer waits for it.

The agent's actual work (summarise / visualise / govern) lives in `process`; everything
about transport, buffering, and re-emitting is handled here once. This is why adding an
agent is small — you write one method.

`stream_document(...)` is the shared emitter used by both the producer and every agent
to put a PROV-JSON document onto the bus as START → CHUNK(s) → END.
"""

from __future__ import annotations

import asyncio
from typing import Any

from .envelope import ProvEventType, ProvRecordEnvelope
from .net_bus import NetworkBus

# PROV-JSON top-level sections we know how to merge when reassembling a stream.
_PROV_SECTIONS = (
    "prefix",
    "agent",
    "activity",
    "entity",
    "used",
    "wasGeneratedBy",
    "wasInformedBy",
    "wasDerivedFrom",
    "wasAssociatedWith",
    "actedOnBehalfOf",
)


def _merge_payload(target: dict[str, Any], payload: dict[str, Any]) -> None:
    """Merge one chunk's payload into the reassembled document."""
    if not isinstance(payload, dict):
        return
    for section, value in payload.items():
        if isinstance(value, dict):
            target.setdefault(section, {}).update(value)
        else:
            target[section] = value


def _chunk_document(doc: dict[str, Any]) -> list[dict[str, Any]]:
    """Split a PROV-JSON doc into a few section-wise chunks (to demonstrate streaming)."""
    chunks: list[dict[str, Any]] = []
    head = {k: doc[k] for k in ("prefix", "agent") if k in doc}
    if head:
        chunks.append(head)
    if "activity" in doc:
        chunks.append({"activity": doc["activity"]})
    if "entity" in doc:
        chunks.append({"entity": doc["entity"]})
    rels = {
        k: doc[k]
        for k in doc
        if k in _PROV_SECTIONS and k not in ("prefix", "agent", "activity", "entity")
    }
    if rels:
        chunks.append(rels)
    return chunks or [doc]


async def stream_document(
    bus: NetworkBus,
    subject: str,
    *,
    session_id: str,
    producer_id: str,
    doc: dict[str, Any],
    delay_ms: float = 0.0,
) -> int:
    """Emit `doc` onto `subject` as STREAM_START → RECORD_CHUNK(s) → STREAM_END."""
    seq = 0

    async def send(event_type: str, payload: dict[str, Any]) -> None:
        nonlocal seq
        seq += 1
        env = ProvRecordEnvelope(
            session_id=session_id,
            sequence_no=seq,
            producer_id=producer_id,
            event_type=event_type,
            payload=payload,
        )
        await bus.publish(subject, env)
        if delay_ms:
            await asyncio.sleep(delay_ms / 1000.0)

    await send(ProvEventType.STREAM_START.value, {"prefix": doc.get("prefix", {})})
    for chunk in _chunk_document(doc):
        await send(ProvEventType.RECORD_CHUNK.value, chunk)
    await send(ProvEventType.STREAM_END.value, {})
    return seq


class StreamingAgent:
    """Base class: subscribe → buffer a stream → process → emit. Override `process`."""

    #: subject pattern this agent listens on (NATS syntax, e.g. "producer.>")
    input_subject: str = "producer.>"
    #: Prefix for this agent's output subject. The full subject is
    #: f"{output_prefix}.{role}.{session}".
    output_prefix: str = "agent.lifecycle"

    def __init__(self, role: str, bus: NetworkBus) -> None:
        self.role = role
        self.bus = bus
        # merged docs, keyed by session_id, still streaming
        self._buffers: dict[str, dict[str, Any]] = {}

    # ── the one method each concrete agent implements ──────────────────────────
    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        """Do the agent's work on a completed document.

        Returns (prov_json, log_line):
          prov_json — this agent's OWN W3C PROV-JSON (reflexive lifecycle) to re-emit,
          log_line  — a human-readable one-liner printed to the console.
        """
        raise NotImplementedError

    # ── lifecycle ──────────────────────────────────────────────────────────────
    async def start(self) -> None:
        await self.bus.subscribe(self.input_subject, self._on_envelope)
        # announce readiness so the producer waits for us before streaming
        ready = ProvRecordEnvelope(
            session_id="_system",
            sequence_no=0,
            producer_id=self.role,
            event_type=ProvEventType.HEARTBEAT.value,
            payload={"role": self.role},
        )
        await self.bus.publish(f"system.ready.{self.role}", ready)
        print(f"[{self.role}] ready — listening on '{self.input_subject}'", flush=True)

    async def _on_envelope(self, subject: str, env: ProvRecordEnvelope) -> None:
        sid = env.session_id
        if env.event_type == ProvEventType.STREAM_START.value:
            self._buffers[sid] = {"_subject": subject}
            _merge_payload(self._buffers[sid], env.payload)
            return
        buf = self._buffers.setdefault(sid, {"_subject": subject})
        if env.event_type == ProvEventType.RECORD_CHUNK.value:
            _merge_payload(buf, env.payload)
            return
        if env.event_type == ProvEventType.STREAM_END.value:
            src = buf.pop("_subject", subject)
            doc = {k: v for k, v in buf.items() if not k.startswith("_")}
            await self._finalize(sid, doc, src)
            self._buffers.pop(sid, None)

    async def _finalize(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> None:
        loop = asyncio.get_event_loop()
        # run the (sync) domain logic off the event loop so a slow LLM call
        # doesn't block the subscription.
        prov_json, log_line = await loop.run_in_executor(
            None, self.process, session_id, doc, source_subject
        )
        print(f"[{self.role}] {log_line}", flush=True)
        # re-emit this agent's own PROV as a stream on its output subject
        out_session = f"{session_id}::{self.role}"
        out_subject = f"{self.output_prefix}.{self.role}.{session_id}"
        await stream_document(
            self.bus, out_subject, session_id=out_session, producer_id=self.role, doc=prov_json
        )
