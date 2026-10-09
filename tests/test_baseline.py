"""The Strategy A baseline: generous where it can be, blind where it must be.

A baseline exists to be beaten fairly. These tests pin both halves of that:

* what the callback reconstruction **can** do — activities, entities, dataflow, derived
  `wasInformedBy`, and typed activities when given a label map — because a weak baseline
  would make the paper's comparison worthless;
* what it **cannot** do, and that the restriction is structural rather than an artifact of
  this implementation. The observation stream has no field for a justification, so no
  configuration of the handler can produce one.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from eval.baseline import (
    DEFAULT_TYPE_MAP,
    CallbackLogger,
    _visible,
    compare,
    entity_identity_collisions,
    fact_recovery,
    justification_facts,
    read_events,
    unreachable_rationale,
    write_events,
)
from yprov4agenticai import ProvTracker
from yprov4agenticai.observer import StepEvent, get_default_observer, observe

JUSTIFICATION = "Self-consistency 0.41 < 0.70 threshold; the figures are unsupported."


def _lifecycle() -> ProvTracker:
    """A three-step lifecycle ending in a justified decision, as the agents emit."""
    tracker = ProvTracker(sid="s0", agent_label="Test_Agent", verbose=False)
    with tracker.activity("ReceiveTaskActivity", "Test: receive the task") as prov:
        task = prov.produces("TaskEntity", "summarise the run")
    with tracker.activity("ToolCallActivity", "Test: write the summary") as prov:
        prov.consumes(task)
        summary = prov.produces("ResponseEntity", "a summary", derived_from=task)
    tracker.decide("REVISE", JUSTIFICATION, label="Test: revise summary", consumed=summary)
    return tracker


def _observed(type_map: object = DEFAULT_TYPE_MAP) -> tuple[CallbackLogger, ProvTracker]:
    logger = CallbackLogger(type_map=type_map)  # type: ignore[arg-type]
    with observe(logger.record):
        tracker = _lifecycle()
    return logger, tracker


# ── the observation hook ──────────────────────────────────────────────────────────
def test_the_observer_sees_every_step_including_the_decision() -> None:
    """A decision is a step. Hiding it would make the baseline weaker than a real handler."""
    logger, _ = _observed()
    names = [event.name for event in logger.events()]
    assert names == [
        "Test: receive the task",
        "Test: write the summary",
        "Test: revise summary",
    ]


def test_the_observer_is_restored_after_the_block() -> None:
    before = get_default_observer()
    with observe(CallbackLogger().record):
        assert get_default_observer() is not None
    assert get_default_observer() is before


def test_the_event_carries_values_not_identifiers() -> None:
    """A handler is handed payloads; entity identity is something it has to mint."""
    logger, _ = _observed()
    tool_call = logger.events()[1]
    assert tool_call.inputs == ("summarise the run",)
    assert tool_call.outputs == ("a summary",)
    assert not any("yp:" in value for value in tool_call.inputs + tool_call.outputs)


def test_the_event_type_has_no_field_for_a_rationale() -> None:
    """The restriction is enforced by the type, not by the handler's good behaviour."""
    fields = set(StepEvent.__dataclass_fields__)
    assert not fields & {"justification", "verdict", "rationale", "confidence", "type"}


def test_a_failing_step_is_observed_as_a_failure() -> None:
    logger = CallbackLogger()
    tracker = ProvTracker(sid="s0", verbose=False, observer=logger.record)
    with pytest.raises(TimeoutError), tracker.activity("ToolCallActivity"):
        raise TimeoutError("the tool did not answer")
    assert [event.error for event in logger.events()] == ["TimeoutError"]


# ── what the reconstruction recovers ──────────────────────────────────────────────
def test_it_reconstructs_activities_entities_and_dataflow() -> None:
    logger, _ = _observed()
    doc = logger.to_prov_json("s0", "Test_Agent")
    assert len(doc["activity"]) == 3
    assert len(doc["entity"]) == 2
    assert len(doc["used"]) == 2
    assert len(doc["wasGeneratedBy"]) == 2
    assert len(doc["wasAssociatedWith"]) == 3


