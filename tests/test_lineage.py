"""Lineage: is every relation in the graph one that was actually observed?

A provenance system's relations are its claims. The tracker used to manufacture two kinds
of them from the order in which calls happened to be made — a derivation between each
entity and the previously registered one, and an informed-by edge between each activity and
the previously recorded one. Both produced exactly n-1 edges for n items regardless of what
the agent did, so the counts measured how many steps ran rather than how they connected,
and two unrelated entities registered in sequence yielded a claim that one came from the
other.

Derivation is now declared by the caller. Informed-by is derived from dataflow, which is
what PROV defines it to be: *a2* was informed by *a1* when *a1* generated an entity *a2*
used.
"""

from __future__ import annotations

from typing import Any

from yprov4agenticai import ProvTracker

T0 = "2026-10-08T09:00:00+00:00"
T1 = "2026-10-08T09:00:01+00:00"


def relations(document: dict[str, Any], section: str) -> set[tuple[str, str]]:
    keys = {
        "wasDerivedFrom": ("prov:generatedEntity", "prov:usedEntity"),
        "wasInformedBy": ("prov:informed", "prov:informant"),
    }[section]
    return {(edge[keys[0]], edge[keys[1]]) for edge in (document.get(section) or {}).values()}


# ──────────────────────────────────────────────────────────────────────────────────
# Derivation must be declared
# ──────────────────────────────────────────────────────────────────────────────────
def test_unrelated_entities_are_not_claimed_to_derive_from_each_other() -> None:
    """Two independent inputs, registered in sequence. Neither came from the other."""
    tracker = ProvTracker(sid="s", agent_label="A")
    tracker.register_entity("TaskEntity", "a config file", suffix="_cfg")
    tracker.register_entity("TaskEntity", "an unrelated dataset", suffix="_data")

    assert relations(tracker.to_prov_json(), "wasDerivedFrom") == set()


def test_a_declared_derivation_is_emitted() -> None:
    tracker = ProvTracker(sid="s", agent_label="A")
    source = tracker.register_entity("TaskEntity", "the input")
    product = tracker.register_entity("ResponseEntity", "the output", derived_from=source)

    assert relations(tracker.to_prov_json(), "wasDerivedFrom") == {(product, source)}


def test_an_entity_may_derive_from_several_sources() -> None:
    """Lineage is a graph, not a chain; a merge step has more than one input."""
    tracker = ProvTracker(sid="s", agent_label="A")
    left = tracker.register_entity("TaskEntity", "left input", suffix="_l")
    right = tracker.register_entity("TaskEntity", "right input", suffix="_r")
    merged = tracker.register_entity("ResponseEntity", "merged", derived_from=[left, right])

    assert relations(tracker.to_prov_json(), "wasDerivedFrom") == {
        (merged, left),
        (merged, right),
    }


def test_a_derivation_is_not_duplicated_when_an_entity_is_re_registered() -> None:
    tracker = ProvTracker(sid="s", agent_label="A")
    source = tracker.register_entity("TaskEntity", "the input")
    tracker.register_entity("ResponseEntity", "v1", derived_from=source)
    tracker.register_entity("ResponseEntity", "v2 overwrites v1", derived_from=source)

    assert len(relations(tracker.to_prov_json(), "wasDerivedFrom")) == 1


def test_declared_derivations_survive_a_state_round_trip() -> None:
    """The tracker is carried inside an agent's serialisable state, so this must persist."""
    tracker = ProvTracker(sid="s", agent_label="A")
    source = tracker.register_entity("TaskEntity", "the input")
    product = tracker.register_entity("ResponseEntity", "the output", derived_from=source)

    restored = ProvTracker.from_state("s", tracker.to_state())

    assert relations(restored.to_prov_json(), "wasDerivedFrom") == {(product, source)}


# ──────────────────────────────────────────────────────────────────────────────────
# Informed-by must follow the dataflow
# ──────────────────────────────────────────────────────────────────────────────────
def branching_lifecycle() -> ProvTracker:
    """Two steps read the same input, so they are siblings rather than a chain.

    The index-order rule claimed `read_b` was informed by `read_a` purely because it was
    recorded second. Nothing flows between them.
    """
    tracker = ProvTracker(sid="s", agent_label="A")
    task = tracker.register_entity("TaskEntity", "the input")
    tracker.record_activity(
        "ReceiveTaskActivity", "receive", T0, T1, out_eid=task, verbose=False
    )
    tracker.record_activity("PlanningActivity", "read_a", T0, T1, in_eid=task, verbose=False)
    tracker.record_activity("ToolCallActivity", "read_b", T0, T1, in_eid=task, verbose=False)
    return tracker


def test_sibling_steps_are_not_claimed_to_inform_each_other() -> None:
    document = branching_lifecycle().to_prov_json()
    edges = relations(document, "wasInformedBy")

    informants = {informant for _, informant in edges}
    assert informants == {"yp:ReceiveTaskActivity_s_00"}, (
        "both readers take their input from the receive step, and from nothing else"
    )


def test_every_informed_by_edge_is_backed_by_a_shared_entity() -> None:
    """The invariant the derivation guarantees and the index rule could not."""
    document = branching_lifecycle().to_prov_json()
    generated_by = {
        edge["prov:entity"]: edge["prov:activity"]
        for edge in document["wasGeneratedBy"].values()
    }
    consumed_by: dict[str, set[str]] = {}
    for edge in document["used"].values():
        consumed_by.setdefault(edge["prov:activity"], set()).add(edge["prov:entity"])

    for informed, informant in relations(document, "wasInformedBy"):
        shared = {
            entity
            for entity in consumed_by.get(informed, set())
            if generated_by.get(entity) == informant
        }
        assert shared, f"{informed} <- {informant} is backed by no shared entity"


def test_an_activity_that_consumes_nothing_is_informed_by_nothing() -> None:
    tracker = ProvTracker(sid="s", agent_label="A")
    tracker.record_activity("ReceiveTaskActivity", "first", T0, T1, verbose=False)
    tracker.record_activity("PlanningActivity", "second", T0, T1, verbose=False)

    assert relations(tracker.to_prov_json(), "wasInformedBy") == set()


def test_informed_by_count_is_not_a_function_of_activity_count() -> None:
    """The symptom that made the old counts misleading.

    The index rule produced n-1 edges for any n activities, so the relation total grew
    with the number of steps whether or not they were connected. Adding an unconnected
    step must not add an edge.
    """
    connected = branching_lifecycle()
    before = len(relations(connected.to_prov_json(), "wasInformedBy"))

    connected.record_activity("ResponseActivity", "unconnected", T0, T1, verbose=False)
    after = len(relations(connected.to_prov_json(), "wasInformedBy"))

    assert after == before
