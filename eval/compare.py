"""Compare two runs' emitted provenance, ignoring what is expected to differ.

Every phase of work on this demonstrator has to answer one of two questions: *did this
change alter the provenance?* or *did this change alter only what I intended?* A byte
diff cannot answer either, because timestamps, session identifiers and content-addressed
pointers legitimately differ between runs of identical code.

This module compares the parts that carry meaning — which typed activities and entities
exist, how they are related, what each decision concluded and why — and reports the rest
as context. A refactor should produce an empty structural diff; a fix should produce
exactly the structural diff it claims.

    python -m eval.compare --before experiments/baseline-pre-review --after out
    python -m eval.compare --before experiments/baseline-pre-review --after out --strict
"""

from __future__ import annotations

import argparse
import json
import re
from collections import Counter
from pathlib import Path
from typing import Any, NamedTuple

REL_KEYS = (
    "used",
    "wasGeneratedBy",
    "wasInformedBy",
    "wasDerivedFrom",
    "wasAssociatedWith",
    "actedOnBehalfOf",
)

#: The two endpoints of each relation, in PROV-JSON's own field names, ordered
#: (from, to) the way the relation reads: ``used`` reads "activity used entity".
REL_ENDPOINTS = {
    "used": ("prov:activity", "prov:entity"),
    "wasGeneratedBy": ("prov:entity", "prov:activity"),
    "wasInformedBy": ("prov:informed", "prov:informant"),
    "wasDerivedFrom": ("prov:generatedEntity", "prov:usedEntity"),
    "wasAssociatedWith": ("prov:activity", "prov:agent"),
    "actedOnBehalfOf": ("prov:delegate", "prov:responsible"),
}

#: Attributes that legitimately differ between two runs of identical code.
VOLATILE_ATTRS = frozenset(
    {"prov:startTime", "prov:endTime", "prov:startedAtTime", "prov:endedAtTime"}
)

#: Sequence suffix minted per activity (`..._00`, `..._01`), stable only within a run.
_ORDINAL = re.compile(r"_\d+$")


class Document(NamedTuple):
    """One emitted provenance document, reduced to what a comparison should consider."""

    name: str
    activity_types: Counter[str]
    entity_types: Counter[str]
    relation_counts: dict[str, int]
    edges: Counter[tuple[str, str, str]]
    verdicts: Counter[str]
    justifications: dict[str, str]
    activity_attributes: Counter[str]
    entity_attributes: Counter[str]
    root_keys: frozenset[str]


def _type_of(node: dict[str, Any]) -> str:
    prov_type = node.get("prov:type")
    if isinstance(prov_type, dict):
        return str(prov_type.get("$", ""))
    return str(prov_type or "")


def _node_keys(doc: dict[str, Any]) -> dict[str, str]:
    """Give every node a name that survives re-running the same code.

    Identifiers embed a session and an ordinal, so they differ between runs that are
    otherwise identical. A node is named instead by its kind, its type, and its position
    among the nodes of that type in the order the record lists them — which is the order
    the agent registered them. Two activities of the same type stay distinct
    (``activity:yp:ToolCallActivity#0`` and ``#1``), which a bare type would merge.
    """
    keys: dict[str, str] = {}
    for kind in ("activity", "entity", "agent"):
        seen: Counter[str] = Counter()
        for node_id, node in (doc.get(kind) or {}).items():
            node_type = _type_of(node) or _ORDINAL.sub("", node_id)
            keys[node_id] = f"{kind}:{node_type}#{seen[node_type]}"
            seen[node_type] += 1
    return keys


def _edges(doc: dict[str, Any], keys: dict[str, str]) -> Counter[tuple[str, str, str]]:
    """Every relation as (relation, from, to) over stable node names.

    This is what makes the comparison see lineage rather than only its volume: a ``used``
    edge repointed at a different entity, or a derivation reversed, leaves every count
    unchanged and changes this set. An endpoint that names no node in the record is kept
    as ``dangling:<id>`` rather than dropped, because a dangling reference is a change.
    """
    edges: Counter[tuple[str, str, str]] = Counter()
    for relation, (source_field, target_field) in REL_ENDPOINTS.items():
        for record in (doc.get(relation) or {}).values():
            source, target = record.get(source_field), record.get(target_field)
            edges[
                (
                    relation,
                    keys.get(str(source), f"dangling:{source}"),
                    keys.get(str(target), f"dangling:{target}"),
                )
            ] += 1
    return edges


def reduce_document(name: str, doc: dict[str, Any]) -> Document:
    activities = doc.get("activity") or {}
    entities = doc.get("entity") or {}
    keys = _node_keys(doc)

    activity_attributes: Counter[str] = Counter()
    verdicts: Counter[str] = Counter()
    justifications: dict[str, str] = {}

    for activity_id, node in activities.items():
        key = keys[activity_id]
        for attribute in node:
            if attribute not in VOLATILE_ATTRS:
                activity_attributes[attribute] += 1
        if "yp:verdict" in node:
            verdicts[str(node["yp:verdict"])] += 1
        if node.get("yp:justification"):
            justifications[key] = str(node["yp:justification"])

    # Entity attributes are tracked separately. Renaming one — `yp:value` to the standard
    # `prov:value`, say — changes what a conforming consumer can read, so a comparison
    # that watched only activities would report such a change as no change at all.
    entity_attributes: Counter[str] = Counter()
    for node in entities.values():
        for attribute in node:
            entity_attributes[attribute] += 1

    return Document(
        name=name,
        activity_types=Counter(_type_of(node) for node in activities.values()),
        entity_types=Counter(_type_of(node) for node in entities.values()),
        relation_counts={key: len(doc.get(key) or {}) for key in REL_KEYS},
        edges=_edges(doc, keys),
        verdicts=verdicts,
        justifications=justifications,
        activity_attributes=activity_attributes,
        entity_attributes=entity_attributes,
        # An unrecognised root key makes a conforming parser reject the whole document,
        # so a change at this level is always worth reporting.
        root_keys=frozenset(doc),
    )


