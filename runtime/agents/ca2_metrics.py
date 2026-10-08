"""
ca2_metrics.py — CA2: Metrics_Visualizer: renders a chart from a PROV record
(operational agent, deterministic — no LLM).

Consumes a producer's yProv4ML PROV-JSON, extracts what it can measure (graph
composition + any numeric metric entities), renders a bar chart (PNG) to the shared
output volume, and emits its own typed PROV lifecycle (reflexive), with a
ToolCallActivity that generated the chart entity (pointer-to-PID, not the bytes).
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, List, Tuple

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import now_iso, prov_counts, write_prov, OUT_DIR
from yprov4agenticai import ProvTracker, LightweightStore, ptr_kwargs

import matplotlib
matplotlib.use("Agg")            # headless: no display in a container
import matplotlib.pyplot as plt  # noqa: E402


class CA2Metrics(StreamingAgent):
    input_subject = "producer.>"
    output_prefix = "agent.lifecycle"

    def __init__(self, bus: NetworkBus):
        super().__init__(role="ca2", bus=bus)
        self.store = LightweightStore(root=str(OUT_DIR / "store"))

    @staticmethod
    def _numeric_metrics(doc: Dict[str, Any]) -> List[Tuple[str, float]]:
        """Pull any numeric prov:value entities (yProv4ML logs some as typed values)."""
        out: List[Tuple[str, float]] = []
        for eid, e in (doc.get("entity") or {}).items():
            v = e.get("prov:value") or e.get("yp:value")
            label = e.get("prov:label", eid.split(":")[-1])
            if isinstance(v, (int, float)):
                out.append((str(label)[:18], float(v)))
            elif isinstance(v, dict) and isinstance(v.get("$"), (int, float)):
                out.append((str(label)[:18], float(v["$"])))
        return out[:10]

    def _render(self, session_id: str, doc: Dict[str, Any], c: Dict[str, int]) -> Path:
        charts_dir = OUT_DIR / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)
        png = charts_dir / f"{session_id}_metrics.png"

        metrics = self._numeric_metrics(doc)
        fig, ax = plt.subplots(figsize=(7, 4))
        if metrics:
            labels, vals = zip(*metrics)
            ax.bar(labels, vals, color="#2a52c9")
            ax.set_title(f"{session_id} — numeric metrics")
            ax.tick_params(axis="x", rotation=45)
        else:
            labels = ["activities", "entities", "relations"]
            vals = [c["activities"], c["entities"], c["relations"]]
            ax.bar(labels, vals, color=["#2a52c9", "#3f7d68", "#b8801c"])
            ax.set_title(f"{session_id} — graph composition")
        fig.tight_layout()
        fig.savefig(png, dpi=110)
        plt.close(fig)
        return png

    def process(self, session_id: str, doc: Dict[str, Any],
                source_subject: str) -> Tuple[Dict[str, Any], str]:
        c = prov_counts(doc)
        png = self._render(session_id, doc, c)
        ptr = self.store.put(png.as_posix(), kind="chart")   # store the pointer to the artifact

        t = ProvTracker(sid=session_id, agent_label="Metrics_Visualizer")
        t0 = now_iso()
        task = t.register_entity("TaskEntity", f"producer doc on {source_subject}")
        t.record_activity("ReceiveTaskActivity", "Metrics: receive producer record",
                          t0, now_iso(), out_eid=task, verbose=False)
        t.record_activity("PlanningActivity", "Metrics: choose chart",
                          now_iso(), now_iso(), in_eid=task, verbose=False)
        chart = t.register_entity("ToolResultEntity", png.name, **ptr_kwargs(ptr))
        t.record_activity("ToolCallActivity", "Metrics: render chart (matplotlib)",
                          now_iso(), now_iso(), in_eid=task, out_eid=chart, verbose=False)
        t.record_activity(
            "DecisionActivity", "Metrics: accept chart", now_iso(), now_iso(),
            in_eid=chart,
            justification=(f"Rendered a chart summarising {c['activities']} activities / "
                           f"{c['entities']} entities of {session_id}; deterministic, no "
                           "model involved. Accepted."),
            verbose=False)
        t.record_activity("ResponseActivity", "Metrics: respond", now_iso(), now_iso(),
                          in_eid=chart, verbose=False)

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, f"visualised {session_id} -> {png.name}, PID {ptr['yp:pid']}"
