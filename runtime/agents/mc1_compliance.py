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

from collections.abc import Iterator, Mapping
from typing import Any

from yprov4agenticai.rules import Rule, Scalar

from ..base_agent import StreamingAgent
from ..net_bus import Bus
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
    "namespace-declarations",  # not a shape: PROV-JSON prefixes, resolved in conversion
    "typed-activities",  # yp:TypedActivityShape / activity-typed
    "decision-present",  # yp:AgentDecidedShape / decision-present
    "decision-justified",  # yp:DecisionShape / decision-justified
)

#: Where the full standard is declared. Cited in the emitted record so a reader of a
#: verdict can find the shapes it was reached against.
SHAPES_REFERENCE = "vocab/shapes.ttl"


def _compliance_rule(evidence: Mapping[str, Scalar]) -> dict[str, str]:
    """PASS only a record that failed none of the structural checks."""
    return {"yp:verdict": "PASS" if evidence["failed_checks"] == 0 else "FAIL"}


COMPLIANCE_RULE = Rule("mc1.compliance/v1", _compliance_rule)

#: The namespaces a record's two prefixes must be bound to. Checking only that the prefix
#: *names* exist accepted a record whose ``yp`` was bound to some other vocabulary, in which
#: ``yp:DecisionActivity`` means something else entirely.
NAMESPACES = {
    "prov": "http://www.w3.org/ns/prov#",
    "yp": "http://hpci-lab.org/yProv4Agents#",
}

#: Every subclass of ``yp:AgentLifecycleActivity`` in ``vocab/yprov4agents.ttl``.
#:
#: A copy, because this agent checks records without an RDF toolchain in its container — and
#: a copy can drift, so ``tests/test_shapes.py`` derives the same set from the ontology and
#: fails if the two differ. ``typed-activities`` used to test only that the activity table
#: was non-empty, so a record whose steps carried no type at all passed a check named for
#: typing.
LIFECYCLE_ACTIVITY_TYPES = frozenset(
    {
        "yp:AnalysisActivity",
        "yp:AuditActivity",
        "yp:ComplianceCheckActivity",
        "yp:DecisionActivity",
        "yp:GovernanceActivity",
        "yp:ObservationActivity",
        "yp:PersistActivity",
        "yp:PlanningActivity",
        "yp:ReceiveTaskActivity",
        "yp:ResponseActivity",
        "yp:ToolCallActivity",
    }
)


def _iter_activities(doc: dict[str, Any]) -> Iterator[tuple[str, dict[str, Any]]]:
    yield from (doc.get("activity") or {}).items()


def _literal(value: Any) -> str:
    """The text of an attribute value, whether plain or a PROV-JSON typed literal.

    PROV-JSON may write a string either plainly or as ``{"$": "text", "type": "xsd:string"}``,
    and tools other than this library commonly use the second form. Taking ``str()`` of that
    dictionary is never empty, so a typed literal of pure whitespace passed the
    justification check — reintroducing, through an encoding, the defect the shapes were
    declared to catch.
    """
    if isinstance(value, dict):
        return str(value.get("$", ""))
    return "" if value is None else str(value)


def _type_of(a: dict[str, Any]) -> str:
    return _literal(a.get("prov:type"))


class MC1Compliance(StreamingAgent):
    agent_label = "ComplianceChecker"
    input_subject = "agent.lifecycle.>"
    output_prefix = "governance"

    def __init__(self, bus: Bus) -> None:
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
        else:
            wrong = sorted(n for n, uri in NAMESPACES.items() if prefix.get(n) != uri)
            if wrong:
                failures.append(f"prefix bound to the wrong namespace: {', '.join(wrong)}")

        activities = dict(_iter_activities(doc))
        if not activities:
            failures.append("no typed activities")
        else:
            untyped = sorted(
                aid
                for aid, a in activities.items()
                if _type_of(a) not in LIFECYCLE_ACTIVITY_TYPES
            )
            if untyped:
                failures.append(
                    f"{len(untyped)} activit{'y' if len(untyped) == 1 else 'ies'} without a "
                    f"lifecycle type"
                )

        decisions = [
            (aid, a) for aid, a in _iter_activities(doc) if _type_of(a) == "yp:DecisionActivity"
        ]
        if not decisions:
            failures.append("no yp:DecisionActivity present")
        else:
            # Stripped, because whitespace is not a justification. The shape's
            # sh:pattern "\S" says the same thing, and this line used to disagree with it.
            missing = [
                aid for aid, a in decisions if not _literal(a.get("yp:justification")).strip()
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
            self.agent_label,
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

        evidence: dict[str, Scalar] = {"failed_checks": len(failures)}
        verdict = COMPLIANCE_RULE.apply(evidence)["yp:verdict"]
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
            rule=COMPLIANCE_RULE,
            evidence=evidence,
            attributes={"yp:target_agent": src_role},
        )

        return tracker.emit(), f"compliance of {src_role} [{session_id}]: {verdict} ({reason})"
