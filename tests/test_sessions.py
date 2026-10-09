"""A record is never dropped, merged with another, or overwritten because of its session id.

Three ways it could happen before (docs/AUDIT.md, finding [8]):

* CA1 remembered every session it had persisted and ignored any that arrived again,
  without a word — so a replay, or a producer restarted by its supervisor, was never
  stored and nothing said so.
* A session was named after its input's folder. yProv4ML writes one file per rank into
  one folder in a multi-GPU run (``…_GR0``, ``…_GR1``), and those files shared a session.
* A record file was written to a path derived from the session, replacing whatever was
  there — and a local run did not clear its previous run's records, so a run that emitted
  fewer of them left stale ones to be counted as its own.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from runtime.agents.ca1_storage import PERSIST_AFTER, CA1Storage
from runtime.base_agent import stream_document
from runtime.envelope import ProvEventType, ProvRecordEnvelope
from runtime.producer import sessions_for
from yprov4agenticai import ProvTracker

# ── naming ─────────────────────────────────────────────────────────────────────────


def test_a_folder_with_one_document_is_named_after_the_folder(tmp_path: Path) -> None:
    doc = tmp_path / "example_0" / "prov_example_GR0_0.json"
    assert sessions_for([doc], tmp_path) == [(doc, "example_0")]


def test_documents_sharing_a_folder_get_distinct_sessions(tmp_path: Path) -> None:
    """The multi-GPU case: one folder, one file per rank."""
    ranks = [tmp_path / "run" / f"prov_GR{n}.json" for n in range(2)]
    sessions = [session for _, session in sessions_for(ranks, tmp_path)]
    assert sessions == ["run__prov_GR0", "run__prov_GR1"]


def test_a_document_at_the_root_is_named_by_its_stem(tmp_path: Path) -> None:
    doc = tmp_path / "loose.json"
    assert sessions_for([doc], tmp_path) == [(doc, "loose")]


def test_a_session_cannot_split_or_widen_a_bus_subject(tmp_path: Path) -> None:
    """A session is a token of ``producer.yprov4ml.<session>``: a dot would split it into
    two tokens, and ``*`` or ``>`` would turn it into a wildcard."""
    doc = tmp_path / "run.v2 *>" / "a.json"
    ((_, session),) = sessions_for([doc], tmp_path)
    assert all(c not in session for c in ".*> ")


def test_names_that_would_still_collide_are_refused(tmp_path: Path) -> None:
    clash = [tmp_path / "run.1" / "a.json", tmp_path / "run_1" / "b.json"]
    with pytest.raises(ValueError, match="would share session"):
        sessions_for(clash, tmp_path)


# ── storage ────────────────────────────────────────────────────────────────────────


class RecordingBus:
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


def _envelopes(doc: dict[str, Any]) -> list[ProvRecordEnvelope]:
    bus = RecordingBus()
    asyncio.run(
        stream_document(
            bus,  # type: ignore[arg-type]
            "producer.yprov4ml.s",
            session_id="s",
            producer_id="producer",
            doc=doc,
        )
    )
    return [envelope for _, envelope in bus.published]


def _ack(role: str) -> ProvRecordEnvelope:
    return ProvRecordEnvelope(
        session_id=f"s::{role}",
        sequence_no=5,
        producer_id=role,
        event_type=ProvEventType.STREAM_END.value,
    )


def test_a_session_that_arrives_again_is_persisted_again_and_says_so(
    headless_agent: Callable[..., Any],
) -> None:
    ca1: CA1Storage = headless_agent(CA1Storage)
    bus = RecordingBus()
    ca1.bus = bus  # type: ignore[assignment]
    envelopes = _envelopes(_record())

    async def deliver_and_acknowledge() -> None:
        for envelope in envelopes:
            await ca1._on_producer("producer.yprov4ml.s", envelope)
        for role in PERSIST_AFTER:
            await ca1._on_consumer_ack(f"agent.lifecycle.{role}.s", _ack(role))

    async def scenario() -> None:
        await deliver_and_acknowledge()
        await deliver_and_acknowledge()

    asyncio.run(scenario())

    decisions = []
    for _, envelope in bus.published:
        for activity in (envelope.payload.get("activity") or {}).values():
            if "yp:verdict" in activity:
                decisions.append(activity)
    assert len(decisions) == 2, "the second arrival was dropped"
    assert "yp:persist_count" not in decisions[0]
    assert decisions[1]["yp:persist_count"] == 2
    assert "persisted 2 times" in decisions[1]["yp:justification"]


# ── files ──────────────────────────────────────────────────────────────────────────


def test_a_record_never_replaces_another(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    import runtime.util

    monkeypatch.setattr(runtime.util, "OUT_DIR", tmp_path)
    first = runtime.util.write_prov("ca1", "s", {"n": 1})
    second = runtime.util.write_prov("ca1", "s", {"n": 2})
    assert first is not None and second is not None and first != second
    assert json.loads(first.read_text(encoding="utf-8")) == {"n": 1}
    assert json.loads(second.read_text(encoding="utf-8")) == {"n": 2}


def test_a_local_run_clears_only_what_it_writes(tmp_path: Path) -> None:
    from run_local import clear_previous_run

    (tmp_path / "example_0__ca1_prov.json").write_text("{}", encoding="utf-8")
    for owned in ("store", "charts", "baseline"):
        (tmp_path / owned).mkdir()
    (tmp_path / "RESULTS.md").write_text("", encoding="utf-8")
    for kept in ("bench", "faults"):
        (tmp_path / kept).mkdir()
        (tmp_path / kept / "result.json").write_text("{}", encoding="utf-8")

    assert clear_previous_run(tmp_path) == 5
    assert sorted(p.name for p in tmp_path.iterdir()) == ["bench", "faults"]
    assert (tmp_path / "bench" / "result.json").is_file()
