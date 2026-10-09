"""Can every verdict actually fire?

A verdict that only ever takes one value carries no information, so the evaluation reports
reachability alongside the tally. These tests run the injected faults and require each one
to provoke the verdict it exists to provoke — which makes reachability a property the suite
enforces rather than a claim a run happened to demonstrate once.

The discipline that makes them worth anything: **every fault goes into the input or the
environment, never into the check being tested.** A corrupted payload comes from a store
that writes bytes it did not hash; a non-conforming record comes from a third-party
producer; an invented figure comes from a scripted model. The agents' checks, verdict logic
and justifications are untouched in all seven scenarios. A fault injection that patches the
predicate it is exercising establishes only that the patch works.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from eval.fault_injection import SCENARIOS, CorruptingStore, Scenario, run
from yprov4agenticai import LightweightStore


@pytest.mark.parametrize("scenario", SCENARIOS, ids=lambda s: s.name)
def test_each_injected_fault_provokes_its_verdict(scenario: Scenario, tmp_path: Path) -> None:
    """The verdict, and the reason, both have to come out right.

    Checking the reason matters as much as the verdict: CA1 reaches ESCALATE down two
    independent paths — a corrupted payload and an unconfirmed consumption — and they call
    for opposite responses. A test that accepted either would not distinguish them.
    """
    observed = run(scenario, tmp_path)

    assert observed["verdict"] == scenario.verdict, (
        f"{scenario.name} produced {observed['verdict']} rather than {scenario.verdict}"
    )
    assert str(observed["justification"]).strip(), "a verdict without a reason is not a result"


def test_the_two_escalation_paths_are_distinguishable() -> None:
    """Same verdict, different cause, and the record has to say which.

    This is the paper's worked example in test form. A failed integrity check means the
    stored bytes are wrong; an unconfirmed consumption means the data is fine and a
    consumer is down. An operator handed only "ESCALATE" cannot tell them apart, and the
    two demand opposite responses.
    """
    import tempfile

    by_name = {scenario.name: scenario for scenario in SCENARIOS}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        integrity = run(by_name["ca1-integrity"], root)
        timeout = run(by_name["ca1-timeout"], root)
        incomplete = run(by_name["ca1-incomplete"], root)

    assert integrity["verdict"] == timeout["verdict"] == incomplete["verdict"] == "ESCALATE"
    assert integrity["escalation_reason"] == "integrity"
    assert timeout["escalation_reason"] == "consumption-unconfirmed"
    assert incomplete["escalation_reason"] == "incomplete-input"
    justifications = {
        integrity["justification"],
        timeout["justification"],
        incomplete["justification"],
    }
    assert len(justifications) == 3, "three causes, three different explanations"
    assert "partial" in incomplete["justification"]
    assert "sha-256" in integrity["justification"] or "mismatch" in integrity["justification"]
    assert "ordering was not satisfied" in timeout["justification"]


def test_every_verdict_of_every_agent_is_covered() -> None:
    """The matrix closes, or the suite says which cell is missing.

    Twelve verdicts across six agents, plus INCOMPLETE, which every agent can record
    because the gap check lives in the base class they share; one scenario exercises that
    shared path. Nine come from the ordinary run; the other three of the twelve —
    CA2's ACCEPT, CAx's REVISE, CA3's OK — are unreachable from the bundled sample data,
    because the real yProv4ML records carry no numeric values to plot and declare start
    times without end times. That is a finding about the input rather than about the
    agents, and separating the two is the reason these scenarios exist.
    """
    declared = {
        "ca1": {"ACCEPT", "ESCALATE"},
        "cax": {"ACCEPT", "REVISE"},
        "ca2": {"ACCEPT", "REVISE"},
        "ca3": {"OK", "ANOMALY"},
        "mc1": {"PASS", "FAIL"},
        "mc3": {"COMPLETE", "PARTIAL"},
    }
    #: Recorded by the shared base class on a lost envelope, so observing it for any one
    #: agent observes the code path every agent runs.
    shared = {"INCOMPLETE"}
    from_faults: dict[str, set[str]] = {}
    for scenario in SCENARIOS:
        from_faults.setdefault(scenario.agent, set()).add(scenario.verdict)

    # What the happy path produces, read from the frozen reference run rather than assumed.
    reference = Path(__file__).resolve().parent.parent / "experiments" / "phase6-vocabulary"
    from_happy_path: dict[str, set[str]] = {}
    if reference.is_dir():
        for path in reference.glob("*_prov.json"):
            role = path.stem[: -len("_prov")].split("__")[-1]
            document = json.loads(path.read_text(encoding="utf-8"))
            for activity in (document.get("activity") or {}).values():
                if activity.get("yp:verdict"):
                    from_happy_path.setdefault(role, set()).add(activity["yp:verdict"])

    missing = {
        agent: sorted(
            verdicts - from_faults.get(agent, set()) - from_happy_path.get(agent, set())
        )
        for agent, verdicts in declared.items()
    }
    observed_anywhere = set().union(*from_faults.values(), *from_happy_path.values())
    if shared - observed_anywhere:
        missing["*"] = sorted(shared - observed_anywhere)
    uncovered = {agent: gaps for agent, gaps in missing.items() if gaps}
    assert not uncovered, (
        f"verdicts declared in the README but never observed: {uncovered}. Either add a "
        f"scenario to eval/fault_injection.py or stop advertising the verdict."
    )


def test_the_corrupting_store_is_a_faithful_fault() -> None:
    """The injection has to be silent, or it is testing the wrong thing.

    `put` must succeed and return a well-formed pointer whose recorded hash no longer
    matches what is on disk. If the corruption were detectable at write time, CA1's
    round-trip read would not be what caught it.
    """
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary).resolve()
        honest = LightweightStore(root=str(root / "honest"))
        faulty = CorruptingStore(root=str(root / "faulty"))

        payload = "the payload that will be corrupted"
        good = honest.put(payload, kind="test")
        bad = faulty.put(payload, kind="test")

        assert bad["yp:contentHash"] == good["yp:contentHash"], (
            "the fault must be silent: the pointer and hash are the honest ones"
        )
        assert honest.resolve(good["yp:pid"]) == payload
        with pytest.raises(ValueError, match="content-hash mismatch"):
            faulty.resolve(bad["yp:pid"])


def test_a_relative_store_root_works() -> None:
    """Regression: the store used to crash on the first put with a relative root.

    `Path.as_uri()` raises on a relative path, and the payload record carries a `file:`
    URI — so a store opened on "out/store" worked until something was written to it. Found
    while building the fault injector, which is the first code to open a store on a
    relative path.
    """
    import os
    import tempfile

    with tempfile.TemporaryDirectory() as temporary:
        previous = Path.cwd()
        try:
            os.chdir(temporary)
            store = LightweightStore(root="relative/store")
            pointer = store.put("payload", kind="test")
            assert store.resolve(pointer["yp:pid"]) == "payload"
        finally:
            os.chdir(previous)
