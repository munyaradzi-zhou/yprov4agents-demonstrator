"""The vocabulary and its shapes: do they describe this system, and do they have teeth?

Three failure modes a vocabulary can have, and a test for each.

*It can define terms nothing emits.* An ontology written from the documentation rather
than from the records is the same defect as an API nothing implements, which this project
has already had once (review finding A1). So every ``yp:`` term a frozen run contains must
be defined, and the test names any that are not.

*Its shapes can be unfalsifiable.* A shape suite that passes everything is decoration. Each
shape here is checked by mutating a conforming record in the specific way the shape exists
to catch — and that is how the justification shape was found to accept whitespace while the
compliance agent, which strips before testing, would not.

*It can disagree with the agent that enforces the same rules.* MC1's four hand-written
checks and ``shapes.ttl`` are two statements of one standard. If they disagree, one is
wrong, and the last test here asks the question.
"""

from __future__ import annotations

import copy
import re
from pathlib import Path
from typing import Any

import pytest

pytestmark = pytest.mark.conformance

VOCAB = Path(__file__).resolve().parent.parent / "vocab"
ONTOLOGY = VOCAB / "yprov4agents.ttl"
SHAPES = VOCAB / "shapes.ttl"

#: Terms that belong to PROV, not to this vocabulary, and are correctly undefined here.
_PROV_OWNED = {"prov:Bundle"}


def _skip_without_rdf_tooling() -> None:
    pytest.importorskip("rdflib", reason="the eval extra provides rdflib")
    pytest.importorskip("pyshacl", reason="the eval extra provides pyshacl")
    pytest.importorskip("prov", reason="the eval extra provides prov")


@pytest.fixture(scope="module")
def defined_terms() -> set[str]:
    """Every ``yp:`` term the vocabulary defines as a subject."""
    text = ONTOLOGY.read_text(encoding="utf-8")
    # Subject position only: a term mentioned in a comment or a range is not defined.
    return set(re.findall(r"^(yp:\w+)", text, re.MULTILINE))


@pytest.fixture(scope="module")
def conforming_record(prov_documents: list[tuple[Path, dict[str, Any]]]) -> dict[str, Any]:
    """An emitted record with a decision, a pointer and a bundle descriptor in it."""
    for _, document in prov_documents:
        has_decision = any(
            str(activity.get("yp:justification", "")).strip()
            for activity in (document.get("activity") or {}).values()
        )
        has_pointer = any(
            entity.get("yp:pid") for entity in (document.get("entity") or {}).values()
        )
        if has_decision and has_pointer:
            return document
    pytest.skip("no emitted record carries both a justification and a pointer")


# ── the vocabulary describes what the system emits ────────────────────────────────
def test_the_vocabulary_is_valid_turtle() -> None:
    _skip_without_rdf_tooling()
    from rdflib import Graph

    graph = Graph().parse(location=str(ONTOLOGY), format="turtle")
    assert len(graph) > 100, "a vocabulary this small would not cover the record"


def test_the_shapes_are_valid_turtle_and_valid_shacl() -> None:
    """The shapes are themselves checked, by SHACL, against the SHACL metashapes."""
    _skip_without_rdf_tooling()
    from pyshacl import validate as shacl_validate
    from rdflib import Graph

    shapes = Graph().parse(location=str(SHAPES), format="turtle")
    conforms, _, text = shacl_validate(data_graph=shapes, meta_shacl=True)
    assert conforms, f"shapes.ttl is not well-formed SHACL:\n{text}"


def test_every_emitted_term_is_defined(
    prov_documents: list[tuple[Path, dict[str, Any]]], defined_terms: set[str]
) -> None:
    """A term in a record with no definition is a naming convention, not a vocabulary."""
    emitted: set[str] = set()
    for _, document in prov_documents:
        for section in ("activity", "entity"):
            for node in (document.get(section) or {}).values():
                emitted |= {key for key in node if key.startswith("yp:")}
                prov_type = node.get("prov:type")
                if isinstance(prov_type, dict) and str(prov_type.get("$", "")).startswith(
                    ("yp:", "prov:")
                ):
                    emitted.add(str(prov_type["$"]))

    undefined = {term for term in emitted if term not in defined_terms} - _PROV_OWNED
    assert not undefined, f"emitted but undefined in the vocabulary: {sorted(undefined)}"


