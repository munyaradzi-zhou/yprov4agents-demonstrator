"""Do the operational agents' justifications state what was actually checked?

The project's claim is that a decision record carries reasoning a external observer could
not reconstruct. A justification that restates its own instruction — "grounded in the graph
counts", asserted without consulting the graph — does not meet that bar, and neither does
one whose text is identical regardless of what happened.

So each test here establishes two things: that the stated check is performed, and that its
outcome changes the record.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.agents import ca2_metrics, cax_summariser
from runtime.agents.ca2_metrics import SUBJECT_COMPOSITION, SUBJECT_METRICS, CA2Metrics
from runtime.agents.cax_summariser import CAxSummariser
from runtime.llm import Completion
from yprov4agenticai import ProvTracker
from yprov4agenticai.prompts import ACT_SYSTEM

NO_BUS = None
T0 = "2026-10-08T09:00:00+00:00"
T1 = "2026-10-08T09:00:01+00:00"


def fake_completion(text: str, finish_reason: str = "stop") -> Completion:
    """A model response with plausible accounting, for tests that must not call a model.

    Built from the real :class:`Completion` rather than a stub dict, so a change to what
    the record is supposed to carry about a model call breaks these tests too.
    """
    return Completion(
        text=text,
        model="gpt-4o",
        temperature=0.2,
        max_tokens=400,
        latency_s=0.42,
        prompt=ACT_SYSTEM,
        prompt_tokens=310,
        completion_tokens=88,
        finish_reason=finish_reason,
    )


def producer_document(*, numeric_metrics: bool = False) -> dict[str, Any]:
    """A producer record. yProv4ML's real records carry no numeric prov:value entities."""
    tracker = ProvTracker(sid="subject", agent_label="yprov4ml-replay")
    for index in range(3):
        entity = tracker.register_entity("TaskEntity", f"artifact {index}", suffix=f"_{index}")
        tracker.record_activity(
            "ReceiveTaskActivity", f"step {index}", T0, T1, out_eid=entity, verbose=False
        )
    document = tracker.to_prov_json()
    if numeric_metrics:
        document["entity"]["yp:metric_loss"] = {
            "prov:type": {"$": "yp:Metric", "type": "prov:QUALIFIED_NAME"},
            "prov:label": "MSELoss",
            "prov:value": 0.42,
        }
    return document


