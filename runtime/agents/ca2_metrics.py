"""
ca2_metrics.py — CA2: Metrics_Visualizer: renders a chart from a PROV record
(operational agent, deterministic — no LLM).

Consumes a producer's yProv4ML PROV-JSON, extracts what it can measure (graph
composition + any numeric metric entities), renders a bar chart (PNG) to the shared
output volume, and emits its own typed PROV lifecycle (reflexive), with a
ToolCallActivity that generated the chart entity (pointer-to-PID, not the bytes).
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any

import matplotlib

from yprov4agenticai import LightweightStore, ProvTracker, ptr_kwargs

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import OUT_DIR, now_iso, prov_counts, write_prov

matplotlib.use("Agg")  # headless: no display in a container
import matplotlib.pyplot as plt  # imported after the backend is selected

#: What a rendered chart depicts. Recorded on the decision because the two are materially
#: different deliverables: one shows the run's measurements, the other only how many nodes
#: its provenance graph has.
SUBJECT_METRICS = "numeric-metrics"
SUBJECT_COMPOSITION = "graph-composition"


class CA2Metrics(StreamingAgent):
    input_subject = "producer.>"
    output_prefix = "agent.lifecycle"

    def __init__(self, bus: NetworkBus) -> None:
        super().__init__(role="ca2", bus=bus)
        self.store = LightweightStore(root=str(OUT_DIR / "store"))

    @staticmethod
    def _numeric_metrics(doc: dict[str, Any]) -> list[tuple[str, float]]:
        """Pull any numeric prov:value entities (yProv4ML logs some as typed values)."""
        out: list[tuple[str, float]] = []
        for eid, e in (doc.get("entity") or {}).items():
            v = e.get("prov:value") or e.get("yp:value")
            label = e.get("prov:label", eid.split(":")[-1])
            if isinstance(v, (int, float)):
                out.append((str(label)[:18], float(v)))
            elif isinstance(v, dict) and isinstance(v.get("$"), (int, float)):
                out.append((str(label)[:18], float(v["$"])))
        return out[:10]

    def _render(
        self, session_id: str, doc: dict[str, Any], c: dict[str, int]
    ) -> tuple[Path, str, int]:
        """Render the chart. Returns (path, what was plotted, how many series).

        The two branches produce materially different charts — the graph's own numeric
        metrics, or a fallback showing only its composition — and the emitted record
        previously said which in neither case. A reader could not tell whether the chart
        shows the run's measurements or just how many nodes the graph has.
        """
        charts_dir = OUT_DIR / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)
        png = charts_dir / f"{session_id}_metrics.png"

        metrics = self._numeric_metrics(doc)
        labels: Sequence[str]
        values: Sequence[float]
        fig, ax = plt.subplots(figsize=(7, 4))
        if metrics:
            subject, series = SUBJECT_METRICS, len(metrics)
            labels, values = (list(axis) for axis in zip(*metrics, strict=True))
            ax.bar(labels, values, color="#2a52c9")
            ax.set_title(f"{session_id} — numeric metrics")
            ax.tick_params(axis="x", rotation=45)
        else:
            subject, series = SUBJECT_COMPOSITION, 3
            labels = ["activities", "entities", "relations"]
            values = [c["activities"], c["entities"], c["relations"]]
            ax.bar(labels, values, color=["#2a52c9", "#3f7d68", "#b8801c"])
            ax.set_title(f"{session_id} — graph composition")
        fig.tight_layout()
        fig.savefig(png, dpi=110)
        plt.close(fig)
        return png, subject, series

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        c = prov_counts(doc)
        png, subject, series = self._render(session_id, doc, c)
        ptr = self.store.put(png.as_posix(), kind="chart")  # store the pointer to the artifact
        self.store.save()  # keep the shared index complete

        # S4 Observe — the chart is the deliverable, so confirm it reached the disk and is
        # not an empty file before claiming it was rendered.
        rendered_bytes = png.stat().st_size if png.is_file() else 0
        verdict = "ACCEPT" if rendered_bytes > 0 and subject == SUBJECT_METRICS else "REVISE"

        t = ProvTracker(sid=session_id, agent_label="Metrics_Visualizer")
        t0 = now_iso()
        task = t.register_entity("TaskEntity", f"producer doc on {source_subject}")
        t.record_activity(
            "ReceiveTaskActivity",
            "Metrics: receive producer record",
            t0,
            now_iso(),
            out_eid=task,
            verbose=False,
        )
        t.record_activity(
            "PlanningActivity",
            "Metrics: choose chart",
            now_iso(),
            now_iso(),
            in_eid=task,
            verbose=False,
        )
        chart = t.register_entity(
            "ToolResultEntity", png.name, **ptr_kwargs(ptr), derived_from=task
        )
        t.record_activity(
            "ToolCallActivity",
            "Metrics: render chart (matplotlib)",
            now_iso(),
            now_iso(),
            in_eid=task,
            out_eid=chart,
            verbose=False,
        )
        t.record_activity(
            "ObservationActivity",
            "Metrics: confirm the chart reached disk",
            now_iso(),
            now_iso(),
            in_eid=chart,
            extra_attrs={"yp:rendered_bytes": rendered_bytes},
            verbose=False,
        )
        t.record_activity(
            "DecisionActivity",
            f"Metrics: {verdict.lower()} chart",
            now_iso(),
            now_iso(),
            in_eid=chart,
            justification=(
                (
                    f"Plotted {series} numeric metric series read from the graph's "
                    f"prov:value entities"
                    if subject == SUBJECT_METRICS
                    else f"The graph carries no numeric prov:value entities, so no "
                    f"measurement could be plotted; fell back to graph composition "
                    f"({c['activities']} activities / {c['entities']} entities / "
                    f"{c['relations']} relations), which describes the record rather "
                    f"than the run it documents"
                )
                # The exact byte count is evidence, not a claim: a rendered PNG's size
                # depends on the matplotlib build and the host's fonts, so quoting it in
                # prose would make the justification differ between platforms for a run
                # that did the same thing. It stays available as yp:rendered_bytes.
                + f". Wrote a non-empty {png.suffix.lstrip('.')} file to {png.name} with "
                f"matplotlib; deterministic, no model involved. Verdict {verdict}"
                + (
                    "."
                    if verdict == "ACCEPT"
                    else " — the chart is valid but less informative than intended."
                )
            ),
            extra_attrs={
                "yp:verdict": verdict,
                "yp:chart_subject": subject,
                "yp:series_plotted": series,
            },
            verbose=False,
        )
        t.record_activity(
            "ResponseActivity",
            "Metrics: respond",
            now_iso(),
            now_iso(),
            in_eid=chart,
            verbose=False,
        )

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, (
            f"visualised {session_id} -> {png.name} ({subject}, {series} series) "
            f"[{verdict}], PID {ptr['yp:pid']}"
        )