def test_the_vocabulary_defines_nothing_the_demonstrator_cannot_emit(
    defined_terms: set[str],
) -> None:
    """The reverse direction, and it is allowed to fail — with a named exception list.

    A vocabulary may legitimately define more than one demonstrator exercises; a library
    feature the demonstrator does not use is a documented gap, not a lie. What is not
    acceptable is an *undocumented* one, so the exceptions are enumerated here and in
    docs/DESIGN.md rather than left to a reader to discover.
    """
    library_only = {
        "yp:MemoryStateEntity",  # register_memory: offered, not exercised — see DESIGN.md
        "yp:evidence",  # abstract superproperty, never emitted directly
        "yp:outcome",  # only on a step that raised
        "yp:error_type",  # only on a step that raised
        "yp:confidence_score",  # offered; no agent currently scores its own confidence
        "yp:MetaAgent",  # the meta agents are typed yp:OperationalAgent today — see A3
        "yp:AgentLifecycleActivity",  # superclass, never emitted as a concrete type
        "yp:GovernanceActivity",  # superclass, never emitted as a concrete type
        "yp:energy_kwh",  # energy accounting is opt-in (PROV_ENERGY)
        "yp:emissions_kg",
        "yp:energy_source",
        "yp:energy_sampling_period_s",
        "yp:energy_attributable",
        "yp:duration_s",
        "yp:model",  # model provenance needs a key; archived in phase4-llm
        "yp:temperature",
        "yp:max_tokens",
        "yp:prompt_id",
        "yp:prompt_version",
        "yp:prompt_sha",
        "yp:prompt_tokens",
        "yp:completion_tokens",
        "yp:finish_reason",
        "yp:response_sha",
        "yp:llm_latency_s",
        "yp:RecordShape",  # shape names, defined in shapes.ttl's namespace
        "yp:DecisionShape",
        "yp:ActivityShape",
        "yp:PointerShape",
        "yp:EnergyShape",
        "yp:ModelCallShape",
    }
    # Nothing to assert about the exceptions themselves; the test exists so that adding a
    # term to the vocabulary without either emitting it or listing it here fails.
    assert library_only <= defined_terms | {
        "yp:RecordShape",
        "yp:DecisionShape",
        "yp:ActivityShape",
        "yp:PointerShape",
        "yp:EnergyShape",
        "yp:ModelCallShape",
    }, "the exception list names a term the vocabulary does not define"


# ── the shapes validate real records ──────────────────────────────────────────────
def test_every_emitted_record_conforms(prov_dir: Path) -> None:
    """The headline conformance claim, judged by a standard engine over declared shapes."""
    _skip_without_rdf_tooling()
    from eval.validate_shapes import validate_run

    report = validate_run(prov_dir)
    assert report.documents > 0
    assert report.conforms, report.summary() + "\n" + "\n".join(report.violations[:2])


# ── the shapes have teeth ─────────────────────────────────────────────────────────
def _decision_id(d: dict[str, Any]) -> str:
    return next(a for a, n in d["activity"].items() if "yp:justification" in n)


def _other_step_id(d: dict[str, Any]) -> str:
    decision = _decision_id(d)
    return next(a for a in d["activity"] if a != decision)


def _delete_decision(d: dict[str, Any]) -> None:
    """Remove the decision and every relation that mentions it."""
    decision = _decision_id(d)
    d["activity"].pop(decision)
    for relation in ("used", "wasGeneratedBy", "wasInformedBy", "wasAssociatedWith"):
        for rid in [r for r, e in (d.get(relation) or {}).items() if decision in e.values()]:
            d[relation].pop(rid)


