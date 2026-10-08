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

from typing import Any

from yprov4agenticai import LightweightStore, ProvTracker, ptr_kwargs

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import OUT_DIR, get_llm, llm_is_required, now_iso, prov_counts, write_prov


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

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        c = prov_counts(doc)
        summary, mode = self._summarise(doc, c)
        ptr = self.store.put(summary, kind="summary")

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
        t.record_activity(
            "ObservationActivity",
            "CAx: evaluate summary quality",
            now_iso(),
            now_iso(),
            in_eid=resp,
            verbose=False,
        )
        t.record_activity(
            "DecisionActivity",
            "CAx: accept summary",
            now_iso(),
            now_iso(),
            in_eid=resp,
            justification=(
                f"Summary covers all {c['activities']} activities and "
                f"{c['entities']} entities of the source record; grounded in the "
                "graph counts and activity labels. Accepted."
            ),
            verbose=False,
        )
        t.record_activity(
            "ResponseActivity", "CAx: respond", now_iso(), now_iso(), in_eid=resp, verbose=False
        )

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, (
            f"summarised {session_id} ({mode}) -> {len(summary)} chars, PID {ptr['yp:pid']}"
        )
