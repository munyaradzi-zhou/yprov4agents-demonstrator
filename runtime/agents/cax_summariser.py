"""
cax_summariser.py — CAx: the NL provenance summariser (operational agent).

Consumes a producer's yProv4ML PROV-JSON, writes a natural-language summary of it,
and emits its OWN typed W3C PROV lifecycle (the Reflexive Property: the summariser
is itself provenance-tracked). The DecisionActivity carries `yp:justification` — the
project's headline field.

LLM is optional: if OPENAI_API_KEY is set, the summary is written by the model;
otherwise a deterministic stub summary is produced, so the pipeline runs with no key.
"""

from __future__ import annotations

import re
from typing import Any

from yprov4agenticai import LightweightStore, ProvTracker, ptr_kwargs

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import OUT_DIR, get_llm, llm_is_required, now_iso, prov_counts, write_prov

#: Numerals in prose that are units or ordinary language rather than claims about the
#: graph ("3-4 sentences", "one of"). Grounding small integers against the fact set would
#: reject faithful summaries, so the check applies to figures a model could plausibly
#: invent about the data.
_UNGROUNDED_FLOOR = 10

_NUMERAL = re.compile(r"\d[\d,]*(?:\.\d+)?")


class CAxSummariser(StreamingAgent):
    input_subject = "producer.>"
    output_prefix = "agent.lifecycle"

    def __init__(self, bus: NetworkBus) -> None:
        super().__init__(role="cax", bus=bus)
        self.llm = get_llm()
        self.store = LightweightStore(root=str(OUT_DIR / "store"))

    def _summarise(self, doc: dict[str, Any], c: dict[str, int]) -> tuple[str, str]:
        """Return (summary, mode), where mode records the path actually taken.

        The mode is an outcome, not a configuration: an agent that was given a model
        but whose call failed produced computed output, and must say so.
        """
        acts = list((doc.get("activity") or {}).keys())[:6]
        fallback_reason = ""
        if self.llm:
            prompt = (
                "You are CAx, a provenance summariser. In 3-4 sentences, summarise this "
                f"W3C PROV document from a machine-learning run. It has {c['activities']} "
                f"activities, {c['entities']} entities, {c['relations']} lineage relations. "
                f"Key activities: {', '.join(acts)}. Use only these facts."
            )
            try:
                return self.llm(prompt), "llm"
            except Exception as exc:
                if llm_is_required():
                    raise RuntimeError(
                        f"LLM_REQUIRED is set but the summary call failed: {exc}"
                    ) from exc
                print(f"[cax] LLM call failed ({exc}); falling back to computed", flush=True)
                fallback_reason = type(exc).__name__
        return (
            f"This ML provenance record captures {c['activities']} activities and "
            f"{c['entities']} entities linked by {c['relations']} lineage relations. "
            f"Tracked steps include {', '.join(a.split(':')[-1] for a in acts)}. "
            "The graph documents inputs, parameters and outputs of the training run."
        ), (f"computed-after-{fallback_reason}" if fallback_reason else "computed")

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

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        c = prov_counts(doc)
        summary, mode = self._summarise(doc, c)
        grounded, ungrounded = self._check_grounding(summary, doc, c)
        verdict = "ACCEPT" if grounded else "REVISE"
        ptr = self.store.put(summary, kind="summary")
        self.store.save()  # keep the shared index complete

        # emit CAx's own 6-step lifecycle as typed PROV
        t = ProvTracker(sid=session_id, agent_label="NL_Summarizer")
        t0 = now_iso()
        task = t.register_entity("TaskEntity", f"producer doc on {source_subject}")
        t.record_activity(
            "ReceiveTaskActivity",
            "CAx: receive producer record",
            t0,
            now_iso(),
            out_eid=task,
            verbose=False,
        )
        t.record_activity(
            "PlanningActivity",
            "CAx: scope the summary",
            now_iso(),
            now_iso(),
            in_eid=task,
            verbose=False,
        )
        resp = t.register_entity("ResponseEntity", summary, **ptr_kwargs(ptr))
        t.record_activity(
            "ToolCallActivity",
            "CAx: write NL summary",
            now_iso(),
            now_iso(),
            in_eid=task,
            out_eid=resp,
            verbose=False,
        )
        quality = t.register_entity(
            "QualityEntity",
            f"{len(ungrounded)} ungrounded figure(s)"
            if ungrounded
            else "no ungrounded figures",
        )
        t.record_activity(
            "ObservationActivity",
            "CAx: check the summary's figures against the graph",
            now_iso(),
            now_iso(),
            in_eid=resp,
            out_eid=quality,
            extra_attrs={
                "yp:grounding_check": "numerals-against-graph-facts",
                "yp:ungrounded_figure_count": len(ungrounded),
            },
            verbose=False,
        )
        t.record_activity(
            "DecisionActivity",
            f"CAx: {verdict.lower()} summary",
            now_iso(),
            now_iso(),
            in_eid=quality,
            justification=(
                f"Summary of {len(summary)} characters produced by the {mode} path over a "
                f"{c['activities']}-activity / {c['entities']}-entity graph. "
                + (
                    f"Checked every figure of {_UNGROUNDED_FLOOR} or more against the "
                    f"graph's own counts: all supported. This establishes no invented "
                    f"figures, not that the summary is complete."
                    if grounded
                    else f"Figure(s) {', '.join(ungrounded)} appear in the summary but not "
                    f"in the graph's counts, so the text asserts something the record "
                    f"does not support."
                )
                + f" Verdict {verdict}."
            ),
            extra_attrs={"yp:verdict": verdict, "yp:summary_mode": mode},
            verbose=False,
        )
        t.record_activity(
            "ResponseActivity", "CAx: respond", now_iso(), now_iso(), in_eid=resp, verbose=False
        )

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, (
            f"summarised {session_id} ({mode}) -> {len(summary)} chars "
            f"[{verdict}], PID {ptr['yp:pid']}"
        )
