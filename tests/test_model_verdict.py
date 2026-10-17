"""CAx with the model deciding: the verdict is the model's, the code's is kept beside it.

Offline: the model is a stand-in that answers by prompt, so these hold without a key.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval.fault_injection import _headless, _well_instrumented_record
from runtime.agents.cax_summariser import REVIEW_RULE, CAxSummariser, parse_review
from runtime.llm import Completion
from yprov4agenticai import LightweightStore
from yprov4agenticai.prompts import REVIEW_SYSTEM
from yprov4agenticai.rules import replay


class ByPrompt:
    """Answers the summary prompt with ``summary`` and the review prompt with ``review``."""

    model, temperature, max_tokens = "stand-in", 0.0, 400

    def __init__(self, summary: str, review: str, finish_reason: str = "stop") -> None:
        self.replies = {"summary": summary, "review": review}
        self.finish_reason = finish_reason

    def complete(self, prompt: Any, user: str) -> Completion:
        text = self.replies["review" if prompt.id == REVIEW_SYSTEM.id else "summary"]
        return Completion(
            text=text,
            model=self.model,
            temperature=0.0,
            max_tokens=400,
            latency_s=0.0,
            prompt=prompt,
            finish_reason=self.finish_reason,
        )


def _run(tmp_path: Path, summary: str, review: str, **kw: Any) -> dict[str, Any]:
    agent = _headless(
        CAxSummariser,
        tmp_path,
        store=LightweightStore(root=str(tmp_path / "store")),
        llm=ByPrompt(summary, review, **kw),
    )
    record, _ = agent.process("mv", _well_instrumented_record(), "producer.yprov4ml.test")
    return record


def _decision(record: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in record["activity"].values() if "yp:verdict" in a)


def _answer(verdict: str, issue: str = "none", evidence: str = "") -> str:
    return json.dumps(
        {"verdict": verdict, "issue": issue, "evidence": evidence, "searched": ""}
    )


@pytest.fixture(autouse=True)
def _model_decides(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("CAX_VERDICT", "model")


def test_the_models_verdict_stands_and_the_codes_is_kept_beside_it(tmp_path: Path) -> None:
    # The summary invents 4096, so the code check says REVISE; the model accepts it anyway.
    record = _run(tmp_path, "This run logged 4096 steps.", _answer("ACCEPT"))
    decision = _decision(record)
    assert decision["yp:verdict"] == "ACCEPT"
    assert decision["yp:verdict_source"] == "model"
    assert decision["yp:code_verdict"] == "REVISE"
    assert decision["yp:rule"] == "cax.review/v1"
    assert "disagree" in decision["yp:justification"]
    assert {r.status for r in replay(record)} == {"replayed"}


def test_the_review_is_its_own_recorded_step(tmp_path: Path) -> None:
    record = _run(tmp_path, "A faithful summary.", _answer("ACCEPT"))
    review = [
        a for a in record["activity"].values() if a.get("yp:prompt_id") == "review.system"
    ]
    assert len(review) == 1
    assert review[0]["yp:prompt_version"] == REVIEW_SYSTEM.version


def test_an_unreadable_answer_is_not_accepted(tmp_path: Path) -> None:
    record = _run(tmp_path, "A faithful summary.", "Looks fine to me!")
    decision = _decision(record)
    assert decision["yp:verdict"] == "REVISE"
    assert json.loads(decision["yp:rule_inputs"])["review_parsed"] is False
    assert "could not be read" in decision["yp:justification"]


def test_a_cut_off_summary_is_not_accepted_whatever_the_model_says(tmp_path: Path) -> None:
    record = _run(
        tmp_path, "A summary that stops in the", _answer("ACCEPT"), finish_reason="length"
    )
    assert _decision(record)["yp:verdict"] == "REVISE"


@pytest.mark.parametrize(
    ("answer", "verdict"),
    [
        (_answer("REVISE", "unsupported_specific", "4096"), "REVISE"),
        (_answer("ACCEPT"), "ACCEPT"),
        (_answer("MAYBE"), "REVISE"),
        (_answer("ACCEPT", "vibes"), "REVISE"),
        ('Sure: {"verdict": "ACCEPT", "issue": "none"} hope that helps', "ACCEPT"),
    ],
)
def test_the_rule_reads_only_what_the_contract_allows(answer: str, verdict: str) -> None:
    inputs = {**parse_review(answer), "truncated": False}
    assert REVIEW_RULE.apply(inputs)["yp:verdict"] == verdict


def test_without_the_switch_code_decides_and_nothing_new_is_recorded(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("CAX_VERDICT", "code")
    record = _run(tmp_path, "This run logged 4096 steps.", _answer("ACCEPT"))
    decision = _decision(record)
    assert decision["yp:verdict"] == "REVISE"
    assert decision["yp:rule"] == "cax.summary/v1"
    assert "yp:verdict_source" not in decision and "yp:code_verdict" not in decision