def test_it_derives_was_informed_by_from_observed_dataflow() -> None:
    """The one relation that is genuinely automatic, by PROV's own definition."""
    logger, _ = _observed()
    edges = list(logger.to_prov_json("s0", "Test_Agent")["wasInformedBy"].values())
    assert len(edges) == 2
    assert edges[0] == {
        "prov:informed": "cb:activity_01",
        "prov:informant": "cb:activity_00",
    }


def test_a_type_map_types_activities_and_no_map_types_none() -> None:
    mapped, _ = _observed(DEFAULT_TYPE_MAP)
    unmapped = CallbackLogger.from_events(mapped.events())

    def types(logger: CallbackLogger) -> list[str]:
        doc = logger.to_prov_json("s0", "Test_Agent")
        return [a["prov:type"]["$"] for a in doc["activity"].values() if "prov:type" in a]

    assert types(mapped) == [
        "yp:ReceiveTaskActivity",
        "yp:ToolCallActivity",
        "yp:DecisionActivity",
    ]
    assert types(unmapped) == []


def test_a_verdict_is_recoverable_only_from_the_label_text() -> None:
    """Honest about a real capability, and about why it is fragile.

    These agents interpolate the verdict into the step label, so a handler can scrape it.
    A graph whose decision node is named ``decide`` gives up nothing.
    """
    logger, _ = _observed()
    decision = logger.to_prov_json("s0", "Test_Agent")["activity"]["cb:activity_02"]
    assert decision["yp:verdict"] == "REVISE"
    assert decision["cb:verdict_source"] == "scraped-from-label"

    # The same run, with the verdict taken out of the label — which is what a graph whose
    # node is simply called "decide" looks like to a handler.
    anonymous = CallbackLogger.from_events(
        [
            replace(event, name="decide") if "revise" in event.name else event
            for event in logger.events()
        ],
        type_map=DEFAULT_TYPE_MAP,
    )
    activities = anonymous.to_prov_json("s0", "Test_Agent")["activity"].values()
    assert not any("yp:verdict" in activity for activity in activities)


# ── what it cannot recover, and why that is structural ────────────────────────────
def test_no_configuration_recovers_the_justification() -> None:
    logger, tracker = _observed()
    explicit = [tracker.to_prov_json()]
    found, verdicts, wanted = unreachable_rationale(explicit, logger.observed_payloads())
    assert wanted == [JUSTIFICATION]
    assert found == 0, "the rationale must not be reachable from the observed stream"
    assert verdicts == 1, "the verdict, by contrast, is in the label and is recoverable"


def test_the_rationale_search_would_catch_a_leak() -> None:
    """Guards the guard: a search that can never find anything proves nothing."""
    found, _, _ = unreachable_rationale(
        [{"activity": {"a": {"yp:justification": JUSTIFICATION}}}],
        [f"some payload containing {JUSTIFICATION} verbatim"],
    )
    assert found == 1


def test_the_facts_of_a_justification_are_its_figures_names_and_roles() -> None:
    """What the fact-level measure extracts, pinned on the shapes the agents write."""
    assert justification_facts(
        "acknowledged by [ca2, ca3, cax]; 9 activities / 39 entities, 21634 bytes; "
        "sha-256 matched; gap before 'Training' is 5.049s — 64.7% of the run's wall "
        "clock; wrote example_0_metrics.png"
    ) == ["ca2", "ca3", "cax", "39", "21634", "Training", "5.049", "64.7",
          "example_0_metrics.png"]  # fmt: skip


def test_a_fact_is_visible_only_as_a_whole_token() -> None:
    """A figure inside a longer number, or a name inside a longer name, was not seen."""
    haystack = "gap 5.049s; count 2163; agent ca22; file example_0_metrics.png.bak"
    assert _visible("5.049", haystack)
    assert not _visible("216", haystack)
    assert not _visible("ca2", haystack)
    assert _visible("example_0_metrics.png", haystack)


