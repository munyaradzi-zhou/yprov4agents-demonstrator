"""
cax_summariser.py — CAx: the NL provenance summariser (operational agent).

Consumes a producer's yProv4ML PROV-JSON, writes a natural-language summary of it,
and emits its OWN typed W3C PROV lifecycle (the Reflexive Property: the summariser
is itself provenance-tracked). The DecisionActivity carries `yp:justification` — the
project's headline field.

LLM is optional: if OPENAI_API_KEY is set, the summary is written by the model;
otherwise a deterministic stub summary is produced, so the pipeline runs with no key.

Which path ran is recorded either way, and the model path records enough to be repeated:
the prompt's id, version and content hash come from the registry in
``yprov4agenticai.prompts``, and the model, sampling parameters, token counts and latency
come from ``runtime.llm``. Before Phase 4 the emitted graph named none of them, so the one
step in the pipeline that was not deterministic was the one step the record could not
account for (review findings F1 and F2).

Who decides the verdict is a choice. By default code does: the summary's figures are checked
against the graph (``cax.summary/v1``). With ``CAX_VERDICT=model`` and a model available, the
model decides: it reviews the summary against the producer's record under the registered
``review.system`` prompt and answers in a fixed JSON form, and ``cax.review/v1`` turns that
answer into the verdict. The code check still runs and is recorded beside it as a second
opinion (``yp:code_verdict``), so the record shows where the two disagree. Replay re-applies
the rule to the model's recorded answer; it cannot re-run the model, and does not claim to.
"""

from __future__ import annotations

import json
import os
import re
from collections.abc import Mapping
from typing import Any

from yprov4agenticai import LightweightStore
from yprov4agenticai.prompts import ACT_SYSTEM, REVIEW_SYSTEM
from yprov4agenticai.rules import Rule, Scalar

from ..base_agent import StreamingAgent
from ..llm import Completion, get_llm, llm_is_required
from ..net_bus import Bus
from ..util import OUT_DIR, agent_tracker, prov_counts

#: Numerals in prose that are units or ordinary language rather than claims about the
#: graph ("3-4 sentences", "one of"). Grounding small integers against the fact set would
#: reject faithful summaries, so the check applies to figures a model could plausibly
#: invent about the data.
_UNGROUNDED_FLOOR = 10

#: A figure standing on its own. Digits inside an identifier ("corpus_17_GR0_17") are part
#: of a name the summary repeats, not a quantity it asserts; matching them flagged every
#: summary of a run whose name carried a number of 10 or more, which a corpus of 24 runs
#: showed and the two bundled examples never could. A trailing comma is punctuation.
_NUMERAL = re.compile(r"(?<![\w.])\d(?:[\d,]*\d)?(?:\.\d+)?(?!\w)")

#: How many activity names the facts message carries. A ceiling, not a target: the point is
#: to give the model something concrete to name, not to pass it the whole graph.
_FACT_ACTIVITIES = 6


def _summary_rule(evidence: Mapping[str, Scalar]) -> dict[str, str]:
    """ACCEPT a summary that asserts no figure the graph lacks and was not cut off."""
    accept = evidence["ungrounded_figures"] == 0 and not evidence["truncated"]
    return {"yp:verdict": "ACCEPT" if accept else "REVISE"}


SUMMARY_RULE = Rule("cax.summary/v1", _summary_rule)

#: The issues a review may name. Anything else is an answer the rule cannot read.
REVIEW_ISSUES = ("none", "unsupported_specific", "contradiction")

#: Longest piece of a model's answer kept as a rule input. The full answer is an entity.
_EVIDENCE_CHARS = 200


def _review_rule(evidence: Mapping[str, Scalar]) -> dict[str, str]:
    """The model's verdict, where the model gave a readable one; otherwise REVISE.

    Fails closed: an answer that does not parse, or names a verdict or issue outside the
    contract, cannot be acted on, and a summary nobody could review is not accepted. A
    summary cut off by its token budget is not accepted whatever the model said of it.
    """
    readable = bool(evidence["review_parsed"]) and evidence["model_verdict"] in (
        "ACCEPT",
        "REVISE",
    )
    if not readable or evidence["model_issue"] not in REVIEW_ISSUES or evidence["truncated"]:
        return {"yp:verdict": "REVISE"}
    return {"yp:verdict": str(evidence["model_verdict"])}


