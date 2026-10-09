"""
mc3_audit.py — MC3: the Auditability agent (meta agent, deterministic).

Consumes an OPERATIONAL agent's emitted PROV-JSON and produces a who-did-what audit
trail: which agent, which typed activities in what order, and any justifications on
record. Emits its own reflexive PROV (an AuditActivity summarising the trail).

Like MC1, it publishes to `governance.*` so it never re-triggers the meta layer.
"""

from __future__ import annotations

from typing import Any, NamedTuple

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import agent_tracker


def _agent_label(doc: dict[str, Any]) -> str:
    for _, ag in (doc.get("agent") or {}).items():
        if ag.get("prov:label"):
            return str(ag["prov:label"])
    return "unknown-agent"


def _type_of(a: dict[str, Any]) -> str:
    t = a.get("prov:type")
    return str(t.get("$", "")) if isinstance(t, dict) else str(t or "")


class Trail(NamedTuple):
    """A lifecycle reconstructed from someone else's record, with its own caveats.

    ``unordered`` and ``unterminated`` are the reasons a trail may not be usable as
    evidence, and are what separates a COMPLETE verdict from a PARTIAL one.
    """

    steps: list[str]
    justified: int
    unordered: list[str]
    unterminated: list[str]

    @property
    def rendered(self) -> str:
        return " -> ".join(self.steps) if self.steps else "(no activities)"

    @property
    def gaps(self) -> list[str]:
        """Why this trail is not trustworthy evidence, if it is not."""
        reasons: list[str] = []
        if not self.steps:
            reasons.append("no activities to audit")
        if self.unordered:
            reasons.append(
                f"{len(self.unordered)} activity(ies) carry no start time, so cannot be ordered"
            )
        if self.unterminated:
            reasons.append(
                f"{len(self.unterminated)} activity(ies) carry no end time, so have no duration"
            )
        return reasons


def _reconstruct(doc: dict[str, Any]) -> Trail:
    """Rebuild the audited agent's lifecycle in the order it happened.

    An audit trail is only a trail if the steps can actually be placed in order.
    Activities without a start time sort together under the empty string, so their
    relative order would be an artifact of dictionary insertion rather than evidence —
    which is why they are counted rather than quietly sorted.
    """
    activities = sorted(
        (doc.get("activity") or {}).items(), key=lambda kv: kv[1].get("prov:startTime", "")
    )
    return Trail(
        steps=[_type_of(a).split(":")[-1] or "Activity" for _, a in activities],
        justified=sum(1 for _, a in activities if a.get("yp:justification")),
        unordered=[aid for aid, a in activities if not a.get("prov:startTime")],
        unterminated=[aid for aid, a in activities if not a.get("prov:endTime")],
    )


class MC3Audit(StreamingAgent):
    agent_label = "AuditabilityAgent"
    input_subject = "agent.lifecycle.>"
    output_prefix = "governance"

    def __init__(self, bus: NetworkBus) -> None:
        super().__init__(role="mc3", bus=bus)

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        who = _agent_label(doc)

        # The audit is itself provenance-tracked: an auditor that leaves no record of how
        # it reached its conclusion is asking for the trust it exists to establish.
        tracker = agent_tracker(
            self.role,
            session_id,
            self.agent_label,
            sid=f"{session_id}-mc3",
            energy=self.energy,
        )

        # S1 Receive — whose record is under audit.
        with tracker.activity("ReceiveTaskActivity", "MC3: receive agent PROV") as prov:
            subject = prov.produces("TaskEntity", f"{who} PROV under audit ({session_id})")

        # S3 Act — reconstruct the lifecycle from the record alone.
        with tracker.activity("AuditActivity", f"MC3: audit {who}") as prov:
            prov.consumes(subject)
            trail = _reconstruct(doc)
            report = prov.produces(
                "QualityEntity",
                f"agent={who}; steps={len(trail.steps)}; "
                f"justifications={trail.justified}; trail={trail.rendered}",
                derived_from=subject,
            )
            prov.note(
                **{
                    "yp:audited_agent": who,
                    "yp:step_count": len(trail.steps),
                    "yp:justified_step_count": trail.justified,
                    "yp:orderable_step_count": len(trail.steps) - len(trail.unordered),
                }
            )

        # S5 Decide — whether the trail is a complete who-did-what-when record.
        #
        # MC3 previously emitted no verdict at all, which left it failing the rule MC1
        # enforces, and left an auditor that could never report a problem. The conditions
        # under which a reconstructed trail is not trustworthy evidence are in Trail.gaps.
        gaps = trail.gaps
        verdict = "COMPLETE" if not gaps else "PARTIAL"
        if verdict == "COMPLETE":
            finding = (
                "Every step carries a start and end time, so the who-did-what-when trail "
                "is fully ordered and timed."
            )
        else:
            finding = "Trail is not fully reconstructible: " + "; ".join(gaps) + "."
        tracker.decide(
            verdict,
            f"Reconstructed the lifecycle of '{who}': {len(trail.steps)} activities "
            f"({trail.justified} justified) in order {trail.rendered}. "
            f"{finding} Verdict {verdict}.",
            label=f"MC3: trail for {who} is {verdict.lower()}",
            consumed=report,
            attributes={"yp:audited_agent": who},
        )

        return tracker.emit(), (
            f"audited {who} [{session_id}]: {len(trail.steps)} steps, "
            f"{trail.justified} justified -> {verdict}"
        )