def load(run_dir: Path) -> dict[str, Document]:
    """Reduce every emitted document in a run directory, keyed by filename."""
    documents: dict[str, Document] = {}
    for path in sorted(run_dir.glob("*_prov.json")):
        doc = json.loads(path.read_text(encoding="utf-8"))
        documents[path.name] = reduce_document(path.name, doc)
    return documents


def _counter_delta(before: Counter[str], after: Counter[str]) -> list[str]:
    lines = []
    for key in sorted(set(before) | set(after)):
        was, now = before.get(key, 0), after.get(key, 0)
        if was != now:
            lines.append(f"{key}: {was} -> {now}")
    return lines


def compare_document(before: Document, after: Document) -> list[str]:
    """Structural differences between the same document in two runs."""
    differences: list[str] = []

    for label, was, now in (
        ("activity types", before.activity_types, after.activity_types),
        ("entity types", before.entity_types, after.entity_types),
        ("verdicts", before.verdicts, after.verdicts),
        ("activity attributes", before.activity_attributes, after.activity_attributes),
        ("entity attributes", before.entity_attributes, after.entity_attributes),
    ):
        differences.extend(f"{label}: {delta}" for delta in _counter_delta(was, now))

    for key in sorted(before.root_keys ^ after.root_keys):
        verb = "removed" if key in before.root_keys else "added"
        differences.append(f"root key {verb}: {key}")

    for key in REL_KEYS:
        before_count = before.relation_counts[key]
        after_count = after.relation_counts[key]
        if before_count != after_count:
            differences.append(f"relation {key}: {before_count} -> {after_count}")

    # Counts above say how much lineage changed; this says which. A rewired edge keeps
    # every count and shows up only here.
    for edge in sorted(set(before.edges) | set(after.edges)):
        before_n, after_n = before.edges.get(edge, 0), after.edges.get(edge, 0)
        if before_n != after_n:
            relation, source, target = edge
            verb = "removed" if after_n < before_n else "added"
            differences.append(f"edge {verb}: {relation} {source} -> {target}")

    for key in sorted(set(before.justifications) | set(after.justifications)):
        before_text = before.justifications.get(key)
        after_text = after.justifications.get(key)
        if before_text == after_text:
            continue
        if before_text is None:
            differences.append(f"justification added on {key}")
        elif after_text is None:
            differences.append(f"justification REMOVED from {key}")
        else:
            differences.append(f"justification text changed on {key}")

    return differences


def compare_runs(before_dir: Path, after_dir: Path) -> tuple[dict[str, list[str]], list[str]]:
    """Compare two run directories.

    Returns (per-document differences, run-level problems). Run-level problems cover
    documents that appeared or vanished, which is a structural change in its own right.
    """
    before, after = load(before_dir), load(after_dir)
    problems: list[str] = []
    problems.extend(
        f"document missing from after: {name}" for name in sorted(set(before) - set(after))
    )
    problems.extend(
        f"document new in after: {name}" for name in sorted(set(after) - set(before))
    )

    differences: dict[str, list[str]] = {}
    for name in sorted(set(before) & set(after)):
        delta = compare_document(before[name], after[name])
        if delta:
            differences[name] = delta
    return differences, problems


def totals(run_dir: Path) -> dict[str, int]:
    """Aggregate structural totals for a run, as reported in the paper's E1/E3 tables."""
    documents = load(run_dir)
    return {
        "documents": len(documents),
        "activities": sum(sum(d.activity_types.values()) for d in documents.values()),
        "entities": sum(sum(d.entity_types.values()) for d in documents.values()),
        "relations": sum(sum(d.relation_counts.values()) for d in documents.values()),
        "justifications": sum(len(d.justifications) for d in documents.values()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.compare",
        description="Compare the provenance two runs emitted, ignoring timestamps.",
    )
    parser.add_argument("--before", required=True, help="baseline run directory")
    parser.add_argument("--after", required=True, help="candidate run directory")
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero on any structural difference (use when asserting a refactor "
        "changed nothing)",
    )
    args = parser.parse_args(argv)

    before_dir, after_dir = Path(args.before), Path(args.after)
    for path in (before_dir, after_dir):
        if not path.is_dir():
            raise SystemExit(f"no such run directory: {path}")

    before_totals, after_totals = totals(before_dir), totals(after_dir)
    print(f"{'signal':<16} {'before':>9} {'after':>9} {'delta':>9}")
    for key in before_totals:
        was, now = before_totals[key], after_totals[key]
        marker = "" if was == now else "   <-- changed"
        print(f"{key:<16} {was:>9} {now:>9} {now - was:>+9}{marker}")

    differences, problems = compare_runs(before_dir, after_dir)

    if problems:
        print(f"\nRun-level problems ({len(problems)}):")
        for problem in problems:
            print(f"  ! {problem}")

    if not differences:
        print(
            "\nNo structural differences: every document carries the same typed "
            "activities, entities, lineage edges, verdicts and justifications."
        )
    else:
        print(f"\nStructural differences in {len(differences)} document(s):")
        for name, deltas in differences.items():
            print(f"  {name}")
            for delta in deltas:
                print(f"    - {delta}")

    changed = bool(differences or problems)
    return 1 if (changed and args.strict) else 0


if __name__ == "__main__":
    raise SystemExit(main())
