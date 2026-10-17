"""The LangGraph baseline: the framework's handler sees the state, never the records."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from eval import framework_baseline
from eval.bench_overhead import _redirect_output

RECORD = Path("data/yprov4ml/example_0/prov_example_GR0_0.json")


def test_one_run_through_the_graph(tmp_path: Path) -> None:
    _redirect_output(tmp_path)
    records: list[tuple[str, dict]] = []
    app = framework_baseline.build_graph(records)
    handler = framework_baseline._handler_class()()
    state = {
        "session": "s0",
        "record": json.loads(RECORD.read_text(encoding="utf-8")),
        "subject": "producer.yprov4ml.test",
        "results": {},
    }
    final = app.invoke(state, config={"callbacks": [handler]})

    # CA1 persisted after all three consumers, and every agent left a record beside the graph.
    assert set(final["results"]) == {"cax", "ca2", "ca3", "ca1"}
    assert sorted(name.split("__")[1] for name, _ in records) == ["ca1", "ca2", "ca3", "cax"]
    # The handler saw the run, and was never shown a decision's record.
    seen = "\n".join(handler.payloads)
    assert handler.events > 0
    assert "yp:rule_inputs" not in seen and "yp:justification" not in seen
