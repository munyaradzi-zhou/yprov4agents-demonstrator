"""The capture API: what it records automatically, what it refuses, what it must not add.

These are unit tests over the library, so they hold regardless of what any run produced.
Three properties are load-bearing and each has a test that fails loudly if it stops
holding:

1. The wrappers are *thin* — a document built through ``step``/``activity``/``decide`` is
   structurally identical to one built from the primitives. If that ever stops being true,
   the ergonomic layer has started inventing records, and every number measured through
   one layer stops describing the other.
2. The wrappers record no lineage nobody declared. This is the defect Phase 1 removed from
   the tracker, and a convenience layer is exactly where it would come back.
3. A decision cannot be recorded without its rationale.
"""

from __future__ import annotations

import datetime as dt

import pytest

from eval.compare import reduce_document
from yprov4agenticai import ActivityScope, ProvTracker
from yprov4agenticai.api import OUTCOME_ERROR, Span


def _tracker(sid: str = "t0") -> ProvTracker:
    return ProvTracker(sid=sid, agent_label="Test_Agent", verbose=False)


def _types(doc: dict) -> list[str]:
    return [a["prov:type"]["$"] for a in doc["activity"].values()]


# ── step(): the decorator ─────────────────────────────────────────────────────────
def test_step_records_one_typed_activity_per_call() -> None:
    tracker = _tracker()

    @tracker.step("PlanningActivity", "choose an approach")
    def plan(task: str) -> str:
        return f"plan for {task}"

    assert plan("summarise") == "plan for summarise"
    doc = tracker.to_prov_json()
    assert _types(doc) == ["yp:PlanningActivity"]
    (activity,) = doc["activity"].values()
    assert activity["prov:label"] == "choose an approach"
    assert activity["prov:startTime"] <= activity["prov:endTime"]


def test_step_defaults_its_label_to_the_function_name() -> None:
    tracker = _tracker()

    @tracker.step("PlanningActivity")
    def plan() -> None:
        return None

    plan()
    (activity,) = tracker.to_prov_json()["activity"].values()
    assert "plan" in activity["prov:label"]


def test_step_declares_no_lineage_unless_asked() -> None:
    """A wrapper sees that a function ran, never what it considered its input."""
    tracker = _tracker()

    @tracker.step("PlanningActivity")
    def plan() -> str:
        return "a plan"

    plan()
    doc = tracker.to_prov_json()
    assert doc["used"] == {}
    assert doc["wasGeneratedBy"] == {}
    assert doc["wasDerivedFrom"] == {}


def test_step_registers_its_return_value_when_produces_is_given() -> None:
    tracker = _tracker()

    @tracker.step("ToolCallActivity", produces="ToolResultEntity")
    def run() -> str:
        return "tool output"

    run()
    doc = tracker.to_prov_json()
    entities = {
        eid: node
        for eid, node in doc["entity"].items()
        if node["prov:type"]["$"] == "yp:ToolResultEntity"
    }
    assert len(entities) == 1
    (eid,) = entities
    assert entities[eid]["prov:value"] == "tool output"
    assert [r["prov:entity"] for r in doc["wasGeneratedBy"].values()] == [eid]


def test_step_injects_the_scope_into_a_function_that_asks_for_it() -> None:
    tracker = _tracker()
    task = tracker.register_entity("TaskEntity", "summarise the run")

    @tracker.step("ToolCallActivity")
    def run(payload: str, *, prov: ActivityScope) -> str:
        prov.consumes(task)
        prov.note(**{"yp:tool": "matplotlib"})
        return prov.produces("ToolResultEntity", payload, derived_from=task)

    run("chart.png")
    doc = tracker.to_prov_json()
    (activity,) = doc["activity"].values()
    assert activity["yp:tool"] == "matplotlib"
    assert [r["prov:entity"] for r in doc["used"].values()] == [task]
    assert [r["prov:usedEntity"] for r in doc["wasDerivedFrom"].values()] == [task]


def test_step_records_a_call_that_raised_and_reraises_it() -> None:
    tracker = _tracker()

    @tracker.step("ToolCallActivity")
    def run() -> None:
        raise TimeoutError("the tool did not answer")

    with pytest.raises(TimeoutError):
        run()

    (activity,) = tracker.to_prov_json()["activity"].values()
    assert activity["yp:outcome"] == OUTCOME_ERROR
    assert activity["yp:error_type"] == "TimeoutError"


