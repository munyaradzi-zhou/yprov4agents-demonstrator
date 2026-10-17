"""Strategy T: an observer *inside* the step, and what it buys over one at the boundary.

    python -m eval.line_tracer --repetitions 30 --out-dir out/bench
    python -m eval.line_tracer --report out/bench/tracer.json

Proposition 1 is about observers at a step's boundary. The obvious objection is that an
observer need not stay there: ``sys.settrace`` sees every line an agent executes and every
local it assigns, which is how noWorkflow's fine-grained mode recovers a script's variables.
This module builds that observer and measures it on the same agents and the same producer
record as the overhead study, answering three questions:

*Does it see the computed values?* For each decision, the values its rule read
(``yp:rule_inputs``) are looked for among the scalar locals the tracer captured, by value.
It is expected to, and the measurement is there to say by how much.

*At what price?* Each agent's step is timed with and without the tracer, interleaved, under
the same 3x-spread rule as the overhead study; the trace's size is compared with the
record's.

*Does seeing them amount to knowing the reason?* Each decision's inputs are counted as a
share of the distinct values the tracer captured for that step. The trace holds the needle;
finding it requires reading the code, which is the step the record was supposed to save.

The tracer is deliberately cheap for what it does: it traces only frames whose code lives
in the agents, keeps scalars only, and records a local only when it changes. A real
fine-grained tracer keeps more, so the costs here are a floor.
"""

from __future__ import annotations

import argparse
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from types import FrameType
from typing import Any

from eval.bench_support import (
    capture_environment,
    dispersion,
    interleaved,
    python_is_optimised,
    relative_overhead,
    resolvable,
    write_results,
)

#: Source files the tracer follows: the agents and the lifecycle they share. Library and
#: standard-library frames are not traced, which favours the tracer on cost.
TRACED_PATHS = (
    str(Path("runtime/agents")),
    str(Path("runtime/base_agent.py")),
)

#: Longest string kept. A summary or a serialised record is a value the step produced, but
#: keeping it whole would make the trace a copy of the output.
MAX_STRING = 200

CONDITIONS = ("OFF", "TRACE")


def _scalar(value: Any) -> bool:
    if isinstance(value, bool | int | float) or value is None:
        return True
    return isinstance(value, str) and len(value) <= MAX_STRING


@dataclass
class LineTrace:
    """Every change to a scalar local, per executed line, in the traced files."""

    events: list[tuple[str, int, dict[str, Any]]] = field(default_factory=list)
    lines: int = 0
    _last: dict[int, dict[str, Any]] = field(default_factory=dict)

    def _local(self, frame: FrameType, event: str, arg: Any) -> Callable | None:
        if event == "line":
            self.lines += 1
            snapshot = {k: v for k, v in frame.f_locals.items() if _scalar(v)}
            previous = self._last.get(id(frame), {})
            changed = {k: v for k, v in snapshot.items() if previous.get(k, ...) != v}
            self._last[id(frame)] = snapshot
            if changed:
                self.events.append((frame.f_code.co_name, frame.f_lineno, changed))
        elif event == "return":
            self._last.pop(id(frame), None)
        return self._local

    def __call__(self, frame: FrameType, event: str, arg: Any) -> Callable | None:
        if event != "call":
            return None
        filename = frame.f_code.co_filename
        if not any(path in filename for path in TRACED_PATHS):
            return None
        return self._local

    def values(self) -> list[Any]:
        """Every distinct scalar value the trace holds, typed."""
        seen: dict[tuple[str, str], Any] = {}
        for _, _, changed in self.events:
            for value in changed.values():
                seen[(type(value).__name__, repr(value))] = value
        return list(seen.values())

    def size_bytes(self) -> int:
        return len(json.dumps(self.events, default=repr).encode("utf-8"))


@contextmanager
def tracing(trace: LineTrace | None) -> Iterator[None]:
    if trace is None:
        yield
        return
    previous = sys.gettrace()
    sys.settrace(trace)
    try:
        yield
    finally:
        sys.settrace(previous)


def holds(values: list[Any], wanted: Any) -> bool:
    """Whether the trace holds a value, compared as the type it was recorded as."""
    return any(type(value) is type(wanted) and value == wanted for value in values)


