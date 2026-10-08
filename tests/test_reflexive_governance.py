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


@pytest.mark.known_defect
@pytest.mark.xfail(
    strict=True,
    reason="todo.md A5: MC1 emits ReceiveTaskActivity and ComplianceCheckActivity but no "
    "yp:DecisionActivity, which is exactly what it requires of everyone else — so it "
    "fails its own check. Remove this marker once MC1's verdict is recorded as a "
    "DecisionActivity.",
)
def test_mc1_passes_its_own_output(mc1: MC1Compliance) -> None:
    """Apply the checker to the checker."""
    own_output, _ = mc1.process(
        "subject", operational_agent_document(), "agent.lifecycle.cax.subject"
    )

    verdict_on_self, log_line = mc1.process("subject-mc1", own_output, "governance.mc1.subject")

    assert verdict_of(verdict_on_self) == "PASS", log_line


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