# ── activity(): the context manager ───────────────────────────────────────────────
def test_activity_records_what_the_block_declared() -> None:
    tracker = _tracker()
    task = tracker.register_entity("TaskEntity", "summarise the run")

    with tracker.activity("ToolCallActivity", "render the chart") as prov:
        prov.consumes(task)
        chart = prov.produces("ToolResultEntity", "chart.png", derived_from=task)

    doc = tracker.to_prov_json()
    assert _types(doc) == ["yp:ToolCallActivity"]
    assert [r["prov:entity"] for r in doc["used"].values()] == [task]
    assert [r["prov:entity"] for r in doc["wasGeneratedBy"].values()] == [chart]


def test_activity_records_a_failing_block_exactly_once() -> None:
    """A step that fails must appear as a failure, not as a hole in the record."""
    tracker = _tracker()

    with pytest.raises(ValueError), tracker.activity("ToolCallActivity") as prov:
        prov.consumes(tracker.register_entity("TaskEntity", "input"))
        raise ValueError("the payload did not parse")

    doc = tracker.to_prov_json()
    assert len(doc["activity"]) == 1
    (activity,) = doc["activity"].values()
    assert activity["yp:outcome"] == OUTCOME_ERROR
    assert activity["yp:error_type"] == "ValueError"
    # What the block declared before failing survives: it is still true that it read this.
    assert len(doc["used"]) == 1


def test_activity_derives_was_informed_by_from_declared_dataflow() -> None:
    tracker = _tracker()
    with tracker.activity("ReceiveTaskActivity") as prov:
        task = prov.produces("TaskEntity", "summarise the run")
    with tracker.activity("PlanningActivity") as prov:
        prov.consumes(task)

    edges = list(tracker.to_prov_json()["wasInformedBy"].values())
    assert len(edges) == 1
    assert edges[0]["prov:informant"].endswith("ReceiveTaskActivity_t0_00")
    assert edges[0]["prov:informed"].endswith("PlanningActivity_t0_01")


def test_scope_concludes_carries_the_verdict_onto_a_working_step() -> None:
    tracker = _tracker()
    with tracker.activity("ObservationActivity") as prov:
        prov.concludes("ACCEPT", "The written file is 4096 bytes, so it is not empty.")

    (activity,) = tracker.to_prov_json()["activity"].values()
    assert activity["yp:verdict"] == "ACCEPT"
    assert activity["yp:justification"].startswith("The written file")


def test_scope_refuses_a_verdict_without_a_justification() -> None:
    tracker = _tracker()
    with (
        pytest.raises(ValueError, match="justification"),
        tracker.activity("ObservationActivity") as prov,
    ):
        prov.concludes("ACCEPT", "   ")


# ── decide(): the one call that cannot be generated ───────────────────────────────
def test_decide_records_a_justified_verdict() -> None:
    tracker = _tracker()
    tracker.decide(
        "REVISE",
        "Self-consistency 0.41 < 0.70 threshold; regenerating.",
        confidence=0.41,
    )
    (activity,) = tracker.to_prov_json()["activity"].values()
    assert activity["prov:type"]["$"] == "yp:DecisionActivity"
    assert activity["yp:verdict"] == "REVISE"
    assert activity["yp:confidence_score"] == 0.41
    assert "0.70 threshold" in activity["yp:justification"]


@pytest.mark.parametrize("justification", ["", "   ", "\n\t"])
def test_decide_refuses_a_blank_justification(justification: str) -> None:
    """The field no machinery can fill in is the field the API will not let a caller skip."""
    tracker = _tracker()
    with pytest.raises(ValueError, match="justification"):
        tracker.decide("ACCEPT", justification)
    assert tracker.to_prov_json()["activity"] == {}


def test_decide_is_counted_by_the_evaluation_harness() -> None:
    """The metric the paper reports must see what the API writes."""
    from eval.evaluate import decisions

    tracker = _tracker()
    tracker.decide("ACCEPT", "Every required consumer acknowledged the record.")
    assert decisions(tracker.to_prov_json()) == (1, 1)


# ── emit(): serialise and hand off ────────────────────────────────────────────────
def test_emit_returns_the_document_and_calls_the_sink_once() -> None:
    received: list[dict] = []
    tracker = ProvTracker(
        sid="t0", agent_label="Test_Agent", verbose=False, sink=received.append
    )
    with tracker.activity("ReceiveTaskActivity") as prov:
        prov.produces("TaskEntity", "summarise the run")

    doc = tracker.emit()
    assert len(received) == 1
    assert received[0] == doc
    assert doc["prefix"]["prov"] == "http://www.w3.org/ns/prov#"