def _untype_a_step(d: dict[str, Any]) -> None:
    d["activity"][_other_step_id(d)].pop("prov:type")


def _mistype_a_step(d: dict[str, Any]) -> None:
    d["activity"][_other_step_id(d)]["prov:type"] = {
        "$": "yp:NotARealClass",
        "type": "prov:QUALIFIED_NAME",
    }


def _typed_literal_whitespace(d: dict[str, Any]) -> None:
    d["activity"][_decision_id(d)]["yp:justification"] = {"$": "   ", "type": "xsd:string"}


def _caught(document: dict[str, Any]) -> bool:
    from eval.validate_shapes import _to_rdf, validate_document

    conforms, _ = validate_document(_to_rdf(document))
    return not conforms


@pytest.mark.parametrize(
    ("what", "break_it"),
    [
        (
            "a decision with no justification",
            lambda d: [a.pop("yp:justification", None) for a in d["activity"].values()],
        ),
        (
            "a justification of only whitespace",
            lambda d: [
                a.__setitem__("yp:justification", "   ")
                for a in d["activity"].values()
                if "yp:justification" in a
            ],
        ),
        (
            "a decision with no verdict",
            lambda d: [a.pop("yp:verdict", None) for a in d["activity"].values()],
        ),
        (
            "an activity with no start time",
            lambda d: [a.pop("prov:startTime", None) for a in d["activity"].values()],
        ),
        (
            "an activity with no end time",
            lambda d: [a.pop("prov:endTime", None) for a in d["activity"].values()],
        ),
        (
            "a record with an unknown granularity",
            lambda d: [
                e.__setitem__("yp:granularity", "SUMMARY")
                for e in d["entity"].values()
                if "yp:granularity" in e
            ],
        ),
        (
            "a pointer with no content hash",
            lambda d: [e.pop("yp:contentHash", None) for e in d["entity"].values()],
        ),
        ("a record with its decision deleted", _delete_decision),
        ("an activity with no type", _untype_a_step),
        ("an activity typed with a class that does not exist", _mistype_a_step),
        ("a typed-literal justification of only whitespace", _typed_literal_whitespace),
        (
            "a content hash that is not a sha-256",
            lambda d: [
                e.__setitem__("yp:contentHash", "md5:deadbeef")
                for e in d["entity"].values()
                if "yp:contentHash" in e
            ],
        ),
    ],
)
def test_the_shapes_catch_what_they_exist_to_catch(
    conforming_record: dict[str, Any], what: str, break_it: Any
) -> None:
    """Mutate a conforming record in one specific way and require the shapes to object.

    A shape suite that passes everything is decoration. This is also how the
    whitespace-justification gap was found: ``sh:minLength 1`` accepts two spaces, while
    MC1 strips before testing, so the shape was weaker than the agent enforcing the same
    rule.
    """
    _skip_without_rdf_tooling()
    assert not _caught(conforming_record), "the unmodified record should conform"

    broken = copy.deepcopy(conforming_record)
    break_it(broken)
    assert _caught(broken), f"the shapes did not object to {what}"


