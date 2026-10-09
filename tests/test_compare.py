"""The provenance gate must see lineage, not only how much of it there is.

`eval.compare --strict` is what CI runs to assert that a change did not alter the emitted
provenance unannounced. It used to compare relation *counts*. Repointing a `used` edge at
the wrong entity, or reversing a derivation, left every count unchanged and the gate
reported "No structural differences" — for the exact defect class the paper discloses,
lineage fabricated with plausible counts (docs/AUDIT.md, finding [1]).

Every test here mutates one real archived record in one specific way and asks whether the
gate notices.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from eval.compare import compare_document, compare_runs, main, reduce_document

REFERENCE = Path("experiments/verdict-reachability/example_0__ca1_prov.json")

Doc = dict[str, Any]


@pytest.fixture
def record() -> Doc:
    doc: Doc = json.loads(REFERENCE.read_text(encoding="utf-8"))
    return doc


def _differences(before: Doc, mutate: Callable[[Doc], None]) -> list[str]:
    after = copy.deepcopy(before)
    mutate(after)
    return compare_document(reduce_document("x", before), reduce_document("x", after))


def test_an_unchanged_record_has_no_differences(record: Doc) -> None:
    assert _differences(record, lambda doc: None) == []


def test_a_repointed_used_edge_is_reported_although_every_count_is_unchanged(
    record: Doc,
) -> None:
    def repoint(doc: Doc) -> None:
        edge = next(iter(doc["used"].values()))
        edge["prov:entity"] = next(e for e in doc["entity"] if e != edge["prov:entity"])

    differences = _differences(record, repoint)
    assert any(d.startswith("edge added: used") for d in differences)
    assert any(d.startswith("edge removed: used") for d in differences)
    assert not any(d.startswith("relation ") for d in differences), "counts did not change"


def test_a_reversed_derivation_is_reported(record: Doc) -> None:
    def reverse(doc: Doc) -> None:
        edge = next(iter(doc["wasDerivedFrom"].values()))
        edge["prov:generatedEntity"], edge["prov:usedEntity"] = (
            edge["prov:usedEntity"],
            edge["prov:generatedEntity"],
        )

    assert any("wasDerivedFrom" in d for d in _differences(record, reverse))


def test_a_reference_to_a_node_that_does_not_exist_is_reported(record: Doc) -> None:
    def dangle(doc: Doc) -> None:
        next(iter(doc["used"].values()))["prov:entity"] = "yp:never_registered"

    assert any("dangling:yp:never_registered" in d for d in _differences(record, dangle))


def test_two_decisions_of_one_type_do_not_hide_each_other(record: Doc) -> None:
    """Justifications used to be keyed by activity type, so a second decision of the same
    type overwrote the first and a change to the first went unseen."""
    decision_id = next(a for a, n in record["activity"].items() if "yp:justification" in n)
    doubled = copy.deepcopy(record)
    second = copy.deepcopy(doubled["activity"][decision_id])
    second["yp:justification"] = "a second, different decision"
    doubled["activity"][decision_id + "_second"] = second

    def change_first(doc: Doc) -> None:
        doc["activity"][decision_id]["yp:justification"] = "changed"

    differences = _differences(doubled, change_first)
    assert any("justification text changed" in d for d in differences)


def test_strict_mode_fails_the_build_on_a_rewired_edge(record: Doc, tmp_path: Path) -> None:
    before, after = tmp_path / "before", tmp_path / "after"
    before.mkdir()
    after.mkdir()
    (before / REFERENCE.name).write_text(json.dumps(record), encoding="utf-8")
    rewired = copy.deepcopy(record)
    edge = next(iter(rewired["used"].values()))
    edge["prov:entity"] = next(e for e in rewired["entity"] if e != edge["prov:entity"])
    (after / REFERENCE.name).write_text(json.dumps(rewired), encoding="utf-8")

    differences, problems = compare_runs(before, after)
    assert differences and not problems
    assert main(["--before", str(before), "--after", str(after), "--strict"]) == 1
    assert main(["--before", str(before), "--after", str(before), "--strict"]) == 0
