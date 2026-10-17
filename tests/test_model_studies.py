"""The model-based studies: their scoring, never their model calls."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.cases import Case
from eval.diagnose import OPTIONS, _order, cause, mcnemar_exact, wilson
from eval.llm_calls import OfflineMiss, ask
from eval.reconstruct import same_verdict


def _case(role: str, verdict: str, reason: str = "", **inputs: object) -> Case:
    return Case(
        case_id=f"t/{role}/{verdict}",
        source="fault",
        role=role,
        session="s",
        verdict=verdict,
        escalation_reason=reason,
        rule="",
        rule_inputs=dict(inputs),
        justification="",
        record_b={},
        record_a={},
        events=[],
    )


@pytest.mark.parametrize(
    ("case", "expected"),
    [
        (_case("ca1", "ESCALATE", "integrity"), "integrity"),
        (_case("ca1", "ACCEPT"), "none"),
        (_case("cax", "REVISE", ungrounded_figures=2, truncated=False), "ungrounded"),
        (_case("cax", "REVISE", ungrounded_figures=0, truncated=True), "truncated"),
        (_case("cax", "INCOMPLETE", envelopes_missing=1), "incomplete"),
        (
            _case("ca2", "REVISE", rendered_bytes=900, chart_subject="graph-composition"),
            "fallback",
        ),
        (
            _case("ca2", "REVISE", rendered_bytes=0, chart_subject="numeric-metrics"),
            "render_failed",
        ),
        (_case("ca2", "ACCEPT", rendered_bytes=900, chart_subject="numeric-metrics"), "none"),
        (_case("ca3", "ANOMALY"), "stall"),
        (_case("ca3", "OK"), "none"),
    ],
)
def test_the_right_answer_comes_from_the_rule_not_a_model(case: Case, expected: str) -> None:
    assert cause(case) == expected
    assert expected in OPTIONS[case.role]


def test_option_orders_differ_between_shuffles_and_repeat_exactly() -> None:
    case = _case("ca1", "ACCEPT")
    orders = [_order(case, k) for k in range(3)]
    assert orders == [_order(case, k) for k in range(3)]
    assert len({tuple(o) for o in orders}) > 1
    assert all(sorted(o) == sorted(OPTIONS["ca1"]) for o in orders)


def test_free_text_verdicts_match_their_token() -> None:
    assert same_verdict("escalated", "ESCALATE")
    assert same_verdict("ANOMALY", "ANOMALY")
    assert not same_verdict("ACCEPT", "ESCALATE")


def test_interval_and_paired_test() -> None:
    low, high = wilson(9, 10)
    assert low < 0.9 < high <= 1.0
    assert mcnemar_exact(0, 0) == 1.0
    assert mcnemar_exact(10, 0) == pytest.approx(2 / 2**10)


def test_the_cache_answers_and_offline_refuses_to_call(tmp_path: Path) -> None:
    with pytest.raises(OfflineMiss):
        ask("s", "u", cache=tmp_path, offline=True)
    answer_text = json.dumps({"choice": "B"})
    # Seed the cache with the file the request would have written.
    probe = None
    try:
        ask("s", "u", cache=tmp_path, offline=True)
    except OfflineMiss as miss:
        probe = str(miss).split()[-1]
    assert probe
    import hashlib

    request = {
        "model": "gpt-4o-2024-08-06",
        "messages": [{"role": "system", "content": "s"}, {"role": "user", "content": "u"}],
        "temperature": 0.0,
        "max_tokens": 500,
        "seed": 20261017,
        "response_format": {"type": "json_object"},
    }
    key = hashlib.sha256(json.dumps(request, sort_keys=True).encode("utf-8")).hexdigest()
    path = tmp_path / key[:2] / f"{key}.json"
    path.parent.mkdir(parents=True)
    response = {
        "model": "gpt-4o-2024-08-06",
        "choices": [{"message": {"content": answer_text}}],
        "usage": {"prompt_tokens": 3, "completion_tokens": 2},
    }
    path.write_text(json.dumps({"request": request, "response": response}), encoding="utf-8")
    answer = ask("s", "u", cache=tmp_path, offline=True)
    assert answer.cached and answer.data == {"choice": "B"} and answer.prompt_tokens == 3
