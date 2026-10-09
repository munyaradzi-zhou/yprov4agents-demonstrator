"""Reflexivity: does the governance layer hold itself to its own standard?

The project's second contribution is that meta agents govern operational agents by reading
the provenance those agents emit about themselves. The strong form of that claim — "the
system tracks its own trackers" — requires the governance layer to be subject to the rule
it enforces. These tests apply MC1 to MC1.

That is not a trick question. A compliance checker which cannot pass its own check has
found a real inconsistency, and the honest move is to assert it in the suite so the fix is
visible when it lands.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from runtime.agent_runner import REGISTRY
from runtime.agents.mc1_compliance import MC1Compliance
from runtime.agents.mc3_audit import MC3Audit
from yprov4agenticai import ProvTracker

T0 = "2026-10-08T09:00:00+00:00"
T1 = "2026-10-08T09:00:01+00:00"

#: MC1's `process` needs no bus; the bus is only used by the streaming lifecycle around it.
NO_BUS = None


@pytest.fixture
def mc1(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MC1Compliance:
    """MC1 writing its artifacts to a temporary directory rather than the shared volume."""
    import runtime.util

    monkeypatch.setattr(runtime.util, "OUT_DIR", tmp_path)
    return MC1Compliance(NO_BUS)  # type: ignore[arg-type]


@pytest.fixture
def mc3(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> MC3Audit:
    """MC3 writing its artifacts to a temporary directory rather than the shared volume."""
    import runtime.util

    monkeypatch.setattr(runtime.util, "OUT_DIR", tmp_path)
    return MC3Audit(NO_BUS)  # type: ignore[arg-type]


def operational_agent_document() -> dict[str, Any]:
    """A well-formed operational lifecycle, of the shape CAx and CA2 emit."""
    tracker = ProvTracker(sid="subject", agent_label="NL_Summarizer")
    task = tracker.register_entity("TaskEntity", "a producer record")
    tracker.record_activity(
        "ReceiveTaskActivity", "receive", T0, T1, out_eid=task, verbose=False
    )
    response = tracker.register_entity("ResponseEntity", "a summary")
    tracker.record_activity(
        "DecisionActivity",
        "accept",
        T0,
        T1,
        in_eid=task,
        out_eid=response,
        justification="The summary is grounded in the graph it describes. Accepted.",
        extra_attrs={"yp:verdict": "ACCEPT"},
        verbose=False,
    )
    return tracker.to_prov_json()


def verdict_of(document: dict[str, Any]) -> str | None:
    """Pull the ``yp:verdict`` MC1 stamped onto its own compliance activity."""
    for attributes in (document.get("activity") or {}).values():
        if "yp:verdict" in attributes:
            return str(attributes["yp:verdict"])
    return None


def test_mc1_passes_a_well_formed_operational_record(mc1: MC1Compliance) -> None:
    document, log_line = mc1.process(
        "subject", operational_agent_document(), "agent.lifecycle.cax.subject"
    )

    assert verdict_of(document) == "PASS", log_line


def test_mc1_fails_a_decision_with_no_justification(mc1: MC1Compliance) -> None:
    """The check must have teeth: strip the justification and MC1 must notice."""
    document = operational_agent_document()
    for attributes in document["activity"].values():
        attributes.pop("yp:justification", None)

    result, log_line = mc1.process("subject", document, "agent.lifecycle.cax.subject")

    assert verdict_of(result) == "FAIL", log_line
    assert "justification" in log_line


def test_mc1_passes_its_own_output(mc1: MC1Compliance) -> None:
    """Apply the checker to the checker."""
    own_output, _ = mc1.process(
        "subject", operational_agent_document(), "agent.lifecycle.cax.subject"
    )

    verdict_on_self, log_line = mc1.process("subject-mc1", own_output, "governance.mc1.subject")

    assert verdict_of(verdict_on_self) == "PASS", log_line


def test_mc1_passes_mc3s_output(mc1: MC1Compliance, mc3: MC3Audit) -> None:
    """The standard must hold across the whole governance layer, not just for MC1.

    MC3 reconstructs trails rather than judging them, so it had no verdict step either and
    would have failed the same check. An auditor that can never report a problem is not an
    auditor, so MC3 now decides whether the trail it rebuilt is actually complete.
    """
    audit, _ = mc3.process(
        "subject", operational_agent_document(), "agent.lifecycle.cax.subject"
    )

    verdict, log_line = mc1.process("subject-mc3", audit, "governance.mc3.subject")

    assert verdict_of(verdict) == "PASS", log_line


def test_mc3_reports_a_partial_trail_when_steps_cannot_be_ordered(mc3: MC3Audit) -> None:
    """Real yProv4ML records declare start times without end times.

    An audit over such a record cannot establish durations, and activities sharing an empty
    start time are ordered by dictionary insertion rather than by evidence. Saying COMPLETE
    there would be a false assurance.
    """
    document = operational_agent_document()
    for attributes in document["activity"].values():
        attributes.pop("prov:endTime", None)

    audit, log_line = mc3.process("subject", document, "agent.lifecycle.cax.subject")

    assert verdict_of(audit) == "PARTIAL", log_line
    justification = next(
        a["yp:justification"] for a in audit["activity"].values() if a.get("yp:justification")
    )
    assert "no end time" in justification


def test_mc3_reports_a_complete_trail_when_every_step_is_timed(mc3: MC3Audit) -> None:
    audit, log_line = mc3.process(
        "subject", operational_agent_document(), "agent.lifecycle.cax.subject"
    )

    assert verdict_of(audit) == "COMPLETE", log_line


@pytest.mark.known_defect
def test_governance_output_is_terminal_by_design() -> None:
    """Reflexivity is one level deep, and that is a property rather than a gap.

    This test used to be an expected failure asserting the opposite — that *some* agent
    consumes `governance.*` — on the grounds that "the system tracks its own trackers"
    implies the meta layer is governed too. Phase 6 resolved it the other way, by deciding
    the claim rather than the code.

    The reason is termination. An agent that governs governance output emits a record that
    is itself governance output, and nothing in the subject structure stops the next pass.
    Publishing meta output to a prefix nobody subscribes to makes the recursion impossible
    by construction, which is worth more than a depth limit enforced by a counter someone
    can forget to check.

    So the invariant under test is the bound: no agent consumes `governance.*`. If that
    ever changes, either a bounded depth scheme arrived with it — in which case this test
    should assert the bound instead — or the recursion is live, which a run would not
    survive. README.md and docs/DESIGN.md §4 state the consequence for contribution C2;
    todo.md A4 records what a bounded second level would involve.
    """
    import importlib

    consumers: dict[str, str] = {}
    for role, (module_name, class_name) in REGISTRY.items():
        module = importlib.import_module(f"runtime.agents.{module_name}")
        agent_class = getattr(module, class_name, None)
        subject = getattr(agent_class, "input_subject", None)
        if subject:
            consumers[role] = subject

    governance_consumers = {
        role: subject for role, subject in consumers.items() if subject.startswith("governance")
    }
    assert not governance_consumers, (
        f"an agent now consumes governance output: {governance_consumers}. Unbounded "
        f"reflexivity does not terminate — if this is deliberate, the depth must be "
        f"bounded in the subject and this test should assert that bound."
    )


def test_the_meta_layer_satisfies_its_own_standard() -> None:
    """What *is* established: MC1 passes the check MC1 enforces.

    The capability half of the reflexivity claim. Governing the meta layer is not run in a
    pipeline, but it is demonstrable — and before Phase 1 it was false: the compliance
    agent demanded a justified decision activity and emitted none, so its own output would
    have failed its own check. Nothing but self-application finds that.
    """
    from runtime.agents.mc1_compliance import MC1Compliance

    agent = MC1Compliance.__new__(MC1Compliance)
    agent.role = "mc1"
    agent.bus = None  # type: ignore[assignment]
    agent._buffers = {}
    agent.energy = None

    subject_record = {
        "prefix": {"prov": "http://www.w3.org/ns/prov#", "yp": "http://x#"},
        "activity": {
            "yp:DecisionActivity_s_00": {
                "prov:type": {"$": "yp:DecisionActivity", "type": "prov:QUALIFIED_NAME"},
                "yp:justification": "Every structural check held.",
                "yp:verdict": "PASS",
            }
        },
    }
    own_record, _ = agent.process("s", subject_record, "agent.lifecycle.cax.s")

    failures, decisions = MC1Compliance._check(own_record)
    assert decisions, "MC1's own record must contain a decision activity"
    assert not failures, f"MC1 fails its own standard: {failures}"
