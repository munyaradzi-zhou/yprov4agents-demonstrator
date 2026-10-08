"""Can a model call be repeated from the record alone?

For an *agentic AI* provenance paper this is the load-bearing question, and before Phase 4
the answer was no: the emitted graph named no model, no sampling parameters, no prompt and
no token counts (review finding F1), while the prompt registry that existed for exactly
this purpose was imported by nothing (F2).

These tests pin the shape of that accounting, and they do it without calling a model — the
vendor call is injected, so what is under test is the provenance rather than the network.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest

from runtime.agents.cax_summariser import CAxSummariser
from runtime.llm import Completion, LLMClient
from yprov4agenticai.prompts import ACT_SYSTEM, NARRATE_FINDINGS, PROMPTS, Prompt

REPLY = "Nine activities and thirty-nine entities, linked by fifty-five relations."


def fake_raw(
    reply: str = REPLY,
    prompt_tokens: int | None = 310,
    completion_tokens: int | None = 88,
    finish_reason: str | None = "stop",
) -> Any:
    """A vendor call that records what it was asked for, so the test can check it."""
    seen: dict[str, Any] = {}

    def raw(
        model: str, temperature: float, max_tokens: int, system: str, user: str
    ) -> tuple[str, int | None, int | None, str | None]:
        seen.update(
            model=model,
            temperature=temperature,
            max_tokens=max_tokens,
            system=system,
            user=user,
        )
        return reply, prompt_tokens, completion_tokens, finish_reason

    raw.seen = seen  # type: ignore[attr-defined]
    return raw


# ── the client ────────────────────────────────────────────────────────────────────
def test_the_client_sends_the_registered_prompt_as_the_system_message() -> None:
    """The instruction comes from the registry, not from an f-string at the call site."""
    raw = fake_raw()
    client = LLMClient(raw, model="gpt-4o")
    client.complete(ACT_SYSTEM, "the facts")

    assert raw.seen["system"] == ACT_SYSTEM.text
    assert raw.seen["user"] == "the facts"
    assert raw.seen["model"] == "gpt-4o"


def test_a_completion_accounts_for_itself() -> None:
    completion = LLMClient(fake_raw(), model="gpt-4o").complete(ACT_SYSTEM, "the facts")
    attributes = completion.provenance()

    assert attributes["yp:model"] == "gpt-4o"
    assert attributes["yp:temperature"] == 0.2
    assert attributes["yp:max_tokens"] == 400
    assert attributes["yp:prompt_tokens"] == 310
    assert attributes["yp:completion_tokens"] == 88
    assert attributes["yp:finish_reason"] == "stop"
    assert attributes["yp:llm_latency_s"] >= 0
    assert len(attributes["yp:response_sha"]) == 12


def test_the_prompt_is_cited_by_identity_version_and_hash() -> None:
    """A prompt is apparatus: two runs differ if it did, so the record has to pin it."""
    completion = LLMClient(fake_raw()).complete(ACT_SYSTEM, "the facts")
    attributes = completion.provenance()

    assert attributes["yp:prompt_id"] == "act.system"
    assert attributes["yp:prompt_version"] == ACT_SYSTEM.version
    assert attributes["yp:prompt_sha"] == ACT_SYSTEM.sha


def test_editing_a_prompt_changes_its_hash_even_without_a_version_bump() -> None:
    """A forgotten version bump is still detectable, which is why the hash is recorded."""
    original = Prompt(id="x", version="1.0", text="do the thing")
    edited = Prompt(id="x", version="1.0", text="do the thing carefully")
    assert original.sha != edited.sha


def test_absent_token_counts_are_absent_rather_than_zero() -> None:
    """A zero is a measurement. If the API reported nothing, the record must not invent one."""
    completion = LLMClient(fake_raw(prompt_tokens=None, completion_tokens=None)).complete(
        ACT_SYSTEM, "the facts"
    )
    attributes = completion.provenance()
    assert "yp:prompt_tokens" not in attributes
    assert "yp:completion_tokens" not in attributes


def test_a_truncated_response_is_identifiable() -> None:
    finished = LLMClient(fake_raw(finish_reason="stop")).complete(ACT_SYSTEM, "f")
    cut_off = LLMClient(fake_raw(finish_reason="length")).complete(ACT_SYSTEM, "f")
    assert not finished.truncated
    assert cut_off.truncated


def test_every_prompt_an_agent_uses_is_in_the_registry() -> None:
    """The registry is the single source of truth, or it is decoration."""
    assert PROMPTS[ACT_SYSTEM.id] is ACT_SYSTEM
    assert PROMPTS[NARRATE_FINDINGS.id] is NARRATE_FINDINGS


# ── the record an agent emits ─────────────────────────────────────────────────────
@pytest.fixture
def cax(headless_agent: Callable[..., CAxSummariser]) -> CAxSummariser:
    return headless_agent(CAxSummariser)


def _producer_document() -> dict[str, Any]:
    return {
        "prefix": {"prov": "http://www.w3.org/ns/prov#"},
        "activity": {"a": {"prov:label": "step"}},
        "entity": {"e": {"prov:value": "x"}},
    }


def _llm_step(document: dict[str, Any]) -> dict[str, Any]:
    return next(
        attributes
        for attributes in document["activity"].values()
        if attributes["prov:type"]["$"] == "yp:ToolCallActivity"
    )


def test_the_model_path_records_model_and_prompt_on_the_activity(
    cax: CAxSummariser,
) -> None:
    cax.llm = LLMClient(fake_raw(), model="gpt-4o")
    document, _ = cax.process("s", _producer_document(), "producer.yprov4ml.s")

    step = _llm_step(document)
    assert step["yp:summary_mode"] == "llm"
    assert step["yp:model"] == "gpt-4o"
    assert step["yp:prompt_id"] == "act.system"
    assert step["yp:prompt_sha"] == ACT_SYSTEM.sha
    assert step["yp:prompt_tokens"] == 310


def test_the_computed_path_records_no_model_attributes(cax: CAxSummariser) -> None:
    """Nothing to account for, so nothing is claimed. An absence here is the truth."""
    document, _ = cax.process("s", _producer_document(), "producer.yprov4ml.s")

    step = _llm_step(document)
    assert step["yp:summary_mode"] == "computed"
    assert not [key for key in step if key.startswith(("yp:model", "yp:prompt_"))]


def test_a_failed_model_call_says_so_rather_than_claiming_the_model_path(
    cax: CAxSummariser,
) -> None:
    def explode(*_args: object) -> tuple[str, int | None, int | None, str | None]:
        raise TimeoutError("the model did not answer")

    cax.llm = LLMClient(explode)
    document, _ = cax.process("s", _producer_document(), "producer.yprov4ml.s")

    step = _llm_step(document)
    assert step["yp:summary_mode"] == "computed-after-TimeoutError"
    assert "yp:model" not in step


def test_a_truncated_summary_is_sent_back_even_when_it_is_grounded(
    cax: CAxSummariser,
) -> None:
    """Two independent faults, so the verdict has to name which one applies.

    A summary cut off on its token budget is a different artifact from one that invented a
    figure: the first is incomplete, the second is unfaithful, and only the second is a
    grounding problem. Reporting both as REVISE with the same text would hide that.
    """
    cax.llm = LLMClient(fake_raw(reply="Nine activities and", finish_reason="length"))
    document, _ = cax.process("s", _producer_document(), "producer.yprov4ml.s")

    decision = next(
        attributes
        for attributes in document["activity"].values()
        if attributes.get("yp:verdict")
    )
    assert decision["yp:verdict"] == "REVISE"
    assert "token limit" in decision["yp:justification"]
    assert "all supported" in decision["yp:justification"], (
        "the grounding check passed, and the justification should still say so"
    )


def test_the_completion_type_is_what_the_agents_record() -> None:
    """Guards against the record and the client drifting apart."""
    completion = Completion(
        text="x",
        model="m",
        temperature=0.0,
        max_tokens=1,
        latency_s=0.0,
        prompt=NARRATE_FINDINGS,
    )
    assert set(completion.provenance()) == {
        "yp:model",
        "yp:temperature",
        "yp:max_tokens",
        "yp:llm_latency_s",
        "yp:response_sha",
        "yp:prompt_id",
        "yp:prompt_version",
        "yp:prompt_sha",
    }
