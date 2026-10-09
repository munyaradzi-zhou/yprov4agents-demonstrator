"""Fault injection: can CA1's verdict ever be anything but ACCEPT?

A verdict that only ever takes one value carries no information, and a healthy run never
exercises the other branch. So these tests inject the two faults CA1 claims to detect and
assert that the record says so — which is also what makes the ``ESCALATE`` column in the
evaluation's verdict tally mean something.

Two conditions are independent and must stay distinguishable:

*Integrity.* The payload read back from the store does not hash to what was promised.

*Consumption.* The record was archived without its consumers confirming they had used it.
That is the safety net firing, and it keeps the record — but it forfeits the ordering the
architecture is built around, which is the whole claim behind persist-after-consume.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Any

import pytest

from runtime.agents import ca1_storage
from runtime.agents.ca1_storage import (
    PERSIST_TRIGGER_QUORUM,
    PERSIST_TRIGGER_TIMEOUT,
    CA1Storage,
)
from yprov4agenticai import ProvTracker

NO_BUS = None


def subject_record() -> dict[str, Any]:
    """A small producer document for CA1 to archive."""
    tracker = ProvTracker(sid="held", agent_label="yprov4ml-replay")
    entity = tracker.register_entity("TaskEntity", "a training run's provenance")
    tracker.record_activity(
        "ReceiveTaskActivity",
        "train",
        "2026-10-08T09:00:00+00:00",
        "2026-10-08T09:00:01+00:00",
        out_eid=entity,
        verbose=False,
    )
    return tracker.to_prov_json()


@pytest.fixture
def ca1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CA1Storage:
    """CA1 with its store and output directory under a temporary path."""
    import runtime.util

    monkeypatch.setattr(runtime.util, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ca1_storage, "OUT_DIR", tmp_path)
    return CA1Storage(NO_BUS)  # type: ignore[arg-type]


def decision_of(document: dict[str, Any]) -> dict[str, Any]:
    for attributes in document["activity"].values():
        prov_type = attributes.get("prov:type", {})
        if isinstance(prov_type, dict) and prov_type.get("$") == "yp:DecisionActivity":
            return attributes
    raise AssertionError("CA1 emitted no yp:DecisionActivity")


# ──────────────────────────────────────────────────────────────────────────────────
# The healthy path
# ──────────────────────────────────────────────────────────────────────────────────
def test_quorum_persist_is_accepted(ca1: CA1Storage) -> None:
    document, log_line = ca1._do_persist(
        "held",
        subject_record(),
        ["cax", "ca2", "ca3"],
        PERSIST_TRIGGER_QUORUM,
        "all expected consumers acknowledged",
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "ACCEPT", log_line
    assert decision["yp:persist_trigger"] == PERSIST_TRIGGER_QUORUM
    assert decision["yp:consumers_acknowledged"] == 3
    assert "yp:escalation_reason" not in decision
    assert "round-trip read verified" in decision["yp:justification"]


# ──────────────────────────────────────────────────────────────────────────────────
# Fault 1: the record was archived without confirmed consumption
# ──────────────────────────────────────────────────────────────────────────────────
def test_timeout_persist_escalates_rather_than_passing_silently(ca1: CA1Storage) -> None:
    """The safety net keeps the record, but must not report it as a clean accept.

    Reporting ACCEPT here made the advertised ordering and its fallback indistinguishable
    without reading prose — and at a short timeout the fallback is what actually fires.
    """
    document, log_line = ca1._do_persist(
        "held",
        subject_record(),
        [],
        PERSIST_TRIGGER_TIMEOUT,
        "timeout after 6.0s; no acknowledgement from ['ca2', 'ca3', 'cax']",
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "ESCALATE", log_line
    assert decision["yp:escalation_reason"] == "consumption-unconfirmed"
    assert decision["yp:consumers_acknowledged"] == 0
    justification = decision["yp:justification"]
    assert "WITHOUT confirmed consumption" in justification
    assert "persist-after-consume ordering was not satisfied" in justification


def test_partial_acknowledgement_also_escalates(ca1: CA1Storage) -> None:
    """Two of three consumers is not quorum, and the record must name who was missing."""
    document, _ = ca1._do_persist(
        "held",
        subject_record(),
        ["cax", "ca2"],
        PERSIST_TRIGGER_TIMEOUT,
        "timeout after 6.0s; no acknowledgement from ['ca3']",
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "ESCALATE"
    assert decision["yp:consumers_acknowledged"] == 2
    assert "ca3" in decision["yp:justification"]


# ──────────────────────────────────────────────────────────────────────────────────
# Fault 2: the stored payload is not what was promised
# ──────────────────────────────────────────────────────────────────────────────────
def test_corrupted_payload_escalates_on_integrity(
    ca1: CA1Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Corrupt the payload between the write and the read-back.

    This is the condition the ACCEPT verdict is supposed to rule out. Before the store
    read through to disk it could not arise at all, because the check compared the string
    just written with itself.
    """
    original_put = ca1.store.put

    def put_then_corrupt(payload: str, kind: str = "artifact") -> dict:
        pointer = original_put(payload, kind=kind)
        blob = Path(ca1.store.root or ".") / f"{pointer['yp:pid'].rsplit('-', 1)[-1]}.txt"
        blob.write_bytes(b"a different payload entirely")
        return pointer

    monkeypatch.setattr(ca1.store, "put", put_then_corrupt)

    document, log_line = ca1._do_persist(
        "held",
        subject_record(),
        ["cax", "ca2", "ca3"],
        PERSIST_TRIGGER_QUORUM,
        "all expected consumers acknowledged",
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "ESCALATE", log_line
    assert decision["yp:escalation_reason"] == "integrity"
    assert "content-hash mismatch" in decision["yp:justification"]


def test_integrity_outranks_consumption_in_the_escalation_reason(
    ca1: CA1Storage, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When both conditions fail, the record names the more serious one."""
    monkeypatch.setattr(
        ca1.store, "resolve", lambda *_a, **_k: (_ for _ in ()).throw(OSError("disk gone"))
    )

    document, _ = ca1._do_persist(
        "held", subject_record(), [], PERSIST_TRIGGER_TIMEOUT, "timeout"
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "ESCALATE"
    assert decision["yp:escalation_reason"] == "integrity"


# ──────────────────────────────────────────────────────────────────────────────────
# The race the guard used to lose
# ──────────────────────────────────────────────────────────────────────────────────
def test_quorum_cancels_the_timeout_guard(ca1: CA1Storage) -> None:
    """Reaching quorum must stand the safety net down, not run alongside it.

    The guard is a fallback for a consumer that never reports. Left running after quorum
    it competed with the consumers, so at a short timeout a record could be archived with
    zero acknowledgements while another in the same run reached full quorum.
    """

    async def scenario() -> None:
        monkeypatched_persist: list[str] = []

        async def record_persist(sid: str, *, trigger: str, reason: str) -> None:
            monkeypatched_persist.append(trigger)
            ca1._done.add(sid)

        ca1._persist = record_persist  # type: ignore[method-assign]
        ca1._pending["held"] = subject_record()
        # CA1 has its whole copy: the stream's end arrived. Without this, quorum correctly
        # waits for the end of CA1's own copy instead of persisting a partial one.
        ca1._ended["held"] = 3
        ca1._guards["held"] = asyncio.create_task(ca1._timeout_guard("held"))

        for role in ("cax", "ca2", "ca3"):
            await ca1._on_consumer_ack(
                f"agent.lifecycle.{role}.held",
                _stream_end_envelope(),
            )

        assert monkeypatched_persist == [PERSIST_TRIGGER_QUORUM]
        assert "held" not in ca1._guards, "the guard should have been cancelled"

    asyncio.run(scenario())


def _stream_end_envelope() -> Any:
    from runtime.envelope import ProvEventType, ProvRecordEnvelope

    return ProvRecordEnvelope(
        session_id="held",
        sequence_no=99,
        producer_id="consumer",
        event_type=ProvEventType.STREAM_END.value,
    )


class RecordingBus:
    """Captures what an agent publishes, so a full persist can run without a broker."""

    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, subject: str, envelope: Any) -> None:
        self.published.append((subject, envelope))

    async def subscribe(self, subject: str, handler: Any) -> None:  # pragma: no cover
        raise AssertionError("this test does not expect a subscription")


def test_session_bookkeeping_is_released_after_persisting(ca1: CA1Storage) -> None:
    """Per-session state must not accumulate for the lifetime of the process."""
    bus = RecordingBus()
    ca1.bus = bus  # type: ignore[assignment]

    async def scenario() -> None:
        ca1._pending["held"] = subject_record()
        ca1._acks["held"] = {"cax", "ca2", "ca3"}
        await ca1._persist("held", trigger=PERSIST_TRIGGER_QUORUM, reason="quorum")

    asyncio.run(scenario())

    assert "held" not in ca1._pending, "the held document was not released"
    assert "held" not in ca1._acks, "acknowledgement bookkeeping was not released"
    assert "held" in ca1._done, "the session must be remembered as persisted"

    subjects = {subject for subject, _ in bus.published}
    assert subjects == {"agent.lifecycle.ca1.held"}, "CA1 must emit its own receipt stream"


def test_the_archived_payload_is_the_document_that_was_held(ca1: CA1Storage) -> None:
    """What CA1 stored must be recoverable and equal to what it was given."""
    held = subject_record()
    document, _ = ca1._do_persist(
        "held", held, ["cax", "ca2", "ca3"], PERSIST_TRIGGER_QUORUM, "quorum"
    )

    pid = next(entity["yp:pid"] for entity in document["entity"].values() if "yp:pid" in entity)
    assert json.loads(ca1.store.resolve(pid, verify=True)) == held
