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
@pytest.mark.xfail(
    strict=True,
    reason="todo.md A4: no agent subscribes to governance.>, so meta-agent output is "
    "terminal and the meta layer is never itself governed. Remove this marker when "
    "the reflexivity claim is either implemented or narrowed in the documentation.",
)
def test_some_agent_governs_the_governance_layer() -> None:
    """`governance.*` is published to but never read, so reflexivity stops one level deep."""
    import importlib

    subscriptions = []
    for module_name, class_name in REGISTRY.values():
        module = importlib.import_module(f"runtime.agents.{module_name}")
        agent_class = getattr(module, class_name, None)
        subject = getattr(agent_class, "input_subject", None)
        if subject:
            subscriptions.append(subject)

    governed = [s for s in subscriptions if s.startswith("governance")]
    assert governed, (
        f"no agent consumes governance output; subscriptions are {sorted(set(subscriptions))}"
    )
