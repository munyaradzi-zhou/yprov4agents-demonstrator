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
    A5  anomaly     — ordering-gap outliers against the run's own distribution

Everything is derived from PROV itself (types, roles, timestamps, relations), so the agent
is domain-agnostic by construction — the property Sandro asked for. The LLM, when a key is
present, only *narrates* the already-computed findings; it never produces the numbers.

Completeness gaps — unclosed activities, partial typing, absent instrumentation — are
reported separately from anomalies. They describe what the producer recorded rather
than how the run behaved, and folding them into the anomaly set made ANOMALY the
verdict for essentially every real graph, which left the verdict carrying no
information.

Decide★ verdicts: OK (no invariant breached) / ANOMALY (records the breached invariant).
"""

from __future__ import annotations

import re
import statistics
from datetime import UTC, datetime
from typing import Any

from yprov4agenticai import LightweightStore
from yprov4agenticai.prompts import NARRATE_FINDINGS

from ..base_agent import StreamingAgent
from ..llm import Completion, get_llm, llm_is_required
from ..net_bus import NetworkBus
from ..util import OUT_DIR, agent_tracker

# Metric-family classifiers, matched against entity labels rather than a hard-coded
# schema so the agent stays domain-agnostic. The families must partition: a label like
# "gpu_power_usage" matches both, so energy takes precedence and resource excludes it.
_ENERGY_PAT = re.compile(r"emission|energy|carbon|power", re.I)
_RESOURCE_PAT = re.compile(r"cpu|gpu|ram|memory|disk|temperature|usage|util", re.I)

#: Outlier threshold in median-absolute-deviation units. 3.5 is the conventional
#: equivalent of a 3-sigma rule for a normal distribution, and unlike sigma it remains
#: attainable on the handful of gaps an agent lifecycle produces.
_MAD_THRESHOLD = 3.5

#: Below this many gaps there is no distribution to be an outlier against.
_MIN_GAPS_FOR_OUTLIERS = 5

#: A flagged gap must also account for at least this share of the run's wall clock, and be
#: at least this many seconds. Producers log setup activities within the same millisecond,
#: so the median gap is often near zero; without a materiality floor the normalised score
#: explodes and sub-second noise is reported beside a genuine multi-second stall.
_MATERIAL_SHARE = 0.05
_MIN_MATERIAL_GAP_S = 0.5

#: The mining algorithms this agent applies, in the order the module docstring lists them.
#: Named once so the figure in the justification and the ``yp:algorithms`` attribute on the
#: planning step cannot disagree with each other or with the code that runs.
_ALGORITHMS = ("structure", "timing", "sustainability", "resource", "anomaly")

#: How many findings a justification quotes before summarising the remainder. A
#: justification is an explanation, not a dump of the finding list.
_JUSTIFICATION_EXAMPLES = 3


def _clean_ts(value: Any) -> datetime | None:
    """Parse a PROV timestamp, tolerating yProv4ML's '...^^xsd:dateTime' suffix."""
    if isinstance(value, dict):
        value = value.get("$")
    if not isinstance(value, str):
        return None
    text = value.split("^^")[0].strip().replace("Z", "+00:00")
    try:
        parsed = datetime.fromisoformat(text)
    except ValueError:
        return None
    # Sources mix offset-aware and naive timestamps, so normalise to one scale before
    # returning a naive value for arithmetic. Discarding the offset instead of converting
    # it -- as this did previously -- makes a +02:00 timestamp compare equal to a Z
    # timestamp two hours earlier, injecting phantom gaps into the very series the timing
    # and outlier algorithms analyse.
    if parsed.tzinfo is not None:
        parsed = parsed.astimezone(UTC)
    return parsed.replace(tzinfo=None)


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
    def _streams(
        doc: dict[str, Any], pattern: re.Pattern, exclude: re.Pattern | None = None
    ) -> dict[str, Any]:
        """Inventory the metric streams whose label matches `pattern` but not `exclude`.

        Classification is by label rather than by schema, which keeps the agent
        domain-agnostic. `exclude` makes the families a partition: without it a label like
        ``gpu_power_usage`` matches both the energy pattern (on "power") and the resource
        pattern (on "usage"), so the same stream was counted twice and the two totals
        could not be read as independent.
        """
        by_source: dict[str, int] = {}
        by_context: dict[str, int] = {}
        names: list[str] = []
        for eid, e in (doc.get("entity") or {}).items():
            label = str(e.get("prov:label", eid))
            if not pattern.search(label):
                continue
            if exclude is not None and exclude.search(label):
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
    def _timing_outliers(timing: dict[str, Any]) -> list[str]:
        """Flag ordering gaps that are outliers against the rest of the run.

        Uses the median and the median absolute deviation rather than mean and standard
        deviation. With a population standard deviation over *n* samples the largest
        attainable z-score is (n-1)/sqrt(n), so a 3-sigma rule needs at least 11 gaps
        before it can fire at all — and an agent lifecycle has six activities while the
        bundled yProv4ML records have nine. The previous rule was unreachable on every
        input in the repository, which is the worst kind of check: one that always passes.

        MAD is also robust to the outlier itself, which a standard deviation is not: one
        large gap inflates sigma enough to hide its own significance.
        """
        gaps = [g for _, g in timing["gaps"]]
        if len(gaps) < _MIN_GAPS_FOR_OUTLIERS:
            return []
        median = statistics.median(gaps)
        deviations = [abs(g - median) for g in gaps]

        # Scale estimate. The median absolute deviation is the robust default, but it is
        # exactly zero whenever more than half the gaps are identical — which is the
        # common case here, since a producer logs most of its activities at a fixed
        # cadence and then stalls once. A zero scale would reject the very outlier the
        # check exists to find, so fall back to the mean absolute deviation, which is less
        # robust but non-zero as soon as any gap differs at all.
        scale = statistics.median(deviations)
        if scale <= 0:
            scale = statistics.fmean(deviations)
        if scale <= 0:
            # Every gap is identical: nothing is unusual relative to anything else.
            return []

        # Statistical unusualness alone is not enough to report. Producers log their setup
        # activities within the same millisecond, so the median gap is often near zero and
        # the MAD with it — which makes the normalised score explode and flags a 0.098s
        # gap at 11 MAD alongside a genuine 103s stall. A finding has to be both unusual
        # for this run and large enough to matter in it.
        wall_clock = float(timing["wall_clock_s"])
        material_floor = max(_MIN_MATERIAL_GAP_S, _MATERIAL_SHARE * wall_clock)

        found = []
        for aid, gap in timing["gaps"]:
            if gap <= median or gap < material_floor:
                continue
            if abs(gap - median) / scale <= _MAD_THRESHOLD:
                continue
            share = 100.0 * gap / wall_clock if wall_clock > 0 else 0.0
            found.append(
                f"timing: gap before '{aid}' is {gap:.3f}s — {share:.1f}% of the run's "
                f"{wall_clock:.3f}s wall clock, against a median inter-activity gap of "
                f"{median:.3f}s"
            )
        return found

    @classmethod
    def _anomalies(cls, timing: dict[str, Any]) -> list[str]:
        """Invariant breaches: something in this run looks wrong.

        Deliberately narrow. Absent instrumentation and partial typing are facts about how
        the producer was configured, not misbehaviour by the run, and reporting them here
        made ANOMALY the verdict for essentially every real graph -- which left the verdict
        carrying no information. Those are reported as completeness findings instead.
        """
        return cls._timing_outliers(timing)

    @staticmethod
    def _completeness(
        struct: dict[str, Any],
        timing: dict[str, Any],
        energy: dict[str, Any],
        resource: dict[str, Any],
    ) -> list[str]:
        """Gaps in what the producer recorded, as distinct from anomalies in what it did.

        These are the findings that make the analytics agent useful on real input -- the
        bundled yProv4ML records trip the first two -- but they describe the provenance,
        not the computation it documents.
        """
        found: list[str] = []
        if timing["activities_without_end_time"]:
            n = len(timing["activities_without_end_time"])
            shown = ", ".join(timing["activities_without_end_time"][:3])
            found.append(
                f"timing-completeness: {n} activity(ies) declare a start time but no end "
                f"time ({shown}{'...' if n > 3 else ''}), so their durations are unknown"
            )
        if struct["entities"] and struct["typing_coverage"] < 100.0:
            untyped = struct["entities"] - struct["typed_entities"]
            found.append(
                f"typing-completeness: {untyped} of {struct['entities']} entities carry no "
                f"prov:type ({struct['typing_coverage']}% typed)"
            )
        if energy["count"] == 0:
            found.append(
                "sustainability-completeness: no energy or emission streams — this run is "
                "not carbon-instrumented"
            )
        if resource["count"] == 0:
            found.append(
                "resource-completeness: no CPU/GPU/RAM streams — this run is not "
                "resource-instrumented"
            )
        return found

    # ── report assembly ────────────────────────────────────────────────────────
    def _narrate(
        self,
        sid: str,
        findings: dict[str, Any],
        anomalies: list[str],
        completeness: list[str],
    ) -> tuple[str, str, Completion | None]:
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
            f"Anomalies: {len(anomalies)}; completeness findings: {len(completeness)}."
        )
        if not self.llm:
            return head, "computed", None
        try:
            completion = self.llm.complete(
                NARRATE_FINDINGS,
                f"Findings: {findings}\n\n"
                f"Anomalies: {anomalies}\n\n"
                f"Completeness gaps: {completeness}",
            )
        except Exception as exc:
            if llm_is_required():
                raise RuntimeError(f"LLM_REQUIRED is set but narration failed: {exc}") from exc
            print(f"[ca3] LLM narration failed ({exc}); using computed report", flush=True)
            return head, f"computed-after-{type(exc).__name__}", None
        return completion.text, "llm", completion

    def _justify(
        self,
        verdict: str,
        struct: dict[str, Any],
        timing: dict[str, Any],
        anomalies: list[str],
        completeness: list[str],
    ) -> str:
        """State which algorithms ran, what they found, and what the verdict rests on.

        The two halves are reported separately on purpose. An anomaly is a claim about how
        the run behaved; a completeness gap is a claim about what the producer recorded.
        Folding them together made ANOMALY the verdict for every real graph, which left
        the verdict carrying no information.
        """
        if anomalies:
            found = (
                "Breached invariants: "
                + "; ".join(anomalies[:_JUSTIFICATION_EXAMPLES])
                + (
                    f" (+{len(anomalies) - _JUSTIFICATION_EXAMPLES} more)"
                    if len(anomalies) > _JUSTIFICATION_EXAMPLES
                    else ""
                )
            )
        elif len(timing["gaps"]) >= _MIN_GAPS_FOR_OUTLIERS:
            found = (
                f"No invariant breached: no ordering gap exceeds {_MAD_THRESHOLD:g} MAD "
                f"above the median"
            )
        else:
            # An outlier test over too few samples cannot fire, so it has to say it
            # abstained rather than report a clean result it was never able to reach.
            found = (
                f"No invariant breached: only {len(timing['gaps'])} ordering gap(s), below "
                f"the {_MIN_GAPS_FOR_OUTLIERS} needed to identify an outlier, so the "
                f"timing check abstained"
            )
        if completeness:
            gaps = (
                f" Separately, {len(completeness)} completeness gap(s) in what the "
                f"producer recorded: " + "; ".join(completeness[:_JUSTIFICATION_EXAMPLES]) + "."
            )
        else:
            gaps = (
                " No completeness gaps: every activity is closed, typing is complete, and "
                "energy and resource streams are present."
            )
        return (
            f"Applied {len(_ALGORITHMS)} algorithms ({', '.join(_ALGORITHMS)}) to a "
            f"{struct['activities']}-activity / {struct['entities']}-entity graph. "
            f"{found}{gaps} Verdict {verdict}."
        )

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        tracker = agent_tracker(self.role, session_id, "Analytics_Agent", energy=self.energy)

        # S1 Receive — the graph to mine, and where it came from.
        with tracker.activity("ReceiveTaskActivity", "CA3: receive provenance graph") as prov:
            task = prov.produces("TaskEntity", f"provenance graph from {source_subject}")

        # S2 Plan — which algorithms to apply. Fixed in this agent, so it costs nothing;
        # the record names the set so a reader knows what was and was not looked for.
        with tracker.activity("PlanningActivity", "CA3: select analytics algorithms") as prov:
            prov.consumes(task)
            prov.note(**{"yp:algorithms": ",".join(_ALGORITHMS)})

        # S3 Analyse — the algorithm set, then the narration of what it found. This block
        # is where CA3's time actually goes, and the record now shows that.
        with tracker.activity("AnalysisActivity", "CA3: mine the provenance graph") as prov:
            prov.consumes(task)
            struct = self._structure(doc)
            timing = self._timing(doc)
            energy = self._streams(doc, _ENERGY_PAT)
            resource = self._streams(doc, _RESOURCE_PAT, exclude=_ENERGY_PAT)
            anomalies = self._anomalies(timing)
            completeness = self._completeness(struct, timing, energy, resource)
            findings = {
                "structure": struct,
                "timing": timing,
                "sustainability": energy,
                "resource": resource,
            }
            report, mode, completion = self._narrate(
                session_id, findings, anomalies, completeness
            )
            pointer = self.store.put(report, kind="analytics-report")
            self.store.save()  # keep the shared index complete
            result = prov.produces(
                "ToolResultEntity",
                report,
                pid=pointer["yp:pid"],
                content_hash=pointer["yp:contentHash"],
                derived_from=task,
            )
            prov.note(
                **{
                    "yp:activities_analysed": struct["activities"],
                    "yp:entities_analysed": struct["entities"],
                    "yp:energy_streams": energy["count"],
                    "yp:resource_streams": resource["count"],
                    "yp:wall_clock_s": timing["wall_clock_s"],
                    "yp:report_mode": mode,
                }
            )
            # The numbers are computed; the model only narrates them. Recording which model,
            # under which prompt version, is what lets a reader tell the two apart later.
            if completion is not None:
                prov.note(**completion.provenance())

        # S4 Observe — summarise what the invariant checks concluded.
        with tracker.activity(
            "ObservationActivity", "CA3: validate findings against invariants"
        ) as prov:
            prov.consumes(result)
            quality = prov.produces(
                "QualityEntity",
                f"{len(anomalies)} invariant(s) breached, "
                f"{len(completeness)} completeness gap(s)"
                if (anomalies or completeness)
                else "all invariants held, no completeness gaps",
                derived_from=result,
            )

        # S5 Decide — OK, or name the invariant that broke.
        verdict = "ANOMALY" if anomalies else "OK"
        tracker.decide(
            verdict,
            self._justify(verdict, struct, timing, anomalies, completeness),
            label=f"CA3: {verdict}",
            consumed=quality,
            attributes={
                "yp:anomaly_count": len(anomalies),
                "yp:completeness_gap_count": len(completeness),
            },
        )

        # S6 Respond — publish the report.
        with tracker.activity("ResponseActivity", "CA3: publish analytics report") as prov:
            prov.consumes(result)

        return tracker.emit(), (
            f"analysed {session_id} ({mode}) -> {verdict}, "
            f"{len(anomalies)} anomaly(ies), {len(completeness)} completeness gap(s), "
            f"PID {pointer['yp:pid']}"
        )
