"""
ca3_analytics.py — CA3: the Analytics agent (operational agent, heuristic + optional LLM).

Generalised on Sandro's 2026-10-05 steer: *"rather than going on the metrics, it could be
something on the structure ... I would start with agents that could be used in any provenance
graph ... anomaly detection is just one analytics task."*

So this is **not** an anomaly detector. It is an analytics agent that applies a *set of
mining algorithms* to any W3C PROV document — yProv4ML, yProv4WFs, or an agent's own
lifecycle record — and anomaly detection is one algorithm inside it:

    A1  structure   — composition, typing coverage, dangling/orphan nodes, derivation depth
    A2  timing      — activity span, ordering gaps, bottleneck, critical path (wasInformedBy)
    A3  sustainability — energy/emission metric streams by source and context
    A4  resource    — CPU/GPU/RAM/disk/thermal streams by source and context
    A5  anomaly     — invariant checks over A1–A4 (3σ gap, missing end times, empty streams)

Everything is derived from PROV itself (types, roles, timestamps, relations), so the agent
is domain-agnostic by construction — the property Sandro asked for. The LLM, when a key is
present, only *narrates* the already-computed findings; it never produces the numbers.

Decide★ verdicts: OK (no invariant breached) / ANOMALY (records the breached invariant).
"""

from __future__ import annotations

import re
import statistics
from datetime import datetime
from typing import Any

from yprov4agenticai import LightweightStore, ProvTracker, ptr_kwargs

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import OUT_DIR, get_llm, llm_is_required, now_iso, write_prov

# metric-family classifiers — matched against entity labels/ids, not hard-coded schemas
_ENERGY_PAT = re.compile(r"emission|energy|carbon|power", re.I)
_RESOURCE_PAT = re.compile(r"cpu|gpu|ram|memory|disk|temperature|usage|util", re.I)
_SIGMA = 3.0  # anomaly threshold for timing gaps


def _clean_ts(value: Any) -> datetime | None:
    """Parse a PROV timestamp, tolerating yProv4ML's '...^^xsd:dateTime' suffix."""
    if isinstance(value, dict):
        value = value.get("$")
    if not isinstance(value, str):
        return None
    text = value.split("^^")[0].strip().replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError:
        return None
    return dt.replace(tzinfo=None)  # compare naively; sources mix tz-awareness


def _type_of(node: dict[str, Any]) -> str:
    t = node.get("prov:type")
    if isinstance(t, dict):
        return str(t.get("$", ""))
    return str(t or "")


