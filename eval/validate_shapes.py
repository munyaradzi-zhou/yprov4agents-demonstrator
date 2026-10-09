"""Validate emitted records against the vocabulary's SHACL shapes.

MC1, the compliance agent, enforces a structural standard that until now lived inside MC1
as four hand-written predicates. ``vocab/shapes.ttl`` declares that standard, and this
module applies it — with a standard engine, over the same records, so the agent can be
checked *against* the shapes rather than being the only thing that knows them.

    python -m eval.validate_shapes --prov-dir out
    python -m eval.validate_shapes --prov-dir experiments/audit-llm --verbose

The path from an emitted record to a validated one has one step worth explaining. The
demonstrator emits PROV-JSON, and SHACL validates RDF graphs. The ``prov`` library — the
same independent implementation the conformance tests use as a judge — reads the PROV-JSON
and writes RDF, so the pipeline is:

    record.json  →  prov.ProvDocument  →  RDF graph  →  pyshacl  →  conformance report

Two deliberate consequences. The conversion is not ours, so a record that fails to convert
is a record that is not really PROV, and the validation inherits the conformance gate for
free. And a ``yp:`` attribute survives the round trip as an RDF predicate in this
project's namespace, which is what lets a shape written against the vocabulary apply to a
document the agents produced without any translation layer of our own.

What this cannot do is judge whether a justification is *good*. No shape can; that is a
judgement about prose. What it establishes is that the field is present, non-empty, and on
the activity type that is supposed to carry it — which is the claim worth making
mechanically, and the one a reader can re-check with software they already trust.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

#: Where the vocabulary lives, relative to the repository root.
VOCAB_DIR = Path(__file__).resolve().parent.parent / "vocab"
ONTOLOGY = VOCAB_DIR / "yprov4agents.ttl"
SHAPES = VOCAB_DIR / "shapes.ttl"


@dataclass
class Report:
    """The outcome of validating one run's records."""

    documents: int = 0
    conforming: int = 0
    unconvertible: list[str] = field(default_factory=list)
    violations: list[str] = field(default_factory=list)
    checks_exercised: Counter[str] = field(default_factory=Counter)

    @property
    def conforms(self) -> bool:
        return not self.unconvertible and not self.violations

    def summary(self) -> str:
        if self.conforms:
            return f"{self.conforming}/{self.documents} records conform to vocab/shapes.ttl"
        parts = [f"{self.conforming}/{self.documents} records conform"]
        if self.unconvertible:
            parts.append(f"{len(self.unconvertible)} would not convert to RDF")
        if self.violations:
            parts.append(f"{len(self.violations)} shape violation(s)")
        return "; ".join(parts)


def _to_rdf(document: dict[str, Any]) -> Any:
    """Convert one PROV-JSON document to an RDF graph, via the reference library.

    Deliberately not our own converter. A document that will not round-trip through an
    independent PROV implementation is not conformant PROV, and that failure should
    surface here rather than being smoothed over by a bespoke translation.
    """
    from prov.model import ProvDocument

    parsed = ProvDocument.deserialize(content=json.dumps(document), format="json")
    return parsed.serialize(format="rdf", rdf_format="turtle")


def validate_document(turtle: str) -> tuple[bool, str]:
    """Validate one record's RDF against the shapes. Returns (conforms, report text)."""
    from pyshacl import validate as shacl_validate
    from rdflib import Graph

    data = Graph().parse(data=turtle, format="turtle")
    # The ontology is loaded as its own graph rather than merged into the data: the
    # shapes target yp: classes, and the subclass hierarchy has to be available for a
    # record typed yp:DecisionActivity to be recognised as a yp:AgentLifecycleActivity.
    ontology = Graph().parse(location=str(ONTOLOGY), format="turtle")
    shapes = Graph().parse(location=str(SHAPES), format="turtle")

    conforms, _, text = shacl_validate(
        data_graph=data,
        shacl_graph=shapes,
        ont_graph=ontology,
        inference="rdfs",
        advanced=True,
        abort_on_first=False,
        meta_shacl=False,
    )
    return bool(conforms), str(text)


def validate_run(prov_dir: Path) -> Report:
    """Validate every emitted record in a run directory."""
    report = Report()
    for path in sorted(prov_dir.glob("*_prov.json")):
        report.documents += 1
        document = json.loads(path.read_text(encoding="utf-8"))
        try:
            turtle = _to_rdf(document)
        except Exception as exc:
            report.unconvertible.append(f"{path.name}: {exc}")
            continue

        conforms, text = validate_document(turtle)
        if conforms:
            report.conforming += 1
        else:
            report.violations.append(f"{path.name}:\n{text}")

        # Which shapes actually had something to say about this record. A shape that
        # never matches anything is not enforcing a standard, it is decoration — and a
        # suite of such shapes would report perfect conformance over an empty check.
        for activity in (document.get("activity") or {}).values():
            report.checks_exercised["prov:Activity (TypedActivityShape)"] += 1
            prov_type = activity.get("prov:type")
            name = prov_type.get("$", "") if isinstance(prov_type, dict) else ""
            if name:
                report.checks_exercised[name] += 1
        for _agent in document.get("agent") or {}:
            report.checks_exercised["prov:Agent (AgentDecidedShape)"] += 1
        for entity in (document.get("entity") or {}).values():
            if entity.get("yp:pid"):
                report.checks_exercised["yp:pid (PointerShape)"] += 1
            if entity.get("yp:granularity"):
                report.checks_exercised["prov:Bundle (RecordShape)"] += 1
    return report


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.validate_shapes",
        description="Validate emitted provenance against vocab/shapes.ttl.",
    )
    parser.add_argument("--prov-dir", default="out", help="directory of *_prov.json")
    parser.add_argument(
        "--verbose", action="store_true", help="print the full SHACL report per failure"
    )
    args = parser.parse_args(argv)

    prov_dir = Path(args.prov_dir)
    if not prov_dir.is_dir():
        raise SystemExit(f"no such directory: {prov_dir}")
    for required in (ONTOLOGY, SHAPES):
        if not required.is_file():
            raise SystemExit(f"missing vocabulary file: {required}")

    report = validate_run(prov_dir)
    print(f"[shacl] {report.summary()}")

    if report.checks_exercised:
        print("[shacl] node kinds the shapes were applied to:")
        for name, count in sorted(report.checks_exercised.items()):
            print(f"          {name:<36} {count}")

    for failure in report.unconvertible:
        print(f"  ! will not convert: {failure}")
    for violation in report.violations:
        print(f"  ! {violation.splitlines()[0]}")
        if args.verbose:
            print("\n".join(f"      {line}" for line in violation.splitlines()[1:]))

    return 0 if report.conforms else 1


if __name__ == "__main__":
    raise SystemExit(main())
