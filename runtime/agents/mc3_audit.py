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

    def __init__(self, bus: NetworkBus):
        super().__init__(role="mc3", bus=bus)

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        who = _agent_label(doc)
        # order activities by start time when available
        acts = list((doc.get("activity") or {}).items())
        acts.sort(key=lambda kv: kv[1].get("prov:startTime", ""))
        trail: list[str] = []
        n_just = 0
        for aid, a in acts:
            kind = _type_of(a).split(":")[-1] or "Activity"
            trail.append(kind)
            if a.get("yp:justification"):
                n_just += 1
        trail_str = " -> ".join(trail) if trail else "(no activities)"

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
        )
        t.record_activity(
            "AuditActivity",
            f"MC3: audit {who}",
            now_iso(),
            now_iso(),
            in_eid=subj,
            out_eid=report,
            justification=(
                f"Reconstructed the lifecycle of '{who}': {len(acts)} activities "
                f"({n_just} justified) in order {trail_str}."
            ),
            extra_attrs={"yp:audited_agent": who, "yp:step_count": len(acts)},
            verbose=False,
        )

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, f"audited {who} [{session_id}]: {len(acts)} steps, {n_just} justified"