REVIEW_RULE = Rule("cax.review/v1", _review_rule)


def verdict_by_model() -> bool:
    """Whether the deployment asked for the model to decide (``CAX_VERDICT=model``)."""
    return os.environ.get("CAX_VERDICT", "code").strip().lower() == "model"


def parse_review(text: str) -> dict[str, Scalar]:
    """The model's answer as rule inputs: what it decided, why, and whether it was readable."""
    try:
        start, end = text.index("{"), text.rindex("}") + 1
        answer = json.loads(text[start:end])
        parsed = isinstance(answer, dict)
    except ValueError:
        answer, parsed = {}, False
    if not parsed:
        answer = {}
    return {
        "review_parsed": parsed,
        "model_verdict": str(answer.get("verdict", "")).strip().upper(),
        "model_issue": str(answer.get("issue", "")).strip().lower(),
        "model_evidence": str(answer.get("evidence", ""))[:_EVIDENCE_CHARS],
    }


class CAxSummariser(StreamingAgent):
    agent_label = "NL_Summarizer"
    input_subject = "producer.>"
    output_prefix = "agent.lifecycle"

    def __init__(self, bus: Bus) -> None:
        super().__init__(role="cax", bus=bus)
        self.llm = get_llm()
        self.store = LightweightStore(root=str(OUT_DIR / "store"))

    @staticmethod
    def _facts(doc: dict[str, Any], c: dict[str, int]) -> str:
        """The user message: everything the summary is entitled to mention, and no more.

        Separate from the instruction, which lives in the prompt registry. The split is
        what makes the prompt versionable: the contract is stable across runs and the facts
        are not, so only one of the two belongs under a content hash.
        """
        activities = list((doc.get("activity") or {}).keys())[:_FACT_ACTIVITIES]
        return (
            f"Summarise this W3C PROV document from a machine-learning run in 3-4 "
            f"sentences. It has {c['activities']} activities, {c['entities']} entities, "
            f"and {c['relations']} lineage relations. Key activities: "
            f"{', '.join(activities)}."
        )

    def _summarise(
        self, doc: dict[str, Any], c: dict[str, int]
    ) -> tuple[str, str, Completion | None]:
        """Return (summary, mode, completion), where mode records the path actually taken.

        The mode is an outcome, not a configuration: an agent that was given a model but
        whose call failed produced computed output, and must say so. The completion is
        returned rather than discarded because the record needs its provenance — and is
        None on the computed path, where there is no model call to account for.
        """
        activities = list((doc.get("activity") or {}).keys())[:_FACT_ACTIVITIES]
        fallback_reason = ""
        if self.llm:
            try:
                completion = self.llm.complete(ACT_SYSTEM, self._facts(doc, c))
                return completion.text, "llm", completion
            except Exception as exc:
                if llm_is_required():
                    raise RuntimeError(
                        f"LLM_REQUIRED is set but the summary call failed: {exc}"
                    ) from exc
                print(f"[cax] LLM call failed ({exc}); falling back to computed", flush=True)
                fallback_reason = type(exc).__name__
        return (
            (
                f"This ML provenance record captures {c['activities']} activities and "
                f"{c['entities']} entities linked by {c['relations']} lineage relations. "
                f"Tracked steps include "
                f"{', '.join(a.split(':')[-1] for a in activities)}. "
                "The graph documents inputs, parameters and outputs of the training run."
            ),
            f"computed-after-{fallback_reason}" if fallback_reason else "computed",
            None,
        )

    @staticmethod
    def _grounding_facts(doc: dict[str, Any], c: dict[str, int]) -> set[str]:
        """Every figure the summary is entitled to state, drawn from the graph itself."""
        facts = {str(value) for value in c.values()}
        facts |= {
            str(len(doc.get(section) or {})) for section in ("activity", "entity", "agent")
        }
        return facts

    @classmethod
    def _check_grounding(
        cls, summary: str, doc: dict[str, Any], c: dict[str, int]
    ) -> tuple[bool, list[str]]:
        """Verify that every substantive figure in the summary comes from the graph.

        This is the check the justification used to assert without performing. The prompt
        instructs the model to use only the supplied facts; whether it complied is
        decidable, and a provenance record that claims a summary is grounded should have
        established it rather than restated the instruction.

        Deliberately narrow and one-sided. It cannot show a summary is faithful, only that
        a particular figure in it is unsupported — so an unsupported figure is evidence of
        a problem, while its absence is not evidence of correctness. The justification says
        exactly that much.
        """
        facts = cls._grounding_facts(doc, c)
        ungrounded = []
        for match in _NUMERAL.finditer(summary):
            literal = match.group()
            normalised = literal.replace(",", "")
            try:
                magnitude = float(normalised)
            except ValueError:  # pragma: no cover - the pattern only matches numerals
                continue
            if magnitude < _UNGROUNDED_FLOOR:
                continue
            if normalised not in facts and normalised.rstrip("0").rstrip(".") not in facts:
                ungrounded.append(literal)
        return not ungrounded, ungrounded

    def _justify(
        self,
        verdict: str,
        summary: str,
        mode: str,
        counts: dict[str, int],
        ungrounded: list[str],
        truncated: bool = False,
    ) -> str:
        """State what was summarised, by which path, and what the checks found."""
        if not ungrounded:
            finding = (
                f"Checked every figure of {_UNGROUNDED_FLOOR} or more against the "
                f"graph's own counts: all supported. This establishes no invented "
                f"figures, not that the summary is complete."
            )
        else:
            finding = (
                f"Figure(s) {', '.join(ungrounded)} appear in the summary but not in the "
                f"graph's counts, so the text asserts something the record does not "
                f"support."
            )
        cut = (
            " The model stopped on its token limit rather than at the end of a sentence, "
            "so the text is incomplete regardless of whether it is faithful."
            if truncated
            else ""
        )
        return (
            f"Summary of {len(summary)} characters produced by the {mode} path over a "
            f"{counts['activities']}-activity / {counts['entities']}-entity graph. "
            f"{finding}{cut} Verdict {verdict}."
        )

    def _review(
        self,
        tracker: Any,
        response: str,
        summary: str,
        doc: dict[str, Any],
        counts: dict[str, int],
        quality: str | None,
    ) -> tuple[dict[str, Scalar] | None, str | None]:
        """Ask the model for the verdict. Returns its parsed answer and the entity it made.

        Without a model, the code's verdict stands and the record says nothing of a review
        that never happened; with ``LLM_REQUIRED`` set, that is an error instead.
        """
        if not self.llm:
            if llm_is_required():
                raise RuntimeError("CAX_VERDICT=model and LLM_REQUIRED are set, but no model")
            print("[cax] CAX_VERDICT=model but no model; the code check decides", flush=True)
            return None, quality
        user = (
            f"FACTS\n{self._facts(doc, counts)}\n\n"
            f"RECORD\n{json.dumps(doc, ensure_ascii=False, separators=(',', ':'))}\n\n"
            f"SUMMARY\n{summary}"
        )
        with tracker.activity("ObservationActivity", "CAx: model reviews the summary") as prov:
            prov.consumes(response)
            completion = self.llm.complete(REVIEW_SYSTEM, user)
            review = parse_review(completion.text)
            produced = prov.produces("QualityEntity", completion.text, derived_from=response)
            prov.note(**completion.provenance())
        return review, produced

    @staticmethod
    def _justify_review(
        verdict: str, inputs: Mapping[str, Scalar], code_verdict: str, ungrounded: list[str]
    ) -> str:
        """The model's decision in its own terms, beside what the code check found."""
        if not inputs["review_parsed"]:
            said = "gave an answer that could not be read, so the summary is not accepted"
        else:
            said = f"answered {inputs['model_verdict']} (issue: {inputs['model_issue']}"
            said += (
                f"; evidence: {inputs['model_evidence']})" if inputs["model_evidence"] else ")"
            )
        code = (
            f"found figure(s) {', '.join(ungrounded)} unsupported"
            if ungrounded
            else "found no unsupported figure"
        )
        agreement = "agree" if code_verdict == verdict else "disagree"
        cut = " The summary was cut off by its token budget." if inputs["truncated"] else ""
        return (
            f"The model reviewed the summary against the producer's record under "
            f"{REVIEW_SYSTEM.id} v{REVIEW_SYSTEM.version} and {said}.{cut} The code check "
            f"{code}, verdict {code_verdict}; the two {agreement}. Verdict {verdict}, as the "
            f"model decided."
        )

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        tracker = agent_tracker(self.role, session_id, self.agent_label, energy=self.energy)

        # S1 Receive — what arrived, and from where.
        with tracker.activity("ReceiveTaskActivity", "CAx: receive producer record") as prov:
            counts = prov_counts(doc)
            task = prov.produces("TaskEntity", f"producer doc on {source_subject}")

        # S2 Plan — scoping the summary is a no-op in this agent, and the record now says
        # so honestly: an activity that does no work takes no measurable time.
        with tracker.activity("PlanningActivity", "CAx: scope the summary") as prov:
            prov.consumes(task)

        # S3 Act — the model call (or the computed fallback), then the payload to the store.
        with tracker.activity("ToolCallActivity", "CAx: write NL summary") as prov:
            prov.consumes(task)
            summary, mode, completion = self._summarise(doc, counts)
            pointer = self.store.put(summary, kind="summary")
            self.store.save()  # keep the shared index complete
            response = prov.produces(
                "ResponseEntity",
                summary,
                pid=pointer["yp:pid"],
                content_hash=pointer["yp:contentHash"],
                derived_from=task,
            )
            # Which prompt, which model, which sampling parameters, how many tokens. The
            # computed path stamps none of this, because there is nothing to account for —
            # an absent attribute is honest, a zero would not be.
            prov.note(**{"yp:summary_mode": mode})
            if completion is not None:
                prov.note(**completion.provenance())

        # S4 Observe — test the summary's figures against the graph that produced them.
        with tracker.activity(
            "ObservationActivity", "CAx: check the summary's figures against the graph"
        ) as prov:
            prov.consumes(response)
            _, ungrounded = self._check_grounding(summary, doc, counts)
            prov.produces(
                "QualityEntity",
                f"{len(ungrounded)} ungrounded figure(s)"
                if ungrounded
                else "no ungrounded figures",
                derived_from=response,
            )
            prov.note(
                **{
                    "yp:grounding_check": "numerals-against-graph-facts",
                    "yp:ungrounded_figure_count": len(ungrounded),
                }
            )
            quality = prov.generated

        # S4b Review — when the deployment asks the model to decide, it reviews the summary
        # against the producer's record. Recorded as its own step with the call's provenance,
        # and its answer as an entity, so the decision consumes what the model said.
        review: dict[str, Scalar] | None = None
        if verdict_by_model():
            review, quality = self._review(tracker, response, summary, doc, counts, quality)

        # S5 Decide — accept the summary, or say what is wrong with it.
        #
        # Two independent faults, so the verdict names which one applies. A truncated
        # summary is a different artifact from an ungrounded one: the first ran out of token
        # budget mid-sentence, the second asserted something the graph does not support, and
        # only the second is a faithfulness problem.
        truncated = completion is not None and completion.truncated
        evidence: dict[str, Scalar] = {
            "ungrounded_figures": len(ungrounded),
            "truncated": truncated,
        }
        code_verdict = SUMMARY_RULE.apply(evidence)["yp:verdict"]
        if review is None:
            verdict = code_verdict
            tracker.decide(
                verdict,
                self._justify(verdict, summary, mode, counts, ungrounded, truncated=truncated),
                label=f"CAx: {verdict.lower()} summary",
                consumed=quality,
                rule=SUMMARY_RULE,
                evidence=evidence,
                attributes={"yp:summary_mode": mode},
            )
        else:
            inputs: dict[str, Scalar] = {**review, "truncated": truncated}
            verdict = REVIEW_RULE.apply(inputs)["yp:verdict"]
            tracker.decide(
                verdict,
                self._justify_review(verdict, inputs, code_verdict, ungrounded),
                label=f"CAx: {verdict.lower()} summary",
                consumed=quality,
                rule=REVIEW_RULE,
                evidence=inputs,
                attributes={
                    "yp:summary_mode": mode,
                    "yp:verdict_source": "model",
                    "yp:code_verdict": code_verdict,
                },
            )

        # S6 Respond — hand the summary on.
        with tracker.activity("ResponseActivity", "CAx: respond") as prov:
            prov.consumes(response)

        return tracker.emit(), (
            f"summarised {session_id} ({mode}) -> {len(summary)} chars "
            f"[{verdict}], PID {pointer['yp:pid']}"
        )
