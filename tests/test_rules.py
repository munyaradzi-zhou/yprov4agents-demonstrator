"""Replayable decisions: a recorded verdict must be the output of its recorded rule.

The justification is prose and cannot be checked against the code that wrote it. The rule
and its inputs can, so these tests hold the line in three places: at the call that records
a decision (a verdict the rule does not give is refused), in the record afterwards (an
edited verdict is caught by replay), and in the one rule whose body was refactored out of
an agent (CA3's timing test must agree with the agent's own outlier search on any input).
"""

from __future__ import annotations

import json
import random
from pathlib import Path
from typing import Any

import pytest

from eval.replay import input_visibility, renderings
from runtime.agents.ca1_storage import PERSIST_RULE
from runtime.agents.ca3_analytics import TIMING_RULE, CA3Analytics, timing_evidence
from yprov4agenticai import ProvTracker
from yprov4agenticai.rules import Rule, canonical, lookup, replay

_FLAG = Rule("test.flag/v1", lambda e: {"yp:verdict": "ACCEPT" if e["ok"] else "REVISE"})


def _decide(verdict: str, **kwargs: Any) -> dict:
    tracker = ProvTracker(sid="r0", agent_label="Test_Agent", verbose=False)
    tracker.decide(verdict, "because the flag says so", **kwargs)
    return tracker.to_prov_json()


def _decision(doc: dict) -> dict:
    return next(a for a in doc["activity"].values() if "yp:verdict" in a)


# ── at the call ───────────────────────────────────────────────────────────────────
def test_a_decision_records_its_rule_and_canonical_inputs() -> None:
    act = _decision(_decide("ACCEPT", rule=_FLAG, evidence={"ok": True, "n": 3}))
    assert act["yp:rule"] == "test.flag/v1"
    assert act["yp:rule_inputs"] == '{"n":3,"ok":true}'


def test_a_verdict_the_rule_does_not_give_is_refused() -> None:
    with pytest.raises(ValueError, match="disagrees with its rule"):
        _decide("ACCEPT", rule=_FLAG, evidence={"ok": False})


def test_an_escalation_reason_is_held_to_the_rule_too() -> None:
    inputs = {
        "integrity_ok": False,
        "envelopes_missing": 0,
        "final_envelope_seen": True,
        "persist_trigger": "quorum",
    }
    with pytest.raises(ValueError, match="yp:escalation_reason"):
        _decide(
            "ESCALATE",
            rule=PERSIST_RULE,
            evidence=inputs,
            attributes={"yp:escalation_reason": "consumption-unconfirmed"},
        )


def test_rule_and_inputs_come_together_or_not_at_all() -> None:
    with pytest.raises(ValueError, match="together"):
        _decide("ACCEPT", rule=_FLAG)
    with pytest.raises(ValueError, match="together"):
        _decide("ACCEPT", evidence={"ok": True})
    assert "yp:rule" not in _decision(_decide("ACCEPT"))


def test_a_rule_name_carries_a_version_and_one_body() -> None:
    with pytest.raises(ValueError, match="version"):
        Rule("test.unversioned", lambda e: {})
    with pytest.raises(ValueError, match="another body"):
        Rule("test.flag/v1", lambda e: {"yp:verdict": "ACCEPT"})
    assert lookup("test.flag/v1") is _FLAG


@pytest.mark.parametrize("bad", [[1, 2], {"a": 1}, float("nan"), float("inf")])
def test_inputs_are_finite_scalars(bad: object) -> None:
    with pytest.raises((TypeError, ValueError)):
        canonical({"x": bad})  # type: ignore[dict-item]


# ── in the record ─────────────────────────────────────────────────────────────────
def test_a_recorded_decision_replays() -> None:
    [result] = replay(_decide("REVISE", rule=_FLAG, evidence={"ok": False}))
    assert result.status == "replayed"


def test_an_edited_verdict_does_not_replay() -> None:
    doc = _decide("REVISE", rule=_FLAG, evidence={"ok": False})
    _decision(doc)["yp:verdict"] = "ACCEPT"
    [result] = replay(doc)
    assert result.status == "mismatch"
    assert "ACCEPT" in result.detail


def test_edited_inputs_do_not_replay() -> None:
    doc = _decide("REVISE", rule=_FLAG, evidence={"ok": False})
    _decision(doc)["yp:rule_inputs"] = '{"ok":true}'
    assert replay(doc)[0].status == "mismatch"


def test_an_unknown_rule_and_a_ruleless_decision_are_reported_not_passed() -> None:
    doc = _decide("REVISE", rule=_FLAG, evidence={"ok": False})
    _decision(doc)["yp:rule"] = "test.flag/v2"
    assert replay(doc)[0].status == "unknown-rule"
    assert replay(_decide("ACCEPT"))[0].status == "no-rule"


# ── the refactored rule ───────────────────────────────────────────────────────────
def _timing(gaps: list[float], wall_clock: float) -> dict[str, Any]:
    return {"gaps": [(f"a{i}", g) for i, g in enumerate(gaps)], "wall_clock_s": wall_clock}


def test_ca3s_rule_agrees_with_its_outlier_search_on_any_input() -> None:
    rng = random.Random(20261017)
    disagreements = []
    for _ in range(2000):
        n = rng.randint(0, 12)
        gaps = [round(rng.choice([0.0, 0.001, rng.expovariate(1.0)]), 6) for _ in range(n)]
        if gaps and rng.random() < 0.5:
            gaps[rng.randrange(n)] = round(rng.uniform(0.0, 120.0), 6)
        timing = _timing(gaps, round(sum(gaps) + rng.uniform(0.0, 5.0), 3))
        found = bool(CA3Analytics._timing_outliers(timing))
        ruled = TIMING_RULE.apply(timing_evidence(timing))["yp:verdict"] == "ANOMALY"
        if found != ruled:
            disagreements.append(gaps)
    assert not disagreements, disagreements[:3]


# ── what the rule read, against what A saw ────────────────────────────────────────
def test_renderings_do_not_let_a_small_float_round_to_zero() -> None:
    assert "0.000" not in renderings(7.8e-05)
    assert "5.049" in renderings(5.049196)


def test_input_visibility_separates_checks_unset_values_and_searchable_ones() -> None:
    doc = _decide("REVISE", rule=_FLAG, evidence={"ok": False})
    act = _decision(doc)
    act["yp:rule_inputs"] = canonical({"ok": False, "gap": 5.049196, "median": None, "n": 12})
    act["yp:justification"] = "gap 5.049s"
    tally = input_visibility([(Path("x__ca3_prov.json"), doc)], ["wall clock 12 s"])["ca3"]
    assert (tally.inputs, tally.tests, tally.unset, tally.searchable) == (4, 1, 1, 2)
    assert (tally.seen_by_a, tally.stated) == (1, 1)


# ── the run ───────────────────────────────────────────────────────────────────────
def test_every_decision_in_the_run_names_its_rule_and_replays(
    prov_documents: list[tuple[Path, dict[str, Any]]],
) -> None:
    import eval.replay  # noqa: F401  registers every agent's rule

    results = [r for _, doc in prov_documents for r in replay(doc)]
    assert results
    assert {r.status for r in results} == {"replayed"}, [
        (r.activity, r.status, r.detail) for r in results if r.status != "replayed"
    ]
    assert all(json.loads(_decision(doc)["yp:rule_inputs"]) for _, doc in prov_documents)
