"""
mc1_compliance.py — MC1: the Compliance checker (meta agent, deterministic).

Consumes an OPERATIONAL agent's emitted PROV-JSON (from agent.lifecycle.*) and checks
it against the project's structural standard: is it typed W3C PROV, does it record a
DecisionActivity, and does that decision carry the required `yp:justification`? Emits
a PASS/FAIL verdict AND its own reflexive PROV (the checker is itself auditable).

Publishes to `governance.*` (a different prefix from what it consumes) so meta output
never re-triggers the meta layer.
"""
from __future__ import annotations

from typing import Any, Dict, List, Tuple

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import now_iso, write_prov
from yprov4agenticai import ProvTracker


def _iter_activities(doc: Dict[str, Any]):
    for aid, a in (doc.get("activity") or {}).items():
        yield aid, a


def _type_of(a: Dict[str, Any]) -> str:
    t = a.get("prov:type")
    if isinstance(t, dict):
        return str(t.get("$", ""))
    return str(t or "")


class MC1Compliance(StreamingAgent):
    input_subject = "agent.lifecycle.>"
    output_prefix = "governance"

    def __init__(self, bus: NetworkBus):
        super().__init__(role="mc1", bus=bus)

    def process(self, session_id: str, doc: Dict[str, Any],
                source_subject: str) -> Tuple[Dict[str, Any], str]:
        src_role = source_subject.split(".")[2] if source_subject.count(".") >= 2 else "?"
        failures: List[str] = []

        prefix = doc.get("prefix", {})
        if "prov" not in prefix or "yp" not in prefix:
            failures.append("prefix missing prov/yp declarations")
        if not doc.get("activity"):
            failures.append("no typed activities")

        decisions = [(aid, a) for aid, a in _iter_activities(doc)
                     if _type_of(a) == "yp:DecisionActivity"]
        if not decisions:
            failures.append("no yp:DecisionActivity present")
        else:
            missing = [aid for aid, a in decisions if not a.get("yp:justification")]
            if missing:
                failures.append(f"{len(missing)} decision(s) without yp:justification")

        verdict = "PASS" if not failures else "FAIL"
        reason = ("all structural checks passed" if verdict == "PASS"
                  else "; ".join(failures))

        # reflexive PROV of the compliance check itself
        t = ProvTracker(sid=f"{session_id}-mc1", agent_label="ComplianceChecker")
        subj = t.register_entity("TaskEntity", f"{src_role} PROV under review ({session_id})")
        t.record_activity("ReceiveTaskActivity", "MC1: receive agent PROV",
                          now_iso(), now_iso(), out_eid=subj, verbose=False)
        t.record_activity(
            "ComplianceCheckActivity", f"MC1: check {src_role} compliance",
            now_iso(), now_iso(), in_eid=subj,
            justification=f"Structural compliance of {src_role} PROV: {verdict} — {reason}.",
            extra_attrs={"yp:verdict": verdict, "yp:target_agent": src_role},
            verbose=False)

        prov = t.to_prov_json()
        write_prov(self.role, session_id, prov)
        return prov, f"compliance of {src_role} [{session_id}]: {verdict} ({reason})"
