"""
ca2_metrics.py — CA2: Metrics_Visualizer: renders a chart from a PROV record
(operational agent, deterministic — no LLM).

Consumes a producer's yProv4ML PROV-JSON, extracts what it can measure (graph
composition + any numeric metric entities), renders a bar chart (PNG) to the shared
output volume, and emits its own typed PROV lifecycle (reflexive), with a
ToolCallActivity that generated the chart entity (pointer-to-PID, not the bytes).

**This agent is the worked example for the capture API** (``docs/INSTRUMENTATION.md`` §3).
The other five drive the tracker through ``register_entity``/``record_activity``; this one
uses ``activity()``, ``decide()`` and ``emit()``. Two things follow, and both are checked:

* The emitted provenance is *structurally identical* either way — same typed activities,
  entities, relations, verdicts and justification text. ``eval.compare`` reports an empty
  diff against the Phase 1 archive, which is what makes the API a refactor rather than a
  change of behaviour.
* The timestamps are not identical, and are better. Each activity's start and end now
  bracket the work itself — the matplotlib call is inside the ``ToolCallActivity`` block —
  where before they were two instants sampled after the work had already finished, so
  every step was recorded as taking approximately zero milliseconds.
"""

from __future__ import annotations

from collections.abc import Sequence
from pathlib import Path
from typing import Any, NamedTuple

import matplotlib

from yprov4agenticai import LightweightStore

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import OUT_DIR, agent_tracker, prov_counts

matplotlib.use("Agg")  # headless: no display in a container
import matplotlib.pyplot as plt  # imported after the backend is selected

#: What a rendered chart depicts. Recorded on the decision because the two are materially
#: different deliverables: one shows the run's measurements, the other only how many nodes
#: its provenance graph has.
SUBJECT_METRICS = "numeric-metrics"
SUBJECT_COMPOSITION = "graph-composition"

#: Most numeric metrics a single chart will carry, and the width of a label on its axis.
_MAX_SERIES = 10
_MAX_LABEL_CHARS = 18


