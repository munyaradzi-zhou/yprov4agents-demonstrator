"""
mc3_audit.py — MC3: the Auditability agent (meta agent, deterministic).

Consumes an OPERATIONAL agent's emitted PROV-JSON and produces a who-did-what audit
trail: which agent, which typed activities in what order, and any justifications on
record. Emits its own reflexive PROV (an AuditActivity summarising the trail).

Like MC1, it publishes to `governance.*` so it never re-triggers the meta layer.
"""

from __future__ import annotations

from typing import Any

from yprov4agenticai import ProvTracker

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import now_iso, write_prov


def _agent_label(doc: dict[str, Any]) -> str:
    for _, ag in (doc.get("agent") or {}).items():
        if ag.get("prov:label"):
            return str(ag["prov:label"])
    return "unknown-agent"


def _type_of(a: dict[str, Any]) -> str:
    t = a.get("prov:type")
    return str(t.get("$", "")) if isinstance(t, dict) else str(t or "")


class MC3Audit(StreamingAgent):
    input_subject = "agent.lifecycle.>"
    output_prefix = "governance"

    def __init__(self, bus: NetworkBus) -> None:
        super().__init__(role="mc3", bus=bus)

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        who = _agent_label(doc)
        acts = list((doc.get("activity") or {}).items())
        acts.sort(key=lambda kv: kv[1].get("prov:startTime", ""))

        trail: list[str] = []
        n_just = 0
        # An audit trail is only a trail if the steps can actually be placed in order.
        # Activities without a start time sort together under the empty string, so their
        # relative order is an artifact of dictionary insertion rather than evidence.
        unordered = [aid for aid, a in acts if not a.get("prov:startTime")]
        unterminated = [aid for aid, a in acts if not a.get("prov:endTime")]
        for _aid, a in acts:
            kind = _type_of(a).split(":")[-1] or "Activity"
            trail.append(kind)
            if a.get("yp:justification"):
                n_just += 1
        trail_str = " -> ".join(trail) if trail else "(no activities)"

        # S5 Decide — whether the trail is a complete who-did-what-when record.
        #
        # MC3 previously emitted no verdict at all, which left it failing the rule MC1
        # enforces, and left an auditor that could never report a problem. These are the
        # conditions under which a reconstructed trail is not trustworthy evidence.
        gaps: list[str] = []
        if not acts:
            gaps.append("no activities to audit")
        if unordered:
            gaps.append(
                f"{len(unordered)} activity(ies) carry no start time, so cannot be ordered"
            )
        if unterminated:
            gaps.append(
                f"{len(unterminated)} activity(ies) carry no end time, so have no duration"
            )
        verdict = "COMPLETE" if not gaps else "PARTIAL"

        # reflexive PROV of the audit
        t = ProvTracker(sid=f"{session_id}-mc3", agent_label="AuditabilityAgent")
        subj = t.register_entity("TaskEntity", f"{who} PROV under audit ({session_id})")
        t.record_activity(
            "ReceiveTaskActivity",
            "MC3: receive agent PROV",
            now_iso(),
            now_iso(),
            out_eid=subj,
            verbose=False,
        )
        report = t.register_entity(
            "QualityEntity",
            f"agent={who}; steps={len(acts)}; justifications={n_just}; trail={trail_str}",
            derived_from=subj,
        )
        t.record_activity(
            "AuditActivity",
            f"MC3: audit {who}",
            now_iso(),
            now_iso(),
            in_eid=subj,
            out_eid=report,
            extra_attrs={
                "yp:audited_agent": who,
                "yp:step_count": len(acts),
                "yp:justified_step_count": n_just,
                "yp:orderable_step_count": len(acts) - len(unordered),
            },
            verbose=False,
        )
        t.record_activity(
            "DecisionActivity",
            f"MC3: trail for {who} is {verdict.lower()}",
            now_iso(),
            now_iso(),
            in_eid=report,
            justification=(
                f"Reconstructed the lifecycle of '{who}': {len(acts)} activities "
                f"({n_just} justified) in order {trail_str}. "
                + (
                    "Every step carries a start and end time, so the who-did-what-when "
                    "trail is fully ordered and timed."
                    if verdict == "COMPLETE"
                    else "Trail is not fully reconstructible: " + "; ".join(gaps) + "."
                )
                + f" Verdict {verdict}."
            ),
            extra_attrs={"yp:verdict": verdict, "yp:audited_agent": who},
            verbose=False,
        )

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, (
            f"audited {who} [{session_id}]: {len(acts)} steps, {n_just} justified -> {verdict}"
        )