@pytest.fixture
def cax(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CAxSummariser:
    import runtime.util

    monkeypatch.setattr(runtime.util, "OUT_DIR", tmp_path)
    monkeypatch.setattr(cax_summariser, "OUT_DIR", tmp_path)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("LLM_REQUIRED", raising=False)
    return CAxSummariser(NO_BUS)  # type: ignore[arg-type]


@pytest.fixture
def ca2(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> CA2Metrics:
    import runtime.util

    monkeypatch.setattr(runtime.util, "OUT_DIR", tmp_path)
    monkeypatch.setattr(ca2_metrics, "OUT_DIR", tmp_path)
    return CA2Metrics(NO_BUS)  # type: ignore[arg-type]


def decision_of(document: dict[str, Any]) -> dict[str, Any]:
    for attributes in document["activity"].values():
        prov_type = attributes.get("prov:type", {})
        if isinstance(prov_type, dict) and prov_type.get("$") == "yp:DecisionActivity":
            return attributes
    raise AssertionError("no yp:DecisionActivity in the emitted record")


# ──────────────────────────────────────────────────────────────────────────────────
# CAx: the grounding check
# ──────────────────────────────────────────────────────────────────────────────────
def test_a_grounded_summary_is_accepted(cax: CAxSummariser) -> None:
    document, _ = cax.process("subject", producer_document(), "producer.yprov4ml.subject")

    decision = decision_of(document)
    assert decision["yp:verdict"] == "ACCEPT"
    assert "all supported" in decision["yp:justification"]


def test_an_invented_figure_is_caught_and_the_summary_sent_back(
    cax: CAxSummariser, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The check must be able to fail, or ACCEPT means nothing.

    A model told to use only the supplied facts can still produce a figure that is not
    among them. That is decidable, and the record should say so rather than asserting
    groundedness because the prompt asked for it.
    """
    monkeypatch.setattr(
        cax,
        "_summarise",
        lambda _doc, _counts: (
            "This run logged 4096 training steps.",
            "llm",
            fake_completion("This run logged 4096 training steps."),
        ),
    )

    document, log_line = cax.process(
        "subject", producer_document(), "producer.yprov4ml.subject"
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "REVISE", log_line
    assert "4096" in decision["yp:justification"]

    observation = next(
        attributes
        for attributes in document["activity"].values()
        if "yp:ungrounded_figure_count" in attributes
    )
    assert observation["yp:ungrounded_figure_count"] == 1


def test_figures_drawn_from_the_graph_are_not_flagged(cax: CAxSummariser) -> None:
    """Counts the graph does contain must pass, or every faithful summary would fail."""
    document = producer_document()
    counts = {"activities": 3, "entities": 3, "relations": 11}

    grounded, ungrounded = cax._check_grounding(
        f"The graph holds {counts['entities']} entities and {counts['relations']} relations.",
        document,
        counts,
    )

    assert grounded, f"flagged figures drawn from the graph itself: {ungrounded}"


def test_small_numbers_in_ordinary_prose_are_not_treated_as_claims(
    cax: CAxSummariser,
) -> None:
    """ "3-4 sentences" is language, not an assertion about the data."""
    grounded, _ = cax._check_grounding(
        "In 3-4 sentences: the run had 2 phases and 1 outcome.",
        producer_document(),
        {"activities": 3, "entities": 3, "relations": 11},
    )

    assert grounded


def test_digits_inside_a_name_are_not_figures(cax: CAxSummariser) -> None:
    """A run called corpus_17_GR0_17 is named, not counted. Found on the 24-run corpus."""
    grounded, ungrounded = cax._check_grounding(
        "Tracked steps include corpus_17_GR0_17, Epoch_12 and model_v2.0.",
        producer_document(),
        {"activities": 3, "entities": 3, "relations": 11},
    )

    assert grounded, f"flagged digits inside identifiers: {ungrounded}"


def test_a_standalone_figure_is_still_checked(cax: CAxSummariser) -> None:
    grounded, ungrounded = cax._check_grounding(
        "The run logged 4096 samples, 512 of them held out.",
        producer_document(),
        {"activities": 3, "entities": 3, "relations": 11},
    )

    assert not grounded
    assert ungrounded == ["4096", "512"]


def test_the_record_states_which_path_produced_the_summary(cax: CAxSummariser) -> None:
    """Without a key the deterministic path runs, and the record must say so.

    This is what makes a model-backed run distinguishable from a deterministic one. The
    two were previously indistinguishable in the emitted provenance.
    """
    document, _ = cax.process("subject", producer_document(), "producer.yprov4ml.subject")

    assert decision_of(document)["yp:summary_mode"] == "computed"


# ──────────────────────────────────────────────────────────────────────────────────
# CA2: which chart was actually produced
# ──────────────────────────────────────────────────────────────────────────────────
def test_plotting_real_measurements_is_accepted(ca2: CA2Metrics) -> None:
    document, log_line = ca2.process(
        "subject", producer_document(numeric_metrics=True), "producer.yprov4ml.subject"
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "ACCEPT", log_line
    assert decision["yp:chart_subject"] == SUBJECT_METRICS
    assert decision["yp:series_plotted"] >= 1
    assert "numeric metric series" in decision["yp:justification"]


def test_falling_back_to_composition_is_recorded_as_a_degraded_result(
    ca2: CA2Metrics,
) -> None:
    """The real yProv4ML records take this branch, and it was previously invisible.

    A chart of graph composition describes the provenance record, not the run it
    documents. Reporting it as a plain accept hid the fact that nothing measurable was
    plotted at all.
    """
    document, log_line = ca2.process(
        "subject", producer_document(), "producer.yprov4ml.subject"
    )

    decision = decision_of(document)
    assert decision["yp:verdict"] == "REVISE", log_line
    assert decision["yp:chart_subject"] == SUBJECT_COMPOSITION
    assert "no numeric prov:value entities" in decision["yp:justification"]
    assert "less informative than intended" in decision["yp:justification"]


def test_the_chart_is_confirmed_on_disk_before_being_claimed(ca2: CA2Metrics) -> None:
    """The deliverable is a file, so the record should establish the file exists."""
    document, _ = ca2.process(
        "subject", producer_document(numeric_metrics=True), "producer.yprov4ml.subject"
    )

    observation = next(
        attributes
        for attributes in document["activity"].values()
        if "yp:rendered_bytes" in attributes
    )
    assert observation["yp:rendered_bytes"] > 0
    # The count itself is deliberately NOT quoted in the justification: a rendered
    # PNG's size varies with the matplotlib build and host fonts, so a run that did
    # the same thing would otherwise produce a different claim on another platform.
    justification = decision_of(document)["yp:justification"]
    assert "non-empty" in justification
    assert str(observation["yp:rendered_bytes"]) not in justification
