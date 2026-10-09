"""A lost envelope must be reported, never turned into a plausible record.

The bus is at-most-once. Every envelope carried a sequence number and nothing read it, so
an agent processed whatever it had buffered when STREAM_END arrived: a lost relations chunk
produced a well-formed record with activities, entities and no lineage, which governance
passed and storage persisted (docs/AUDIT.md, finding [3]). Losing a record is disclosed in
the paper; corrupting one silently would not have been.

The streams here are produced by the real `stream_document` and delivered to real agents,
with one envelope withheld, so the test exercises the same numbering the bus carries.
"""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from typing import Any

from runtime.agents import ca1_storage
from runtime.agents.ca1_storage import (
    ESCALATION_INCOMPLETE,
    PERSIST_AFTER,
    CA1Storage,
)
from runtime.agents.mc1_compliance import MC1Compliance
from runtime.base_agent import (
    INPUT_COMPLETE,
    VERDICT_INCOMPLETE,
    missing_envelopes,
    stream_document,
)
from runtime.envelope import ProvEventType, ProvRecordEnvelope
from yprov4agenticai import ProvTracker


class RecordingBus:
    """Captures what is published, so a stream can be replayed with a gap in it."""

    def __init__(self) -> None:
        self.published: list[tuple[str, ProvRecordEnvelope]] = []

    async def publish(self, subject: str, envelope: ProvRecordEnvelope) -> None:
        self.published.append((subject, envelope))


def _record() -> dict[str, Any]:
    tracker = ProvTracker(sid="s", agent_label="yprov4ml-replay")
    task = tracker.register_entity("TaskEntity", "a run")
    tracker.record_activity(
        "ReceiveTaskActivity",
        "train",
        "2026-10-08T09:00:00+00:00",
        "2026-10-08T09:00:01+00:00",
        out_eid=task,
        verbose=False,
    )
    return tracker.to_prov_json()


def _stream(doc: dict[str, Any], subject: str, session: str) -> list[ProvRecordEnvelope]:
    bus = RecordingBus()
    asyncio.run(
        stream_document(
            bus,  # type: ignore[arg-type]
            subject,
            session_id=session,
            producer_id="producer",
            doc=doc,
        )
    )
    return [envelope for _, envelope in bus.published]


def _decision(doc: dict[str, Any]) -> dict[str, Any]:
    for activity in doc["activity"].values():
        prov_type = activity.get("prov:type", {})
        if isinstance(prov_type, dict) and prov_type.get("$") == "yp:DecisionActivity":
            return activity
    raise AssertionError("no yp:DecisionActivity")


# ── the arithmetic ──────────────────────────────────────────────────────────────────


def test_a_complete_stream_has_nothing_missing() -> None:
    assert missing_envelopes({1, 2, 3, 4}, 4) == []


def test_a_gap_is_named_by_its_sequence_numbers() -> None:
    assert missing_envelopes({1, 2, 5}, 5) == [3, 4]


def test_a_lost_start_is_a_gap_too() -> None:
    assert missing_envelopes({2, 3}, 3) == [1]


def test_an_unnumbered_sender_cannot_be_checked_and_is_not_called_empty() -> None:
    assert missing_envelopes({0}, 0) == []


def test_stream_end_carries_the_length_of_the_stream() -> None:
    """The check relies on STREAM_END's own number being the stream's length."""
    envelopes = _stream(_record(), "producer.yprov4ml.s", "s")
    assert envelopes[-1].event_type == ProvEventType.STREAM_END.value
    assert [e.sequence_no for e in envelopes] == list(range(1, len(envelopes) + 1))


# ── an agent handed a stream with a gap ────────────────────────────────────────────────


def test_an_agent_records_incomplete_input_instead_of_processing_it(
    headless_agent: Callable[..., Any],
) -> None:
    agent: MC1Compliance = headless_agent(MC1Compliance)
    out = RecordingBus()
    agent.bus = out  # type: ignore[assignment]
    processed: list[str] = []

    def process(*args: Any) -> tuple[dict[str, Any], str]:
        processed.append("called")
        return {}, ""

    agent.process = process  # type: ignore[method-assign,assignment]

    envelopes = _stream(_record(), "agent.lifecycle.cax.s", "s")
    lost = envelopes[2]  # a RECORD_CHUNK in the middle of the stream

    async def deliver() -> None:
        for envelope in envelopes:
            if envelope is not lost:
                await agent._on_envelope("agent.lifecycle.cax.s", envelope)

    asyncio.run(deliver())

    assert processed == [], "a partial document must not be processed"
    emitted: dict[str, Any] = {}
    for _, envelope in out.published:
        if isinstance(envelope.payload, dict):
            for section, value in envelope.payload.items():
                if isinstance(value, dict):
                    emitted.setdefault(section, {}).update(value)
    decision = _decision(emitted)
    assert decision["yp:verdict"] == VERDICT_INCOMPLETE
    assert str(lost.sequence_no) in decision["yp:envelopes_missing"]
    assert str(lost.sequence_no) in decision["yp:justification"]

    end = out.published[-1][1]
    assert end.event_type == ProvEventType.STREAM_END.value
    assert end.payload[INPUT_COMPLETE] is False


