"""
base_agent.py — the shared machinery every streaming agent reuses.

A `StreamingAgent`:
  1. connects to the bus and subscribes to an input subject,
  2. buffers the incoming chunk stream per session (STREAM_START..RECORD_CHUNK..STREAM_END),
  3. on STREAM_END, checks that every envelope of the stream arrived, and if so calls
     `process(session, merged_doc, source_subject)` — the ONLY method each concrete agent
     implements — which returns (result_prov_json, log_line). If any envelope is missing
     it records an INCOMPLETE decision instead of processing (see `missing_envelopes`),
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
from .net_bus import Bus
from .util import agent_tracker, energy_tracker

#: The verdict an agent records when the stream it was handed has a gap. Not a judgement
#: about the input's content — the agent never saw all of it — but about its delivery.
VERDICT_INCOMPLETE = "INCOMPLETE"

#: Set on the STREAM_END of an agent's own output when that output records an INCOMPLETE
#: input. CA1 counts a consumer's STREAM_END as "this consumer used the record"; a consumer
#: that only recorded a gap did not use it, and must not be counted.
INPUT_COMPLETE = "input_complete"

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


def missing_envelopes(seen: set[int], end_sequence_no: int) -> list[int]:
    """The sequence numbers a stream carried and this receiver never saw.

    `stream_document` numbers a stream's envelopes 1, 2, 3, … without gaps, ending with
    STREAM_END, so STREAM_END's own sequence number is the length of the stream and needs
    no separate count. A stream is complete exactly when every number below it arrived.

    This matters more than it looks. The bus is at-most-once, and before this check an
    agent processed whatever it had buffered when STREAM_END arrived: a lost relations
    chunk produced a well-formed record with activities, entities and no lineage, which
    governance passed and storage persisted. Losing a record is disclosed in the paper;
    turning a lost envelope into a plausible, wrong record would not have been.

    A sender that does not number its envelopes (every one 0) cannot be checked, and is
    reported as complete rather than as entirely missing.
    """
    if end_sequence_no <= 1:
        return []
    return [n for n in range(1, end_sequence_no) if n not in seen]


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
    bus: Bus,
    subject: str,
    *,
    session_id: str,
    producer_id: str,
    doc: dict[str, Any],
    delay_ms: float = 0.0,
    end_payload: dict[str, Any] | None = None,
) -> int:
    """Emit `doc` onto `subject` as STREAM_START → RECORD_CHUNK(s) → STREAM_END.

    `end_payload` travels on STREAM_END; it is how an agent marks an output that records
    an incomplete input (:data:`INPUT_COMPLETE`).
    """
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
    await send(ProvEventType.STREAM_END.value, dict(end_payload or {}))
    return seq


class StreamingAgent:
    """Base class: subscribe → buffer a stream → process → emit. Override `process`."""

    #: subject pattern this agent listens on (NATS syntax, e.g. "producer.>")
    input_subject: str = "producer.>"
    #: Prefix for this agent's output subject. The full subject is
    #: f"{output_prefix}.{role}.{session}".
    output_prefix: str = "agent.lifecycle"
    #: The name this agent records itself under in its own PROV.
    agent_label: str = "StreamingAgent"

    def __init__(self, role: str, bus: Bus) -> None:
        self.role = role
        self.bus = bus
        # merged docs, keyed by session_id, still streaming
        self._buffers: dict[str, dict[str, Any]] = {}
        # One energy tracker for the life of the agent, or None when energy accounting is
        # off (the default). Per-agent rather than per-message: a backend that samples
        # hardware counters pays a start-up cost, and restarting it per message would
        # mostly measure the restart.
        self.energy = energy_tracker()

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
            self._buffers[sid] = {"_subject": subject, "_seen": {env.sequence_no}}
            _merge_payload(self._buffers[sid], env.payload)
            return
        buf = self._buffers.setdefault(sid, {"_subject": subject})
        seen: set[int] = buf.setdefault("_seen", set())
        seen.add(env.sequence_no)
        if env.event_type == ProvEventType.RECORD_CHUNK.value:
            _merge_payload(buf, env.payload)
            return
        if env.event_type == ProvEventType.STREAM_END.value:
            src = buf.pop("_subject", subject)
            missing = missing_envelopes(seen, env.sequence_no)
            doc = {k: v for k, v in buf.items() if not k.startswith("_")}
            self._buffers.pop(sid, None)
            await self._finalize(sid, doc, src, missing=missing, expected=env.sequence_no)

    async def _finalize(
        self,
        session_id: str,
        doc: dict[str, Any],
        source_subject: str,
        *,
        missing: list[int] | None = None,
        expected: int = 0,
    ) -> None:
        loop = asyncio.get_running_loop()
        # run the (sync) domain logic off the event loop so a slow LLM call
        # doesn't block the subscription.
        end_payload: dict[str, Any] = {}
        if missing:
            prov_json, log_line = await loop.run_in_executor(
                None, self.record_incomplete, session_id, source_subject, missing, expected
            )
            end_payload = {INPUT_COMPLETE: False, "missing": missing}
        else:
            prov_json, log_line = await loop.run_in_executor(
                None, self.process, session_id, doc, source_subject
            )
        print(f"[{self.role}] {log_line}", flush=True)
        # re-emit this agent's own PROV as a stream on its output subject
        out_session = f"{session_id}::{self.role}"
        out_subject = f"{self.output_prefix}.{self.role}.{session_id}"
        await stream_document(
            self.bus,
            out_subject,
            session_id=out_session,
            producer_id=self.role,
            doc=prov_json,
            end_payload=end_payload,
        )

    def record_incomplete(
        self, session_id: str, source_subject: str, missing: list[int], expected: int
    ) -> tuple[dict[str, Any], str]:
        """Record that the input arrived with a gap, instead of acting on what did arrive.

        The agent does not process a partial document, because every output it could
        produce would describe an input it never fully saw. It records the gap as its
        decision, so the absence is in the provenance — governed like any other record —
        rather than only in a log line.
        """
        tracker = agent_tracker(self.role, session_id, self.agent_label, energy=self.energy)
        received = expected - len(missing)
        with tracker.activity(
            "ReceiveTaskActivity", f"{self.role}: receive {source_subject}"
        ) as prov:
            partial = prov.produces(
                "TaskEntity", f"partial stream: {received} of {expected} envelopes"
            )
        tracker.decide(
            VERDICT_INCOMPLETE,
            f"The input stream on {source_subject} declared {expected} envelopes and "
            f"{len(missing)} never arrived (sequence {', '.join(map(str, missing))}). "
            f"The document was not processed: any output would describe an input this "
            f"agent did not fully receive. Verdict {VERDICT_INCOMPLETE}.",
            label=f"{self.role}: incomplete input",
            consumed=partial,
            attributes={
                "yp:envelopes_expected": expected,
                "yp:envelopes_missing": ",".join(map(str, missing)),
            },
        )
        return (
            tracker.emit(),
            f"INCOMPLETE input [{session_id}]: missing envelope(s) {missing} of {expected}",
        )