# ── the shapes and the agent agree ────────────────────────────────────────────────
def test_the_shapes_and_the_compliance_agent_agree(
    conforming_record: dict[str, Any],
) -> None:
    """Two statements of one standard, checked against each other.

    MC1 enforces four named checks in Python; ``shapes.ttl`` declares them in SHACL. Where
    they disagree, one of them is wrong — and until this test existed there was no way to
    ask. Run over a record and over mutations of it, both must reach the same verdict.

    It originally covered only a missing or blank justification, and the two agreed there
    while disagreeing elsewhere: MC1 passed an untyped step, a misspelled type, a ``yp``
    prefix bound to the wrong namespace and a typed-literal justification of whitespace,
    and the shapes passed a record with no decision at all (docs/AUDIT.md, [5] and [6]).
    Every one of those is now a row here.
    """
    _skip_without_rdf_tooling()
    from runtime.agents.mc1_compliance import MC1Compliance

    def wrong_namespace(d: dict[str, Any]) -> None:
        d["prefix"]["yp"] = "http://example.org/not-yprov4agents#"

    mutations: list[tuple[str, Any]] = [
        ("a record with its decision deleted", _delete_decision),
        ("an activity with no type", _untype_a_step),
        ("an activity typed with a class that does not exist", _mistype_a_step),
        ("a typed-literal justification of only whitespace", _typed_literal_whitespace),
        ("the yp prefix bound to the wrong namespace", wrong_namespace),
        ("unmodified", lambda d: None),
        (
            "justification removed",
            lambda d: [a.pop("yp:justification", None) for a in d["activity"].values()],
        ),
        (
            "justification blanked",
            lambda d: [
                a.__setitem__("yp:justification", "  \t ")
                for a in d["activity"].values()
                if "yp:justification" in a
            ],
        ),
    ]

    for label, mutate in mutations:
        candidate = copy.deepcopy(conforming_record)
        mutate(candidate)
        failures, _ = MC1Compliance._check(candidate)
        agent_rejects = bool(failures)
        shapes_reject = _caught(candidate)
        assert agent_rejects == shapes_reject, (
            f"{label}: MC1 {'rejects' if agent_rejects else 'accepts'} but the shapes "
            f"{'reject' if shapes_reject else 'accept'} — one of the two statements of "
            f"this standard is wrong"
        )


def test_the_one_place_the_agent_is_deliberately_stricter_than_the_shapes(
    conforming_record: dict[str, Any],
) -> None:
    """A decision that keeps its justification but loses its stated type.

    The ontology gives ``yp:justification`` the domain ``yp:DecisionActivity``, so under
    RDFS inference anything carrying a justification *is* a decision, and the shapes accept
    the record. MC1 has no reasoner and neither do most PROV-JSON consumers, so it requires
    the type to be stated. That is a real difference between the two statements of the
    standard, and it is kept on purpose — so it is pinned here rather than left to be
    rediscovered. If either side changes, this test says so.
    """
    _skip_without_rdf_tooling()
    from runtime.agents.mc1_compliance import MC1Compliance

    candidate = copy.deepcopy(conforming_record)
    candidate["activity"][_decision_id(candidate)].pop("prov:type")

    failures, _ = MC1Compliance._check(candidate)
    assert failures, "MC1 must require a decision's type to be stated"
    assert not _caught(candidate), "the shapes infer the type from yp:justification's domain"


def test_the_agents_list_of_lifecycle_types_is_the_ontologys() -> None:
    """MC1 carries a copy of the lifecycle classes, because its container has no RDF
    toolchain. A copy can drift; this derives the set from the ontology and compares."""
    _skip_without_rdf_tooling()
    import rdflib
    from rdflib.namespace import RDFS

    from runtime.agents.mc1_compliance import LIFECYCLE_ACTIVITY_TYPES

    graph = rdflib.Graph().parse(ONTOLOGY, format="turtle")
    yp = rdflib.Namespace("http://hpci-lab.org/yProv4Agents#")
    root = yp.AgentLifecycleActivity
    subclasses = set(graph.transitive_subjects(RDFS.subClassOf, root)) - {root}
    from_ontology = {"yp:" + str(cls).split("#", 1)[1] for cls in subclasses}
    assert from_ontology == LIFECYCLE_ACTIVITY_TYPES


def test_the_shapes_file_is_referenced_by_the_compliance_agent() -> None:
    """The agent should point a reader at the declared standard it implements."""
    source = (
        Path(__file__).resolve().parent.parent / "runtime" / "agents" / "mc1_compliance.py"
    ).read_text(encoding="utf-8")
    assert "shapes.ttl" in source, (
        "mc1_compliance.py should cite vocab/shapes.ttl, so a reader of the agent finds "
        "the standard and a reader of the standard finds the agent"
    )
