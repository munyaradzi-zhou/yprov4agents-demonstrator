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
"""

from __future__ import annotations

import re
from typing import Any

from yprov4agenticai import LightweightStore
from yprov4agenticai.prompts import ACT_SYSTEM

from ..base_agent import StreamingAgent
from ..llm import Completion, get_llm, llm_is_required
from ..net_bus import NetworkBus
from ..util import OUT_DIR, agent_tracker, prov_counts

#: Numerals in prose that are units or ordinary language rather than claims about the
#: graph ("3-4 sentences", "one of"). Grounding small integers against the fact set would
#: reject faithful summaries, so the check applies to figures a model could plausibly
#: invent about the data.
_UNGROUNDED_FLOOR = 10

_NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?")

#: How many activity names the facts message carries. A ceiling, not a target: the point is
#: to give the model something concrete to name, not to pass it the whole graph.
_FACT_ACTIVITIES = 6


class CAxSummariser(StreamingAgent):
    input_subject = "producer.>"
    output_prefix = "agent.lifecycle"

    def __init__(self, bus: NetworkBus) -> None:
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

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        tracker = agent_tracker(self.role, session_id, "NL_Summarizer", energy=self.energy)

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
            grounded, ungrounded = self._check_grounding(summary, doc, counts)
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

        # S5 Decide — accept the summary, or say what is wrong with it.
        #
        # Two independent faults, so the verdict names which one applies. A truncated
        # summary is a different artifact from an ungrounded one: the first ran out of token
        # budget mid-sentence, the second asserted something the graph does not support, and
        # only the second is a faithfulness problem.
        truncated = completion is not None and completion.truncated
        verdict = "ACCEPT" if grounded and not truncated else "REVISE"
        tracker.decide(
            verdict,
            self._justify(verdict, summary, mode, counts, ungrounded, truncated=truncated),
            label=f"CAx: {verdict.lower()} summary",
            consumed=quality,
            attributes={"yp:summary_mode": mode},
        )

        # S6 Respond — hand the summary on.
        with tracker.activity("ResponseActivity", "CAx: respond") as prov:
            prov.consumes(response)

        return tracker.emit(), (
            f"summarised {session_id} ({mode}) -> {len(summary)} chars "
            f"[{verdict}], PID {pointer['yp:pid']}"
        )