# ── the measurement ───────────────────────────────────────────────────────────────
def _agents() -> dict[str, tuple[Any, Callable[[Any, dict, str], Any], str]]:
    from runtime.agents.ca1_storage import CA1Storage
    from runtime.agents.ca2_metrics import CA2Metrics
    from runtime.agents.ca3_analytics import CA3Analytics
    from runtime.agents.cax_summariser import CAxSummariser
    from runtime.agents.mc1_compliance import MC1Compliance
    from runtime.agents.mc3_audit import MC3Audit

    def process(agent: Any, document: dict, subject: str) -> Any:
        return agent.process("bench", document, subject)

    def persist(agent: Any, document: dict, subject: str) -> Any:
        return agent._do_persist("bench", document, ["ca2", "ca3", "cax"], "quorum", subject)

    # The same agents and subjects as the overhead study, with no bus: process() is called
    # directly, and CA1 is measured on its persist path, which is where its decision is.
    table: dict[str, tuple[type, Callable[[Any, dict, str], Any], str]] = {
        "CA3 Analytics": (CA3Analytics, process, "producer.yprov4ml.bench"),
        "CA2 Metrics": (CA2Metrics, process, "producer.yprov4ml.bench"),
        "CAx Summariser": (CAxSummariser, process, "producer.yprov4ml.bench"),
        "CA1 Storage": (CA1Storage, persist, "benchmark"),
        "MC1 Compliance": (MC1Compliance, process, "agent.lifecycle.cax.bench"),
        "MC3 Audit": (MC3Audit, process, "agent.lifecycle.cax.bench"),
    }
    return {label: (cls(None), step, subject) for label, (cls, step, subject) in table.items()}


def _rule_inputs(record: dict[str, Any]) -> dict[str, Any]:
    for activity in (record.get("activity") or {}).values():
        if activity.get("yp:rule"):
            return json.loads(activity["yp:rule_inputs"])
    return {}


def measure(repetitions: int) -> dict[str, Any]:
    from eval.bench_overhead import _producer_record, _redirect_output, condition

    document = _producer_record()
    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as temporary:
        _redirect_output(Path(temporary))
        for label, (agent, step, subject) in _agents().items():
            # What the tracer sees, against what the record says the rule read. Run under
            # B so the record exists; the tracer does not depend on the tracker either way.
            trace = LineTrace()
            with condition("B"), tracing(trace):
                record, _ = step(agent, document, subject)
            inputs = _rule_inputs(record)
            values = trace.values()
            searchable = {k: v for k, v in inputs.items() if not isinstance(v, bool)}
            found = [k for k, v in searchable.items() if v is not None and holds(values, v)]
            record_bytes = len(json.dumps(record, ensure_ascii=False).encode("utf-8"))

            # What it costs: the agent on a null tracker, with and without the tracer.
            def run(
                name: str, agent: Any = agent, step: Any = step, subject: str = subject
            ) -> float:
                with condition("OFF"), tracing(LineTrace() if name == "TRACE" else None):
                    started = time.perf_counter_ns()
                    step(agent, document, subject)
                    return float(time.perf_counter_ns() - started)

            samples = interleaved(CONDITIONS, run, repetitions)
            medians = {name: statistics.median(taken) for name, taken in samples.items()}
            results[label] = {
                "rule_inputs": len(inputs),
                "searchable": len([v for v in searchable.values() if v is not None]),
                "found_in_trace": len(found),
                "found": found,
                "distinct_values_traced": len(values),
                "lines_traced": trace.lines,
                "trace_events": len(trace.events),
                "trace_bytes": trace.size_bytes(),
                "record_bytes": record_bytes,
                "median_ns": medians,
                "dispersion_ns": {name: dispersion(taken) for name, taken in samples.items()},
                "overhead_pct": relative_overhead(medians["OFF"], medians["TRACE"]),
                "resolvable": resolvable(samples["OFF"], samples["TRACE"]),
                "repetitions": repetitions,
            }
    return {
        "level": "line-tracer",
        "environment": asdict(capture_environment(interpreter_clean=python_is_optimised())),
        "repetitions": repetitions,
        "agents": results,
    }


def render(payload: dict[str, Any]) -> str:
    lines = [
        "| Agent | Inputs found in trace | Distinct values traced | Trace / record bytes "
        "| Time with tracer |",
        "|---|--:|--:|--:|--:|",
    ]
    found = searchable = 0
    for label, row in payload["agents"].items():
        found += row["found_in_trace"]
        searchable += row["searchable"]
        ratio = row["trace_bytes"] / row["record_bytes"]
        pct = row["overhead_pct"]
        timed = f"+{pct:,.0f}%" if row["resolvable"] and pct is not None else "not resolvable"
        lines.append(
            f"| {label} | {row['found_in_trace']} of {row['searchable']} | "
            f"{row['distinct_values_traced']:,} | {ratio:.1f}x | {timed} |"
        )
    lines.append(f"| **all** | {found} of {searchable} | | | |")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--repetitions", type=int, default=30)
    parser.add_argument("--out-dir", type=Path, default=Path("out/bench"))
    parser.add_argument("--report", type=Path, help="render an existing tracer.json")
    args = parser.parse_args(argv)
    if args.report:
        print(render(json.loads(args.report.read_text(encoding="utf-8"))))
        return 0
    payload = measure(args.repetitions)
    write_results(args.out_dir / "tracer.json", payload)
    print(render(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