class CA3Analytics(StreamingAgent):
    input_subject = "producer.>"
    output_prefix = "agent.lifecycle"

    def __init__(self, bus: NetworkBus) -> None:
        super().__init__(role="ca3", bus=bus)
        self.llm = get_llm()
        self.store = LightweightStore(root=str(OUT_DIR / "store"))

    # ── A1 structure ───────────────────────────────────────────────────────────
    @staticmethod
    def _structure(doc: dict[str, Any]) -> dict[str, Any]:
        acts, ents = doc.get("activity") or {}, doc.get("entity") or {}
        typed_a = sum(1 for a in acts.values() if _type_of(a))
        typed_e = sum(1 for e in ents.values() if _type_of(e))
        kinds: dict[str, int] = {}
        for e in ents.values():
            kinds[_type_of(e) or "(untyped)"] = kinds.get(_type_of(e) or "(untyped)", 0) + 1
        roles: dict[str, int] = {}
        for e in ents.values():
            r = str(e.get("prov:role", "") or "(none)")
            roles[r] = roles.get(r, 0) + 1
        # entities generated but never used downstream = graph leaves
        used_ids = {v.get("prov:entity") for v in (doc.get("used") or {}).values()}
        gen_ids = {v.get("prov:entity") for v in (doc.get("wasGeneratedBy") or {}).values()}
        return {
            "activities": len(acts),
            "entities": len(ents),
            "typed_activities": typed_a,
            "typed_entities": typed_e,
            "typing_coverage": round(100.0 * typed_e / len(ents), 1) if ents else 0.0,
            "entity_types": dict(sorted(kinds.items(), key=lambda kv: -kv[1])[:6]),
            "roles": roles,
            "terminal_entities": len(gen_ids - used_ids),
            "derivations": len(doc.get("wasDerivedFrom") or {}),
        }

    # ── A2 timing ──────────────────────────────────────────────────────────────
    @staticmethod
    def _timing(doc: dict[str, Any]) -> dict[str, Any]:
        acts = doc.get("activity") or {}
        starts: list[tuple[str, datetime]] = []
        spans: list[tuple[str, float]] = []
        no_end = []
        for aid, a in acts.items():
            t0 = _clean_ts(a.get("prov:startedAtTime") or a.get("prov:startTime"))
            t1 = _clean_ts(a.get("prov:endedAtTime") or a.get("prov:endTime"))
            if t0:
                starts.append((aid, t0))
            if t0 and t1:
                spans.append((aid, (t1 - t0).total_seconds()))
            elif t0 and not t1:
                no_end.append(aid)
        starts.sort(key=lambda kv: kv[1])
        gaps = [
            (starts[i][0], (starts[i][1] - starts[i - 1][1]).total_seconds())
            for i in range(1, len(starts))
        ]
        wall = (starts[-1][1] - starts[0][1]).total_seconds() if len(starts) > 1 else 0.0
        bottleneck = max(gaps, key=lambda kv: kv[1]) if gaps else None
        longest = max(spans, key=lambda kv: kv[1]) if spans else None
        return {
            "ordered_activities": len(starts),
            "wall_clock_s": round(wall, 3),
            "measured_spans": len(spans),
            "longest_activity": (
                {"id": longest[0], "seconds": round(longest[1], 3)} if longest else None
            ),
            "bottleneck_gap": (
                {"after": bottleneck[0], "seconds": round(bottleneck[1], 3)}
                if bottleneck
                else None
            ),
            "activities_without_end_time": no_end,
            "gaps": gaps,
            # critical path proxy: the informed-by chain length
            "chain_length": len(doc.get("wasInformedBy") or {}),
        }

    # ── A3 + A4 metric-stream inventories ──────────────────────────────────────
    @staticmethod
    def _streams(doc: dict[str, Any], pattern: re.Pattern) -> dict[str, Any]:
        by_source: dict[str, int] = {}
        by_context: dict[str, int] = {}
        names: list[str] = []
        for eid, e in (doc.get("entity") or {}).items():
            label = str(e.get("prov:label", eid))
            if _type_of(e).endswith("Metric") or pattern.search(label):
                if not pattern.search(label):
                    continue
                names.append(label)
                src = str(e.get("yprov:source", "unknown"))
                ctx = str(e.get("yprov:context", "unknown"))
                by_source[src] = by_source.get(src, 0) + 1
                by_context[ctx] = by_context.get(ctx, 0) + 1
        return {
            "count": len(names),
            "by_source": by_source,
            "by_context": by_context,
            "examples": sorted(names)[:6],
        }

    # ── A5 anomaly detection — ONE algorithm among the set ─────────────────────
    @staticmethod
    def _anomalies(
        struct: dict[str, Any],
        timing: dict[str, Any],
        energy: dict[str, Any],
        resource: dict[str, Any],
    ) -> list[str]:
        found: list[str] = []
        gaps = [g for _, g in timing["gaps"]]
        if len(gaps) >= 3:
            mu, sd = statistics.mean(gaps), statistics.pstdev(gaps)
            if sd > 0:
                for aid, g in timing["gaps"]:
                    if g > mu + _SIGMA * sd:
                        found.append(
                            f"timing: gap before '{aid}' is {g:.3f}s "
                            f"(> mean {mu:.3f}s + {_SIGMA:g}σ {sd:.3f}s)"
                        )
        if timing["activities_without_end_time"]:
            n = len(timing["activities_without_end_time"])
            found.append(
                f"completeness: {n} activity(ies) declare prov:startedAtTime but no end time "
                f"({', '.join(timing['activities_without_end_time'][:3])}"
                f"{'...' if n > 3 else ''})"
            )
        if struct["typing_coverage"] < 100.0:
            found.append(
                f"typing: only {struct['typing_coverage']}% of entities carry a prov:type"
            )
        if energy["count"] == 0:
            found.append(
                "sustainability: no energy/emission streams — run is not carbon-instrumented"
            )
        if resource["count"] == 0:
            found.append("resource: no CPU/GPU/RAM streams — run is not resource-instrumented")
        return found

    # ── report assembly ────────────────────────────────────────────────────────
    def _narrate(
        self, sid: str, findings: dict[str, Any], anomalies: list[str]
    ) -> tuple[str, str]:
        """Return (report, mode), where mode records the path actually taken.

        The numbers are computed either way; the model only narrates them, so a failed
        call degrades presentation rather than correctness. It still has to be recorded:
        a run reported as model-narrated must actually have been narrated.
        """
        s, t = findings["structure"], findings["timing"]
        head = (
            f"Provenance analytics for '{sid}': {s['activities']} activities and "
            f"{s['entities']} entities ({s['typing_coverage']}% typed), "
            f"{s['derivations']} derivations, {s['terminal_entities']} terminal entities. "
            f"Wall-clock span {t['wall_clock_s']}s across {t['ordered_activities']} ordered "
            f"activities. Energy streams: {findings['sustainability']['count']}; "
            f"resource streams: {findings['resource']['count']}. "
            f"Anomalies: {len(anomalies)}."
        )
        if not self.llm:
            return head, "computed"
        try:
            narration = self.llm(
                "You are CA3, a provenance analytics agent. Write 3-4 sentences of "
                "root-cause commentary on these COMPUTED findings. Do not invent numbers; "
                "use only what is given.\n\n"
                f"Findings: {findings}\n\nAnomalies: {anomalies}"
            )
        except Exception as exc:
            if llm_is_required():
                raise RuntimeError(f"LLM_REQUIRED is set but narration failed: {exc}") from exc
            print(f"[ca3] LLM narration failed ({exc}); using computed report", flush=True)
            return head, f"computed-after-{type(exc).__name__}"
        return narration, "llm"

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        # ── run the algorithm set ─────────────────────────────────────────────
        struct = self._structure(doc)
        timing = self._timing(doc)
        energy = self._streams(doc, _ENERGY_PAT)
        resource = self._streams(doc, _RESOURCE_PAT)
        anomalies = self._anomalies(struct, timing, energy, resource)
        findings = {
            "structure": struct,
            "timing": {k: v for k, v in timing.items() if k != "gaps"},
            "sustainability": energy,
            "resource": resource,
        }
        report, mode = self._narrate(session_id, {**findings, "timing": timing}, anomalies)
        verdict = "ANOMALY" if anomalies else "OK"
        ptr = self.store.put(report, kind="analytics-report")
        self.store.save()  # keep the shared index complete

        # ── reflexive PROV: CA3's own 6-step lifecycle ────────────────────────
        t = ProvTracker(sid=session_id, agent_label="Analytics_Agent")
        t0 = now_iso()
        task = t.register_entity("TaskEntity", f"provenance graph from {source_subject}")
        t.record_activity(
            "ReceiveTaskActivity",
            "CA3: receive provenance graph",
            t0,
            now_iso(),
            out_eid=task,
            verbose=False,
        )
        t.record_activity(
            "PlanningActivity",
            "CA3: select analytics algorithms",
            now_iso(),
            now_iso(),
            in_eid=task,
            extra_attrs={"yp:algorithms": "structure,timing,sustainability,resource,anomaly"},
            verbose=False,
        )
        result = t.register_entity("ToolResultEntity", report, **ptr_kwargs(ptr))
        t.record_activity(
            "AnalysisActivity",
            "CA3: mine the provenance graph",
            now_iso(),
            now_iso(),
            in_eid=task,
            out_eid=result,
            extra_attrs={
                "yp:activities_analysed": struct["activities"],
                "yp:entities_analysed": struct["entities"],
                "yp:energy_streams": energy["count"],
                "yp:resource_streams": resource["count"],
                "yp:wall_clock_s": timing["wall_clock_s"],
            },
            verbose=False,
        )
        quality = t.register_entity(
            "QualityEntity",
            f"{len(anomalies)} invariant(s) breached" if anomalies else "all invariants held",
        )
        t.record_activity(
            "ObservationActivity",
            "CA3: validate findings against invariants",
            now_iso(),
            now_iso(),
            in_eid=result,
            out_eid=quality,
            verbose=False,
        )
        t.record_activity(
            "DecisionActivity",
            f"CA3: {verdict}",
            now_iso(),
            now_iso(),
            in_eid=quality,
            justification=(
                f"Applied 5 algorithms (structure, timing, sustainability, resource, anomaly) "
                f"to a {struct['activities']}-activity / {struct['entities']}-entity graph. "
                + (
                    "Breached invariants: "
                    + "; ".join(anomalies[:3])
                    + (f" (+{len(anomalies) - 3} more)" if len(anomalies) > 3 else "")
                    if anomalies
                    else "No invariant breached: timing gaps within 3σ, all activities closed, "
                    "typing complete, energy and resource streams present."
                )
                + f" Verdict {verdict}."
            ),
            extra_attrs={"yp:verdict": verdict, "yp:anomaly_count": len(anomalies)},
            verbose=False,
        )
        t.record_activity(
            "ResponseActivity",
            "CA3: publish analytics report",
            now_iso(),
            now_iso(),
            in_eid=result,
            verbose=False,
        )

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, (
            f"analysed {session_id} ({mode}) -> {verdict}, "
            f"{len(anomalies)} anomaly(ies), PID {ptr['yp:pid']}"
        )