class ChartSpec(NamedTuple):
    """What to draw, decided before anything is drawn.

    Separating the choice from the rendering is what lets the ``PlanningActivity`` bracket
    real work: choosing which of two materially different charts to produce.
    """

    subject: str
    title: str
    labels: Sequence[str]
    values: Sequence[float]
    colors: Sequence[str]


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
                out.append((str(label)[:_MAX_LABEL_CHARS], float(v)))
            elif isinstance(v, dict) and isinstance(v.get("$"), (int, float)):
                out.append((str(label)[:_MAX_LABEL_CHARS], float(v["$"])))
        return out[:_MAX_SERIES]

    def _select_series(
        self, session_id: str, doc: dict[str, Any], counts: dict[str, int]
    ) -> ChartSpec:
        """Choose what the chart will show: the run's measurements, or failing that, the
        shape of the record describing it.

        The fallback is not a cosmetic difference. A composition chart describes the
        provenance document; a metrics chart describes the run the document is about. The
        emitted record has to say which, so the choice is made here and carried onto the
        decision as ``yp:chart_subject``.
        """
        metrics = self._numeric_metrics(doc)
        if metrics:
            labels, values = (list(axis) for axis in zip(*metrics, strict=True))
            return ChartSpec(
                subject=SUBJECT_METRICS,
                title=f"{session_id} — numeric metrics",
                labels=labels,
                values=values,
                colors=["#2a52c9"],
            )
        return ChartSpec(
            subject=SUBJECT_COMPOSITION,
            title=f"{session_id} — graph composition",
            labels=["activities", "entities", "relations"],
            values=[counts["activities"], counts["entities"], counts["relations"]],
            colors=["#2a52c9", "#3f7d68", "#b8801c"],
        )

    @staticmethod
    def _render(session_id: str, spec: ChartSpec) -> Path:
        """Draw the chosen chart to the shared output volume and return its path."""
        charts_dir = OUT_DIR / "charts"
        charts_dir.mkdir(parents=True, exist_ok=True)
        png = charts_dir / f"{session_id}_metrics.png"

        fig, ax = plt.subplots(figsize=(7, 4))
        ax.bar(spec.labels, spec.values, color=list(spec.colors))
        ax.set_title(spec.title)
        if spec.subject == SUBJECT_METRICS:
            ax.tick_params(axis="x", rotation=45)
        fig.tight_layout()
        fig.savefig(png, dpi=110)
        plt.close(fig)
        return png

    @staticmethod
    def _justify(verdict: str, spec: ChartSpec, counts: dict[str, int], png: Path) -> str:
        """State what was plotted, from what, and why that is or is not the intended chart.

        The exact rendered byte count is deliberately absent. It is evidence, not a claim:
        a PNG's size depends on the matplotlib build and the host's fonts, so quoting it
        would make this text differ between platforms for a run that did the same thing.
        It stays available on the observation as ``yp:rendered_bytes``.
        """
        if spec.subject == SUBJECT_METRICS:
            found = (
                f"Plotted {len(spec.values)} numeric metric series read from the graph's "
                f"prov:value entities"
            )
        else:
            found = (
                f"The graph carries no numeric prov:value entities, so no measurement "
                f"could be plotted; fell back to graph composition "
                f"({counts['activities']} activities / {counts['entities']} entities / "
                f"{counts['relations']} relations), which describes the record rather "
                f"than the run it documents"
            )
        closing = (
            "."
            if verdict == "ACCEPT"
            else " — the chart is valid but less informative than intended."
        )
        return (
            f"{found}. Wrote a non-empty {png.suffix.lstrip('.')} file to {png.name} with "
            f"matplotlib; deterministic, no model involved. Verdict {verdict}{closing}"
        )

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        # The sink is where the emitted document goes; giving it to the tracker keeps the
        # destination out of the lifecycle below, which is then purely about what happened.
        tracker = agent_tracker(self.role, session_id, "Metrics_Visualizer", energy=self.energy)

        # S1 Receive — what arrived, and from where.
        with tracker.activity(
            "ReceiveTaskActivity", "Metrics: receive producer record"
        ) as prov:
            counts = prov_counts(doc)
            task = prov.produces("TaskEntity", f"producer doc on {source_subject}")

        # S2 Plan — choose between two materially different charts.
        with tracker.activity("PlanningActivity", "Metrics: choose what to plot") as prov:
            prov.consumes(task)
            spec = self._select_series(session_id, doc, counts)

        # S3 Act — render it. The pointer goes in the graph; the bytes go in the store.
        with tracker.activity("ToolCallActivity", "Metrics: render chart (matplotlib)") as prov:
            prov.consumes(task)
            png = self._render(session_id, spec)
            pointer = self.store.put(png.as_posix(), kind="chart")
            self.store.save()  # keep the shared index complete
            chart = prov.produces(
                "ToolResultEntity",
                png.name,
                pid=pointer["yp:pid"],
                content_hash=pointer["yp:contentHash"],
                derived_from=task,
            )

        # S4 Observe — the chart is the deliverable, so confirm it reached the disk and is
        # not an empty file before claiming it was rendered.
        with tracker.activity(
            "ObservationActivity", "Metrics: confirm the chart reached disk"
        ) as prov:
            prov.consumes(chart)
            rendered_bytes = png.stat().st_size if png.is_file() else 0
            prov.note(**{"yp:rendered_bytes": rendered_bytes})

        # S5 Decide — the one step no observer could have written for this agent.
        verdict = (
            "ACCEPT" if rendered_bytes > 0 and spec.subject == SUBJECT_METRICS else "REVISE"
        )
        tracker.decide(
            verdict,
            self._justify(verdict, spec, counts, png),
            label=f"Metrics: {verdict.lower()} chart",
            consumed=chart,
            attributes={
                "yp:chart_subject": spec.subject,
                "yp:series_plotted": len(spec.values),
            },
        )

        # S6 Respond — hand the chart on.
        with tracker.activity("ResponseActivity", "Metrics: respond") as prov:
            prov.consumes(chart)

        return tracker.emit(), (
            f"visualised {session_id} -> {png.name} "
            f"({spec.subject}, {len(spec.values)} series) "
            f"[{verdict}], PID {pointer['yp:pid']}"
        )
