"""
mc1_compliance.py — MC1: the Compliance checker (meta agent, deterministic).

Consumes an OPERATIONAL agent's emitted PROV-JSON (from agent.lifecycle.*) and checks
it against the project's structural standard: is it typed W3C PROV, does it record a
DecisionActivity, and does that decision carry the required `yp:justification`? Emits
a PASS/FAIL verdict AND its own reflexive PROV (the checker is itself auditable).

**The standard is declared, not hard-coded here.** `vocab/shapes.ttl` states it in SHACL
and `eval.validate_shapes` applies it with a standard engine; this agent enforces the
subset that can be checked from one record, in process, without an RDF toolchain in the
container. The check names below are the shape names, so a verdict can be traced to the
rule it was reached against.

Publishes to `governance.*` (a different prefix from what it consumes) so meta output
never re-triggers the meta layer. That bound is deliberate and is discussed under A4 in
`todo.md`: unbounded reflexivity does not terminate.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Any

from ..base_agent import StreamingAgent
from ..net_bus import NetworkBus
from ..util import agent_tracker

#: The structural standard MC1 enforces, named after the SHACL shapes that declare it.
#:
#: The standard itself lives in ``vocab/shapes.ttl``, not here. That matters for three
#: reasons: a consumer can validate a record with any SHACL engine without running this
#: agent, the checks have names a reader can cite instead of line numbers, and the agent
#: can be tested *against* the declaration — if the two disagree, one of them is wrong,
#: which is a question with an answer.
#:
#: Asking it found a defect in this file. ``decision-justified`` was implemented as
#: ``not a.get("yp:justification")``, so a justification of pure whitespace passed. The
#: shape's ``sh:pattern "\S"`` rejects it, and ``eval.evaluate`` strips before counting —
#: three implementations of one rule, and this was the one that disagreed. See
#: ``tests/test_shapes.py::test_the_shapes_and_the_compliance_agent_agree``.
#:
#: MC1 enforces a *subset* of the shapes: what can be checked from a single record, in
#: process, without an RDF toolchain in the agent's container. ``eval.validate_shapes``
#: applies the full set offline.
CHECK_NAMES = (
    "namespace-declarations",  # not a shape: PROV-JSON prefixes, lost in RDF conversion
    "typed-activities",  # yp:ActivityShape, in aggregate
    "decision-present",  # not a shape: SHACL cannot require a node to exist
    "decision-justified",  # yp:DecisionShape / decision-justified
)

#: Where the full standard is declared. Cited in the emitted record so a reader of a
#: verdict can find the shapes it was reached against.
SHAPES_REFERENCE = "vocab/shapes.ttl"


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
            # Stripped, because whitespace is not a justification. The shape's
            # sh:pattern "\S" says the same thing, and this line used to disagree with it.
            missing = [
                aid for aid, a in decisions if not str(a.get("yp:justification", "")).strip()
            ]
            if missing:
                failures.append(f"{len(missing)} decision(s) without yp:justification")
        return failures, decisions

    def process(
        self, session_id: str, doc: dict[str, Any], source_subject: str
    ) -> tuple[dict[str, Any], str]:
        src_role = source_subject.split(".")[2] if source_subject.count(".") >= 2 else "?"

        # The compliance check is itself provenance-tracked: the checker is auditable on
        # the same terms as the agents it checks.
        tracker = agent_tracker(
            self.role,
            session_id,
            "ComplianceChecker",
            sid=f"{session_id}-mc1",
            energy=self.energy,
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
                    "yp:standard": SHAPES_REFERENCE,
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