def test_a_complete_stream_is_processed_and_not_marked(
    headless_agent: Callable[..., Any],
) -> None:
    agent: MC1Compliance = headless_agent(MC1Compliance)
    out = RecordingBus()
    agent.bus = out  # type: ignore[assignment]

    envelopes = _stream(_record(), "agent.lifecycle.cax.s", "s")

    async def deliver() -> None:
        for envelope in envelopes:
            await agent._on_envelope("agent.lifecycle.cax.s", envelope)

    asyncio.run(deliver())
    end = out.published[-1][1]
    assert INPUT_COMPLETE not in end.payload


# ── CA1: storage must neither count an unused record nor store a partial one silently ──


def _ca1(headless_agent: Callable[..., Any]) -> tuple[CA1Storage, RecordingBus]:
    agent: CA1Storage = headless_agent(CA1Storage)
    bus = RecordingBus()
    agent.bus = bus  # type: ignore[assignment]
    return agent, bus


def _ack(role: str, session: str, payload: dict[str, Any] | None = None) -> ProvRecordEnvelope:
    return ProvRecordEnvelope(
        session_id=f"{session}::{role}",
        sequence_no=5,
        producer_id=role,
        event_type=ProvEventType.STREAM_END.value,
        payload=payload or {},
    )


def test_a_consumer_that_received_a_gap_is_not_counted_as_having_used_the_record(
    headless_agent: Callable[..., Any],
) -> None:
    ca1, _ = _ca1(headless_agent)
    ca1._pending["s"] = _record()
    asyncio.run(
        ca1._on_consumer_ack("agent.lifecycle.cax.s", _ack("cax", "s", {INPUT_COMPLETE: False}))
    )
    assert "cax" not in ca1._acks.get("s", set())


def test_quorum_before_storage_has_its_whole_copy_waits_for_the_end(
    headless_agent: Callable[..., Any],
) -> None:
    """Consumers can finish before CA1's own copy has fully arrived. Persisting at that
    moment stored whatever had arrived so far."""
    ca1, _ = _ca1(headless_agent)
    persisted: list[str] = []

    async def record_persist(sid: str, *, trigger: str, reason: str) -> None:
        persisted.append(trigger)
        ca1._done.add(sid)

    ca1._persist = record_persist  # type: ignore[method-assign]
    envelopes = _stream(_record(), "producer.yprov4ml.s", "s")

    async def scenario() -> None:
        for envelope in envelopes[:-1]:
            await ca1._on_producer("producer.yprov4ml.s", envelope)
        for role in PERSIST_AFTER:
            await ca1._on_consumer_ack(f"agent.lifecycle.{role}.s", _ack(role, "s"))
        assert persisted == [], "persisted before its own copy was complete"
        await ca1._on_producer("producer.yprov4ml.s", envelopes[-1])
        assert persisted == ["quorum"]

    asyncio.run(scenario())


def test_storage_with_a_gap_in_its_copy_stores_it_and_escalates(
    headless_agent: Callable[..., Any],
) -> None:
    ca1, _ = _ca1(headless_agent)
    prov, _ = ca1._do_persist(
        "s", _record(), sorted(PERSIST_AFTER), "quorum", "all acknowledged", [3], True
    )
    decision = _decision(prov)
    assert decision["yp:verdict"] == "ESCALATE"
    assert decision["yp:escalation_reason"] == ESCALATION_INCOMPLETE
    assert decision["yp:envelopes_missing"] == "3"
    assert "partial" in decision["yp:justification"]


def test_a_lost_final_envelope_still_ends_in_a_reported_persist(
    headless_agent: Callable[..., Any], monkeypatch: Any
) -> None:
    """The guard used to be armed by STREAM_END. Losing that one envelope left nothing to
    fire, so the record was held for ever and never persisted or reported."""
    monkeypatch.setattr(ca1_storage, "PERSIST_TIMEOUT_S", 0.05)
    ca1, bus = _ca1(headless_agent)
    envelopes = _stream(_record(), "producer.yprov4ml.s", "s")

    async def scenario() -> None:
        for envelope in envelopes[:-1]:  # STREAM_END never arrives
            await ca1._on_producer("producer.yprov4ml.s", envelope)
        await asyncio.sleep(0.3)

    asyncio.run(scenario())

    assert "s" in ca1._done, "the record was never persisted"
    emitted: dict[str, Any] = {}
    for _, envelope in bus.published:
        for section, value in envelope.payload.items():
            if isinstance(value, dict):
                emitted.setdefault(section, {}).update(value)
    decision = _decision(emitted)
    assert decision["yp:verdict"] == "ESCALATE"
    assert decision["yp:escalation_reason"] == ESCALATION_INCOMPLETE