def test_fact_recovery_separates_what_was_seen_from_what_was_computed(
    tmp_path: Path,
) -> None:
    """The figure a decision computed is invisible; the counts it was handed are not."""
    record = {
        "activity": {
            "d": {
                "yp:verdict": "ACCEPT",
                "yp:justification": "Graph of 39 entities; record is 21634 bytes.",
            }
        }
    }
    recovery = fact_recovery(
        [(tmp_path / "s0__ca1_prov.json", record)], ["a graph with 39 entities"]
    )
    tally = recovery["ca1"]
    assert (tally.decisions, tally.verbatim, tally.facts, tally.facts_visible) == (1, 0, 2, 1)
    assert tally.all_facts_visible == 0


def test_fact_recovery_reports_the_verbatim_case_too(tmp_path: Path) -> None:
    """Guards the guard, as for the verbatim search: a leak must show up in both columns."""
    record = {"activity": {"d": {"yp:justification": JUSTIFICATION}}}
    tally = fact_recovery([(tmp_path / "s0__mc1_prov.json", record)], [JUSTIFICATION])["mc1"]
    assert tally.verbatim == 1
    assert tally.facts_visible == tally.facts == 2  # 0.41 and 0.70
    assert tally.all_facts_visible == 1


def test_reconstruction_carries_no_pointer_or_typed_entity() -> None:
    logger, _ = _observed()
    entities = logger.to_prov_json("s0", "Test_Agent")["entity"].values()
    assert all("yp:pid" not in entity for entity in entities)
    assert all("prov:type" not in entity for entity in entities)


def test_equal_payloads_collapse_into_one_entity() -> None:
    """Content-addressed identity is all a handler has, and it conflates."""
    logger = CallbackLogger()
    tracker = ProvTracker(sid="s0", verbose=False, observer=logger.record)
    for index in (0, 1):
        with tracker.activity("ToolCallActivity", f"step {index}") as prov:
            prov.produces("ToolResultEntity", "the same payload", suffix=f"_{index}")

    assert len(tracker.to_prov_json()["entity"]) == 3  # two distinct + the bundle
    assert len(logger.to_prov_json("s0", "NL_Summarizer")["entity"]) == 1
    assert (
        entity_identity_collisions(
            [tracker.to_prov_json()], [logger.to_prov_json("s0", "NL_Summarizer")]
        )
        == 1
    )


# ── the observation stream as an artifact ─────────────────────────────────────────
def test_an_event_stream_round_trips_through_disk(tmp_path: Path) -> None:
    """The stream is the artifact of condition A; a reconstruction is a function of it."""
    logger, _ = _observed()
    path = tmp_path / "_events.json"
    assert write_events(path, logger.events()) == 3
    assert read_events(path) == logger.events()


def test_compare_reads_both_conditions_from_disk(tmp_path: Path) -> None:
    logger, tracker = _observed()
    prov_dir, baseline_dir = tmp_path / "out", tmp_path / "out" / "baseline"
    prov_dir.mkdir()
    (prov_dir / "s0__test_prov.json").write_text(
        json.dumps(tracker.to_prov_json()), encoding="utf-8"
    )
    logger.write(baseline_dir)

    result = compare(prov_dir, baseline_dir)
    assert result["B"]["justifications"] == 1
    assert result["A"]["justifications"] == 0
    assert result["A0"]["typed_activities"] == 0
    assert result["A"]["typed_activities"] == 3
    assert result["rationale_found"] == 0
    assert result["facts"]["test"]["decisions"] == 1


def test_the_reconstruction_is_itself_valid_prov_json() -> None:
    """Conceded, and tested: syntax was never what separated the two strategies.

    The pre-Phase-3 E3 table claimed Strategy A emitted no W3C PROV-JSON. It was the
    fourth of that table's four wrong zeros. A baseline that could be dismissed on syntax
    would make the comparison about serialisation rather than about what a record can say.
    """
    from eval.baseline import valid_prov_json

    logger, _ = _observed()
    documents = list(logger.documents().values())
    accepted = valid_prov_json(documents)
    if accepted is None:
        pytest.skip("the reference prov parser is not installed")
    assert accepted == len(documents)
