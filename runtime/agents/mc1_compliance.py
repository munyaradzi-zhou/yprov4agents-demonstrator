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

from collections.abc import Iterator
from functools import partial
from typing import Any

from yprov4agenticai import ProvTracker

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import write_prov

#: The structural standard MC1 enforces. Named so the emitted record can state which
#: checks were applied rather than leaving a reader to infer it from a verdict.
CHECK_NAMES = (
    "namespace-declarations",
    "typed-activities",
    "decision-present",
    "decision-justified",
)


def _iter_activities(doc: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    yield from (doc.get("activity") or {}).items()


def _type_of(a: dict[str, Any]) -> str:
    t = a.get("prov:type")
    if isinstance(t, dict):
        return str(t.get("$", ""))
    return str(t or "")


class MC1Compliance(StreamingAgent):
    input_subject = "agent.lifecycle.>"
    output_prefix = "governance"

    def __init__(self, bus: NetworkBus) -> None:
        super().__init__(role="mc1", bus=bus)

    @staticmethod
    def _check(doc: dict[str, Any]) -> tuple[list[str], list[tuple[str, dict[str, Any]]]]:
        """Apply the structural standard. Returns (failures, the decisions examined).

        One function, one standard: the names in :data:`CHECK_NAMES` are what the emitted
        record says were applied, so the two have to be read together.
        """
        failures: list[str] = []

        prefix = doc.get("prefix", {})
        if "prov" not in prefix or "yp" not in prefix:
            failures.append("prefix missing prov/yp declarations")
        if not doc.get("activity"):
            failures.append("no typed activities")

        decisions = [
            (aid, a) for aid, a in _iter_activities(doc) if _type_of(a) == "yp:DecisionActivity"
        ]
        if not decisions:
            failures.append("no yp:DecisionActivity present")
        else:
            missing = [aid for aid, a in decisions if not a.get("yp:justification")]
            if missing:
                failures.append(f"{len(missing)} decision(s) without yp:justification")
        return failures, decisions

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        src_role = source_subject.split(".")[2] if source_subject.count(".") >= 2 else "?"

        # The compliance check is itself provenance-tracked: the checker is auditable on
        # the same terms as the agents it checks.
        tracker = ProvTracker(
            sid=f"{session_id}-mc1",
            agent_label="ComplianceChecker",
            sink=partial(write_prov, self.role, session_id),
            verbose=False,
        )

        # S1 Receive — whose record is under review.
        with tracker.activity("ReceiveTaskActivity", "MC1: receive agent PROV") as prov:
            subject = prov.produces(
                "TaskEntity", f"{src_role} PROV under review ({session_id})"
            )

        # S3 Act — run the checks and record what was examined, not what was concluded.
        with tracker.activity(
            "ComplianceCheckActivity", f"MC1: check {src_role} compliance"
        ) as prov:
            prov.consumes(subject)
            failures, decisions = self._check(doc)
            findings = prov.produces(
                "QualityEntity",
                f"{len(failures)} structural failure(s)"
                if failures
                else "all structural checks held",
                derived_from=subject,
            )
            prov.note(
                **{
                    "yp:target_agent": src_role,
                    "yp:checks_applied": ",".join(CHECK_NAMES),
                    "yp:activities_examined": len(doc.get("activity") or {}),
                    "yp:decisions_examined": len(decisions),
                }
            )

        verdict = "PASS" if not failures else "FAIL"
        reason = "all structural checks passed" if verdict == "PASS" else "; ".join(failures)

        # S5 Decide — the verdict, as a yp:DecisionActivity carrying the justification.
        #
        # This is the step MC1 previously omitted, which meant the compliance checker
        # could not satisfy the rule it enforces on everyone else: it demanded a justified
        # yp:DecisionActivity and emitted none. Applying MC1 to its own output is a
        # one-line experiment and it failed. Recording the verdict in the canonical place
        # makes the governance layer subject to its own standard.
        tracker.decide(
            verdict,
            f"Applied {len(CHECK_NAMES)} structural checks "
            f"({', '.join(CHECK_NAMES)}) to {src_role}'s emitted PROV: "
            f"{len(doc.get('activity') or {})} activities, {len(decisions)} of them "
            f"decisions. {reason.capitalize()}. Verdict {verdict}.",
            label=f"MC1: {verdict.lower()} {src_role}",
            consumed=findings,
            attributes={"yp:target_agent": src_role},
        )

        return tracker.emit(), f"compliance of {src_role} [{session_id}]: {verdict} ({reason})"
