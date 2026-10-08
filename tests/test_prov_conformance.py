"""Conformance: is the emitted provenance actually W3C PROV-JSON?

The project's interoperability claim rests on emitting standard PROV-JSON, so that claim
needs an independent judge. ``prov`` is the reference Python implementation and is not
project code, which is the point: it will reject anything the specification does not allow,
including extensions that look harmless.

Two layers are checked. ``ProvTracker`` output is checked directly, because the library is
what downstream users embed. The artifacts of a real run are checked separately, because an
agent can always serialise something the tracker never produced.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest
from prov.model import ProvDocument

from yprov4agenticai import ProvTracker

pytestmark = pytest.mark.conformance

T0 = "2026-10-08T09:00:00+00:00"
T1 = "2026-10-08T09:00:01+00:00"


def parse(document: dict[str, Any]) -> ProvDocument:
    """Parse with the reference implementation, or fail with the parser's own message."""
    return ProvDocument.deserialize(content=json.dumps(document), format="json")


def build_lifecycle(**tracker_kwargs: Any) -> dict[str, Any]:
    """A minimal but representative agent lifecycle: task in, decision, response out."""
    tracker = ProvTracker(sid="conformance", agent_label="TestAgent", **tracker_kwargs)
    task = tracker.register_entity("TaskEntity", "an incoming provenance record")
    tracker.record_activity(
        "ReceiveTaskActivity", "receive", T0, T1, out_eid=task, verbose=False
    )
    response = tracker.register_entity("ResponseEntity", "the agent's answer")
    tracker.record_activity(
        "DecisionActivity",
        "accept",
        T0,
        T1,
        in_eid=task,
        out_eid=response,
        justification="Every structural invariant held, so the record is accepted.",
        confidence_score=0.93,
        extra_attrs={"yp:verdict": "ACCEPT"},
        verbose=False,
    )
    return tracker.to_prov_json()


# ──────────────────────────────────────────────────────────────────────────────────
# Library output
# ──────────────────────────────────────────────────────────────────────────────────
@pytest.mark.parametrize("granularity", ["DATA", "METADATA"])
def test_tracker_output_is_valid_prov_json(granularity: str) -> None:
    parsed = parse(build_lifecycle(granularity=granularity))
    assert list(parsed.get_records()), "a parsed document should expose records"


def test_document_root_holds_only_containers_the_format_defines() -> None:
    """No unrecognised top-level key, in either mode.

    This is the guard on the regression that made every emitted document unparseable: a
    PROV-JSON consumer treats each root key as a record-type keyword and rejects the whole
    document when it meets one it does not know, rather than skipping it.
    """
    permitted = {
        "prefix",
        "entity",
        "activity",
        "agent",
        "bundle",
        "wasGeneratedBy",
        "used",
        "wasInformedBy",
        "wasStartedBy",
        "wasEndedBy",
        "wasInvalidatedBy",
        "wasDerivedFrom",
        "wasAttributedTo",
        "wasAssociatedWith",
        "actedOnBehalfOf",
        "wasInfluencedBy",
        "specializationOf",
        "alternateOf",
        "hadMember",
        "mentionOf",
    }
    for granularity in ("DATA", "METADATA"):
        unknown = set(build_lifecycle(granularity=granularity)) - permitted
        assert not unknown, f"{granularity} mode emits unrecognised root key(s): {unknown}"


def test_the_record_describes_its_own_configuration() -> None:
    """Granularity and persistence must still be recoverable, in both modes.

    Moving them out of a bespoke root key must not lose them: a reader cannot interpret a
    record without knowing whether entities were captured at all.
    """
    for granularity in ("DATA", "METADATA"):
        document = build_lifecycle(granularity=granularity)
        bundles = [
            entity
            for entity in document["entity"].values()
            if entity.get("prov:type", {}).get("$") == "prov:Bundle"
        ]
        assert len(bundles) == 1, f"{granularity}: expected exactly one bundle descriptor"
        assert bundles[0]["yp:granularity"] == granularity
        assert bundles[0]["yp:persistence"] == "PERSISTENT"
        assert bundles[0]["yp:library_version"]


def test_justification_survives_a_standard_round_trip() -> None:
    """``yp:justification`` must still be there after a third-party parse and re-serialise.

    The headline field is a custom attribute in a custom namespace. If a conforming
    implementation silently dropped it, the provenance would interoperate while losing the
    one thing the project exists to record.
    """
    document = build_lifecycle()

    serialised = parse(document).serialize(format="json")
    assert serialised is not None, "serialize() returns a string when given no stream"
    round_tripped = json.loads(serialised)
    justifications = [
        attributes["yp:justification"]
        for attributes in round_tripped["activity"].values()
        if "yp:justification" in attributes
    ]
    assert len(justifications) == 1
    assert justifications[0].startswith("Every structural invariant held")


def test_prefixes_are_declared() -> None:
    """Unresolvable prefixes make a document uninterpretable outside this project."""
    prefixes = build_lifecycle()["prefix"]
    assert prefixes["prov"] == "http://www.w3.org/ns/prov#"
    assert "yp" in prefixes, "the yp: namespace must be declared, not assumed"


# ──────────────────────────────────────────────────────────────────────────────────
# Artifacts from a real run
# ──────────────────────────────────────────────────────────────────────────────────
def test_emitted_artifacts_are_valid_prov_json(
    prov_documents: list[tuple[Path, dict[str, Any]]],
) -> None:
    rejected: list[str] = []
    for path, document in prov_documents:
        try:
            parse(document)
        except Exception as exc:
            rejected.append(f"{path.name}: {type(exc).__name__}: {exc}")

    assert not rejected, (
        f"{len(rejected)} of {len(prov_documents)} emitted documents are not valid "
        f"PROV-JSON:\n  " + "\n  ".join(rejected[:5])
    )


def test_every_decision_carries_a_justification(
    prov_documents: list[tuple[Path, dict[str, Any]]],
) -> None:
    """Structural check only.

    This asserts instrumentation coverage — that no decision point was left unrecorded.
    It deliberately says nothing about whether a justification is *informative*; see
    todo.md B1 on why coverage alone cannot be the headline result.
    """
    unjustified: list[str] = []
    for path, document in prov_documents:
        for activity_id, attributes in (document.get("activity") or {}).items():
            prov_type = attributes.get("prov:type")
            type_name = prov_type.get("$", "") if isinstance(prov_type, dict) else ""
            if (
                type_name == "yp:DecisionActivity"
                and not str(attributes.get("yp:justification", "")).strip()
            ):
                unjustified.append(f"{path.name}:{activity_id}")

    assert not unjustified, f"decisions without a justification: {unjustified}"