def test_emit_prefers_the_sink_it_is_given() -> None:
    configured: list[dict] = []
    override: list[dict] = []
    tracker = ProvTracker(sid="t0", verbose=False, sink=configured.append)
    tracker.emit(sink=override.append)
    assert (len(configured), len(override)) == (0, 1)


def test_emit_adds_nothing_to_the_document() -> None:
    tracker = _tracker()
    with tracker.activity("ReceiveTaskActivity") as prov:
        prov.produces("TaskEntity", "summarise the run")
    assert tracker.emit() == tracker.to_prov_json()


# ── the thinness claim ────────────────────────────────────────────────────────────
def test_the_api_and_the_primitives_produce_the_same_document() -> None:
    """Build one lifecycle both ways and compare the parts that carry meaning.

    This is the test that keeps the layer honest. The paper reports numbers measured over
    agents written against the primitives and describes an API written against the
    wrappers; if the two diverge structurally, one of those two statements is false.

    The comparison deliberately reuses the evaluation harness's own reduction
    (``eval.compare.reduce_document``), which ignores timestamps and session-local
    ordinals and keeps everything else — types, relations, verdicts, justification text,
    attribute names.
    """
    justification = "Acknowledged by all three required consumers before persisting."

    raw = ProvTracker(sid="t0", agent_label="Test_Agent", verbose=False)
    task = raw.register_entity("TaskEntity", "summarise the run")
    raw.record_activity("ReceiveTaskActivity", "receive", "T0", "T1", out_eid=task)
    result = raw.register_entity("ToolResultEntity", "chart.png", derived_from=task)
    raw.record_activity("ToolCallActivity", "render", "T1", "T2", in_eid=task, out_eid=result)
    raw.record_activity(
        "DecisionActivity",
        "decide: ACCEPT",
        "T2",
        "T3",
        in_eid=result,
        justification=justification,
        confidence_score=0.93,
        extra_attrs={"yp:verdict": "ACCEPT"},
    )

    api = ProvTracker(sid="t0", agent_label="Test_Agent", verbose=False)
    with api.activity("ReceiveTaskActivity", "receive") as prov:
        api_task = prov.produces("TaskEntity", "summarise the run")
    with api.activity("ToolCallActivity", "render") as prov:
        prov.consumes(api_task)
        api_result = prov.produces("ToolResultEntity", "chart.png", derived_from=api_task)
    api.decide("ACCEPT", justification, confidence=0.93, consumed=api_result)

    # One name for both: the reduction carries the document's filename, which is not
    # part of what is being compared here.
    assert reduce_document("lifecycle", api.to_prov_json()) == reduce_document(
        "lifecycle", raw.to_prov_json()
    )


# ── timing fidelity ───────────────────────────────────────────────────────────────
def test_a_short_activity_records_a_measurable_duration() -> None:
    """A fast step must be distinguishable from an unmeasured one.

    The system clock advances in steps of roughly 15.6 ms on Windows, so two
    ``datetime.now()`` readings either side of a sub-millisecond block return the same
    value and the activity is recorded as instantaneous. Every activity in the Phase 1
    archive had that shape — 96 of 96 — which made the recorded timeline unusable for
    saying where a run spent its time. The duration is therefore measured monotonically
    and the start instant anchored to the wall clock (see :class:`Span`).
    """
    tracker = _tracker()
    with tracker.activity("ToolCallActivity"):
        sum(range(10_000))  # well under one clock tick, well over one perf_counter tick

    (activity,) = tracker.to_prov_json()["activity"].values()
    started = dt.datetime.fromisoformat(activity["prov:startTime"])
    ended = dt.datetime.fromisoformat(activity["prov:endTime"])
    assert ended > started, "a step that did work must not be recorded as instantaneous"
    assert (ended - started).total_seconds() < 1.0, "and not as having taken a second"


def test_a_decision_is_timed_by_the_same_clock() -> None:
    tracker = _tracker()
    tracker.decide("ACCEPT", "Every required consumer acknowledged the record.")
    (activity,) = tracker.to_prov_json()["activity"].values()
    assert activity["prov:endTime"] > activity["prov:startTime"]


def test_a_decision_can_be_dated_from_when_deliberation_began() -> None:
    tracker = _tracker()
    span = Span()
    sum(range(10_000))
    tracker.decide("REVISE", "Threshold not met.", started_at=span.start_iso)
    (activity,) = tracker.to_prov_json()["activity"].values()
    assert activity["prov:startTime"] == span.start_iso
