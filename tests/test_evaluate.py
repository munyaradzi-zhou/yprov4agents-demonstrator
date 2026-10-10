"""The evaluation's own arithmetic, on records small enough to check by hand."""

from __future__ import annotations

import json

from eval.evaluate import justification_bytes
from yprov4agenticai import ProvTracker


def _record(justification: str) -> dict[str, object]:
    tracker = ProvTracker(sid="s0", agent_label="Test_Agent", verbose=False)
    with tracker.activity("ReceiveTaskActivity", "Test: receive the task") as prov:
        prov.produces("TaskEntity", "summarise the run")
    tracker.decide("ACCEPT", justification, label="Test: accept")
    return tracker.to_prov_json()


def test_the_cost_of_a_justification_is_what_it_adds_to_the_serialised_record() -> None:
    """Key, quoting and indentation included — not just the length of the text."""
    text = "Checked every figure of 10 or more against the graph: all supported."
    spent, whole = justification_bytes(_record(text))
    line = f'      "yp:justification": {json.dumps(text)},\n'
    assert spent == len(line.encode("utf-8"))
    assert whole == len(json.dumps(_record(text), indent=2, ensure_ascii=False).encode())


def test_a_longer_justification_costs_exactly_its_extra_characters() -> None:
    short, _ = justification_bytes(_record("Verdict ACCEPT."))
    longer, _ = justification_bytes(_record("Verdict ACCEPT, after checking."))
    assert longer - short == len(", after checking")


def test_a_record_without_justifications_costs_nothing() -> None:
    record = _record("Verdict ACCEPT.")
    for activity in record["activity"].values():  # type: ignore[attr-defined]
        activity.pop("yp:justification", None)
    spent, whole = justification_bytes(record)
    assert spent == 0
    assert whole > 0
