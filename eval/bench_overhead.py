"""E6 — what instrumentation costs. The measurement, not the argument.

Implements ``docs/E6_OVERHEAD.md``, amended by ``PLAN.md`` Part 1. Four levels, each
answering a different question, and each able to be run alone:

    micro     what does one library call cost?            (Level 1, the primary result)
    agent     what does one agent's full step cost?       (Level 2, the ratio result)
    scaling   does the cost stay linear as a graph grows? (§7, where a bug would hide)
    modes     what do the tunable modes buy and cost?     (E6c, the tunability result)
    pipeline  what does the whole system cost?            (Level 3, sequential — see R8)

    python -m eval.bench_overhead --level all
    python -m eval.bench_overhead --level micro --iterations 20000

Three conditions, differing only in what is installed (see :mod:`yprov4agenticai.null`):
OFF is a null tracker with no observer, A is a null tracker with the callback baseline
attached, B is the real tracker. The agents are measured unmodified in all three.

Two rules this harness follows and the paper should state:

*Interleave.* Conditions run round-robin, never in blocks. Thermal drift and background
load then hit every condition equally instead of landing on whichever ran last.

*Report the floor.* Several operations here cost less than a microsecond, which is within
an order of magnitude of reading the clock twice. Every figure carries the measured timer
floor, and one near it is reported as an upper bound rather than an estimate.
"""

from __future__ import annotations

import argparse
import datetime
import json
import statistics
import sys
import tempfile
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eval.bench_support import (
    DEFAULT_ITERATIONS,
    DEFAULT_WARMUP,
    Difference,
    Timing,
    capture_environment,
    dispersion,
    fit_exponent,
    interleaved,
    paired_difference,
    python_is_optimised,
    relative_overhead,
    resolvable,
    timeit,
    timer_floor,
    timings_to_json,
    write_results,
)
from yprov4agenticai import LightweightStore, ProvTracker, now_iso
from yprov4agenticai.store import ptr_kwargs

#: Conditions, in the order they are interleaved.
#:
#: B-TRANS is not a fourth kind of instrumentation — it is condition B with persistence
#: turned off, and it is here because without it "B costs 40% more than OFF" hides what the
#: 40% is. Level 1 says a six-step record costs tens of microseconds to build and that one
#: file write costs hundreds. Separating the two turns an unactionable ratio into a choice:
#: this much to construct the provenance, that much to put it on a disk.
CONDITIONS = ("OFF", "A", "B-TRANS", "B")

#: The lifecycle every level measures: six steps, which is what the operational agents emit.
LIFECYCLE_STEPS = 6

#: PROV-JSON relation containers, counted wherever a record's structure is reported.
RELATION_KEYS = (
    "used",
    "wasGeneratedBy",
    "wasInformedBy",
    "wasDerivedFrom",
    "wasAssociatedWith",
)

#: A justification of representative length. Taken from CA1's quorum path rather than
#: invented, because the cost of the field is the cost of the text agents actually write.
SAMPLE_JUSTIFICATION = (
    "Persisted at quorum: all expected consumers acknowledged; acknowledged by "
    "[ca2, ca3, cax], the full set required. Record carries 9 activities / 39 entities / "
    "55 relations, 21634 bytes. Integrity: round-trip read verified; sha-256 matched. "
    "Verdict ACCEPT."
)

SAMPLE_ATTRS = {
    "yp:verdict": "ACCEPT",
    "yp:persist_trigger": "quorum",
    "yp:consumers_required": 3,
    "yp:consumers_acknowledged": 3,
}


# ══════════════════════════════════════════════════════════════════════════════════
#  Level 1 — the microbenchmark
# ══════════════════════════════════════════════════════════════════════════════════
def _fresh_tracker() -> ProvTracker:
    return ProvTracker(sid="bench", agent_label="Bench_Agent", verbose=False)


def _populated_tracker(steps: int = LIFECYCLE_STEPS) -> ProvTracker:
    """A tracker holding one agent's worth of lifecycle, for serialisation timing."""
    tracker = _fresh_tracker()
    previous = tracker.register_entity("TaskEntity", "the task")
    for index in range(steps):
        produced = tracker.register_entity(
            "ToolResultEntity", f"result {index}", suffix=f"_{index}", derived_from=previous
        )
        tracker.record_activity(
            "ToolCallActivity",
            f"step {index}",
            now_iso(),
            now_iso(),
            in_eid=previous,
            out_eid=produced,
            justification=SAMPLE_JUSTIFICATION if index == steps - 1 else None,
            extra_attrs=SAMPLE_ATTRS if index == steps - 1 else None,
        )
        previous = produced
    return tracker


#: Calls per timed sample for the sub-microsecond operations. The timer floor on a Windows
#: host is around a microsecond, so an unbatched figure for a 200 ns call would be a
#: measurement of the clock. These operations are O(1) in the number of records already
#: held — the scaling study confirms it independently — so batching them on one tracker is
#: a fair measurement rather than a convenient one.
FAST_BATCH = 1_000

#: Iterations for the operations that touch the disk. Millisecond-scale work needs far
#: fewer samples for a stable median, and 10,000 file writes would measure the filesystem's
#: patience rather than the library.
IO_ITERATIONS = 300


def level_micro(iterations: int, warmup: int, out_dir: Path) -> dict[str, Any]:
    """Time individual library calls (M1–M12 of ``docs/E6_OVERHEAD.md`` §5).

    Three measurement regimes, chosen by what is being measured rather than uniformly:

    *Batched* for anything under a microsecond (M1–M6, M12), because the clock cannot
    resolve a single call. The median is then trustworthy and the tail is not — a tail of
    batch means is narrower than a tail of calls — so the tail columns for these rows are
    reported as what they are.

    *Unbatched, few iterations* for the storage layer (M9–M11), which is millisecond-scale.

    *Unbatched, many iterations* for serialisation (M7, M8), which sits between the two and
    where the tail is the interesting part.
    """
    floor = timer_floor(min(iterations, DEFAULT_ITERATIONS))
    results: list[Timing] = []
    #: Tail percentiles for the batched operations, measured separately. A batch yields one
    #: sample per batch, so a percentile over batch means is not a percentile over calls —
    #: the median transfers and the tail does not. Both measurements are cheap, so both are
    #: taken: the batch for a median the clock could not otherwise resolve, and an
    #: unbatched run for a tail that means what a reader will take it to mean.
    tails: dict[str, dict[str, float]] = {}

    tracker = _fresh_tracker()

    def measure(
        name: str,
        fn: Callable[[], Any],
        *,
        count: int | None = None,
        batch: int = 1,
    ) -> None:
        results.append(
            timeit(
                name,
                fn,
                iterations=count or iterations,
                warmup=warmup if batch == 1 else min(warmup, 100),
                floor_ns=floor,
                batch=batch,
            )
        )
        if batch > 1:
            unbatched = timeit(
                name,
                fn,
                iterations=min(count or iterations, DEFAULT_ITERATIONS),
                warmup=min(warmup, 500),
                floor_ns=floor,
            )
            tails[name] = {
                "p95_ns": unbatched.p95_ns,
                "p99_ns": unbatched.p99_ns,
                "median_ns": unbatched.median_ns,
                "iterations": unbatched.iterations,
            }

    # M1 — the clock, decomposed. It is on the hot path of every recorded activity, and the
    # decomposition is the actionable part: reading the clock is cheap and rendering the
    # result as ISO-8601 text is not. A library that wants a faster hot path has to change
    # what it stores, not how it measures.
    measure("M1  now_iso()", now_iso, batch=FAST_BATCH)
    measure(
        "M1a   datetime.now(UTC) alone",
        lambda: datetime.datetime.now(datetime.UTC),
        batch=FAST_BATCH,
    )
    instant = datetime.datetime.now(datetime.UTC)
    measure("M1b   .isoformat() alone", instant.isoformat, batch=FAST_BATCH)

    # M2/M3 — entity registration, plain and carrying a store pointer.
    measure(
        "M2  register_entity()",
        lambda: tracker.register_entity("TaskEntity", "the task"),
        batch=FAST_BATCH,
    )
    pointer = {
        "yp:pid": "21.T11999/yprovstore-abcdef012345",
        "yp:contentHash": "sha256:" + "a" * 64,
    }
    measure(
        "M3  register_entity(pid=, content_hash=)",
        lambda: tracker.register_entity("TaskEntity", "the task", **ptr_kwargs(pointer)),
        batch=FAST_BATCH,
    )

    # M4–M6 — the core call, then the headline field, then typed attributes. M5 − M4 is
    # what `yp:justification` costs to record; M6 − M4 is what a typed attribute set costs.
    t0, t1 = now_iso(), now_iso()
    measure(
        "M4  record_activity() minimal",
        lambda: tracker.record_activity("ToolCallActivity", "step", t0, t1),
        batch=FAST_BATCH,
    )
    measure(
        "M5  record_activity(justification=)",
        lambda: tracker.record_activity(
            "ToolCallActivity", "step", t0, t1, justification=SAMPLE_JUSTIFICATION
        ),
        batch=FAST_BATCH,
    )
    measure(
        "M6  record_activity(extra_attrs=)",
        lambda: tracker.record_activity(
            "ToolCallActivity", "step", t0, t1, extra_attrs=dict(SAMPLE_ATTRS)
        ),
        batch=FAST_BATCH,
    )

    # M12 — what the ergonomic API adds over the primitives (PLAN.md R4). Measured as a
    # pair so the difference is the wrapper and nothing else.
    measure("M12 one step via the capture API", lambda: _api_step(tracker), batch=FAST_BATCH)
    measure("M12 one step via the primitives", lambda: _raw_step(tracker), batch=FAST_BATCH)

    # M7/M8 — serialisation of a six-activity record, and the bytes going out.
    populated = _populated_tracker()
    measure("M7  to_prov_json() (6 activities)", populated.to_prov_json, count=2_000)
    document = populated.to_prov_json()
    measure(
        "M8  json.dumps(document)",
        lambda: json.dumps(document, ensure_ascii=False),
        count=2_000,
    )

    # M9–M11 — the storage layer: hash and write, file write, and the round-trip read that
    # Phase 1 turned from a no-op into a real disk read.
    with tempfile.TemporaryDirectory() as temporary:
        store = LightweightStore(root=str(Path(temporary) / "store"))
        payload = json.dumps(document, ensure_ascii=False)
        counter = iter(range(IO_ITERATIONS + warmup + 10))
        measure(
            "M9  store.put(payload)",
            lambda: store.put(f"{payload}{next(counter)}", kind="bench"),
            count=IO_ITERATIONS,
        )
        stored = store.put(payload, kind="bench")
        measure(
            "M11 store.resolve(pid, verify=True)",
            lambda: store.resolve(stored["yp:pid"]),
            count=IO_ITERATIONS,
        )

        prov_dir = Path(temporary) / "out"
        prov_dir.mkdir(parents=True, exist_ok=True)

        def write_once() -> None:
            (prov_dir / "bench__m10_prov.json").write_text(payload, encoding="utf-8")

        measure("M10 write_prov() (file write)", write_once, count=IO_ITERATIONS)

    payload_out = {
        "level": "micro",
        "environment": asdict(capture_environment(interpreter_clean=python_is_optimised())),
        "timer_floor_ns": floor,
        "fast_batch": FAST_BATCH,
        "iterations": iterations,
        "warmup": warmup,
        "timings": _merge_tails(timings_to_json(results), tails),
        "derived": _derived_micro_figures(results, _paired_differences(tracker)),
    }
    write_results(out_dir / "micro.json", payload_out)
    return payload_out


def _paired_differences(tracker: ProvTracker) -> dict[str, Difference]:
    """The three differences the paper quotes, each measured by interleaving its pair.

    These are the figures a reader will check, so they get the careful method: the variant
    and its baseline run back to back inside one round, and the difference is taken within
    the round rather than between two separate measurements.
    """
    t0, t1 = now_iso(), now_iso()
    return {
        "cost_of_justification": paired_difference(
            "record_activity(justification=)",
            lambda: tracker.record_activity(
                "ToolCallActivity", "step", t0, t1, justification=SAMPLE_JUSTIFICATION
            ),
            "record_activity() minimal",
            lambda: tracker.record_activity("ToolCallActivity", "step", t0, t1),
        ),
        "cost_of_extra_attributes": paired_difference(
            "record_activity(extra_attrs=)",
            lambda: tracker.record_activity(
                "ToolCallActivity", "step", t0, t1, extra_attrs=dict(SAMPLE_ATTRS)
            ),
            "record_activity() minimal",
            lambda: tracker.record_activity("ToolCallActivity", "step", t0, t1),
        ),
        "cost_of_the_ergonomic_wrapper": paired_difference(
            "one step via the capture API",
            lambda: _api_step(tracker),
            "one step via the primitives",
            lambda: _raw_step(tracker),
        ),
        # Paired for the same reason: subtracting two separate measurements of operations
        # this close together produced a formatting share of 105.6% of the clock, which is
        # impossible and is a measurement of the gap between the two runs.
        "cost_of_formatting_an_instant": paired_difference(
            "now_iso()",
            now_iso,
            "datetime.now(UTC)",
            lambda: datetime.datetime.now(datetime.UTC),
        ),
    }


def _merge_tails(
    rows: list[dict[str, Any]], tails: dict[str, dict[str, float]]
) -> list[dict[str, Any]]:
    """Attach each batched row's separately measured tail, labelled as such."""
    merged = []
    for row in rows:
        tail = tails.get(row["name"])
        merged.append(row if tail is None else row | {"unbatched": tail})
    return merged


def _api_step(tracker: ProvTracker) -> None:
    with tracker.activity("ToolCallActivity", "step") as prov:
        prov.produces("ToolResultEntity", "result")


def _raw_step(tracker: ProvTracker) -> None:
    entity = tracker.register_entity("ToolResultEntity", "result")
    tracker.record_activity("ToolCallActivity", "step", now_iso(), now_iso(), out_eid=entity)


def _derived_micro_figures(
    results: list[Timing], differences: dict[str, Difference]
) -> dict[str, Any]:
    """The figures the paper quotes, computed from the measurements rather than by hand.

    The three differences come from :func:`~eval.bench_support.paired_difference` and carry
    whether they are *resolvable* — whether the difference exceeds the variation between
    the paired rounds it was measured over. For the cost of ``yp:justification`` that is
    not a technicality: if the answer is "below what this machine can measure", then that
    is the result, and it is the result E6 §9 predicted.
    """
    by_name = {timing.name: timing for timing in results}

    def median(name: str) -> float:
        return by_name[name].median_ns

    tracked_step = median("M4  record_activity() minimal") + median("M1  now_iso()")
    clock_share = 100.0 * median("M1  now_iso()") / tracked_step
    # Reported as an absolute against the clock read it formats, not as a share of
    # `now_iso()`. A share divides a paired measurement by a separately measured median
    # and inherits that median's noise: across runs it came out at 86.6% and then 37.3%,
    # while the paired figure it is built from moved by 6%.
    formatting = differences["cost_of_formatting_an_instant"]
    clock_read = median("M1a   datetime.now(UTC) alone")
    session = (
        LIFECYCLE_STEPS * tracked_step
        + LIFECYCLE_STEPS * median("M2  register_entity()")
        + median("M7  to_prov_json() (6 activities)")
    )
    return {
        **{name: value.as_json() for name, value in differences.items()},
        "cost_of_one_tracked_step_ns": tracked_step,
        "clock_share_of_a_tracked_step_ns": median("M1  now_iso()"),
        "clock_share_of_a_tracked_step_pct": clock_share,
        "clock_read_alone_ns": clock_read,
        "formatting_over_clock_read": formatting.delta_ns / clock_read if clock_read else None,
        "cost_of_a_six_step_session_ns": session,
        # The justification *text*, not what it costs a record. The modes level reports a
        # key of the same name meaning the latter — key, quoting and escaping included —
        # and that is the larger, quotable figure. Two measurements, one name; the report
        # distinguishes them in prose rather than renaming a key that archived results
        # already carry.
        "bytes_per_justification": len(SAMPLE_JUSTIFICATION.encode("utf-8")),
    }


# ══════════════════════════════════════════════════════════════════════════════════
#  Level 2 — per-agent, under the three conditions
# ══════════════════════════════════════════════════════════════════════════════════
@contextmanager
def condition(name: str) -> Iterator[None]:
    """Install one instrumentation condition for the duration of a block.

    OFF and A both run the agents on a null tracker; A additionally installs the callback
    baseline on the observation hook. B is the shipped configuration. Selected through the
    same environment variable a deployment would use, so what is measured is the code that
    ships rather than a benchmark-only path.
    """
    import os

    from eval.baseline import DEFAULT_TYPE_MAP, CallbackLogger
    from yprov4agenticai.observer import set_default_observer

    previous = {
        key: os.environ.get(key) for key in ("PROV_INSTRUMENTATION", "PROV_PERSISTENCE")
    }
    previous_observer = set_default_observer(None)

    if name in ("OFF", "A"):
        os.environ["PROV_INSTRUMENTATION"] = "off"
    else:
        os.environ.pop("PROV_INSTRUMENTATION", None)
    if name == "B-TRANS":
        os.environ["PROV_PERSISTENCE"] = "TRANSIENT"
    else:
        os.environ.pop("PROV_PERSISTENCE", None)
    if name == "A":
        set_default_observer(CallbackLogger(type_map=DEFAULT_TYPE_MAP).record)
    try:
        yield
    finally:
        set_default_observer(previous_observer)
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value


#: Modules that bind OUT_DIR at import time and therefore have to be rebound together.
_OUTPUT_BOUND_MODULES = (
    "runtime.util",
    "runtime.agents.ca1_storage",
    "runtime.agents.ca2_metrics",
    "runtime.agents.ca3_analytics",
    "runtime.agents.cax_summariser",
)


def _redirect_output(root: Path) -> None:
    """Point every module that writes a record at a temporary directory."""
    import importlib

    for module_name in _OUTPUT_BOUND_MODULES:
        setattr(importlib.import_module(module_name), "OUT_DIR", root)  # noqa: B010


def _producer_record() -> dict[str, Any]:
    """A real producer document, from the data the demonstrator replays."""
    candidates = sorted(Path("data/yprov4ml").glob("**/*.json"))
    if not candidates:
        raise SystemExit("no yProv4ML records under data/yprov4ml — nothing to measure against")
    return json.loads(candidates[0].read_text(encoding="utf-8"))


def level_agent(repetitions: int, out_dir: Path) -> dict[str, Any]:
    """Time each agent's full ``process()`` under OFF, A and B.

    The ratio result. Every agent runs on the same real producer record, with its output
    directed at a temporary directory so the measurement includes the file write an agent
    actually performs.
    """
    from runtime.agents.ca1_storage import CA1Storage
    from runtime.agents.ca2_metrics import CA2Metrics
    from runtime.agents.ca3_analytics import CA3Analytics
    from runtime.agents.cax_summariser import CAxSummariser
    from runtime.agents.mc1_compliance import MC1Compliance
    from runtime.agents.mc3_audit import MC3Audit

    document = _producer_record()
    agents: dict[str, tuple[type, str]] = {
        "CA3 Analytics (heuristic)": (CA3Analytics, "producer.yprov4ml.bench"),
        "CA2 Metrics": (CA2Metrics, "producer.yprov4ml.bench"),
        "CAx Summariser (computed)": (CAxSummariser, "producer.yprov4ml.bench"),
        "MC1 Compliance": (MC1Compliance, "agent.lifecycle.cax.bench"),
        "MC3 Audit": (MC3Audit, "agent.lifecycle.cax.bench"),
    }

    results: dict[str, Any] = {}
    with tempfile.TemporaryDirectory() as temporary:
        root = Path(temporary)
        # OUT_DIR is bound at import time by every module that writes a record, so
        # redirecting the benchmark's output means rebinding it in each of them. The
        # environment variable is read at import and these modules are already imported.
        _redirect_output(root)

        for label, (agent_class, subject) in agents.items():
            agent = agent_class(None)

            def run(condition_name: str, agent: Any = agent, subject: str = subject) -> float:
                with condition(condition_name):
                    started = time.perf_counter_ns()
                    agent.process("bench", document, subject)
                    return float(time.perf_counter_ns() - started)

            results[label] = _summarise_agent(
                interleaved(CONDITIONS, run, repetitions),
                repetitions,
                _output_bytes(agent, document, subject),
            )

        # CA1 is measured separately: it persists on a quorum or a timeout rather than on
        # receipt, so `process()` is not its unit of work — `_do_persist` is.
        results["CA1 Storage (persist path)"] = _measure_ca1(CA1Storage, document, repetitions)

    payload = {
        "level": "agent",
        "environment": asdict(capture_environment(interpreter_clean=python_is_optimised())),
        "repetitions": repetitions,
        "agents": results,
    }
    write_results(out_dir / "agent.json", payload)
    return payload


def _summarise_agent(
    samples: dict[str, list[float]], repetitions: int, output_bytes: int, **extra: Any
) -> dict[str, Any]:
    """Reduce one agent's samples, reporting a ratio only where one is resolvable.

    Every overhead figure carries whether the difference it describes exceeds the noise in
    the samples it came from. Without that, an agent whose own work is three orders of
    magnitude larger than its instrumentation produces a number that looks like a result
    and is a measurement of the scheduler.
    """
    medians = {name: statistics.median(values) for name, values in samples.items()}

    def ratio(baseline: str) -> dict[str, Any]:
        return {
            "pct": relative_overhead(medians[baseline], medians["B"]),
            "resolvable": resolvable(samples[baseline], samples["B"]),
            "noise_ns": max(dispersion(samples[baseline]), dispersion(samples["B"])),
        }

    return {
        "median_ns": medians,
        "p95_ns": {
            name: sorted(values)[min(int(0.95 * len(values)), len(values) - 1)]
            for name, values in samples.items()
        },
        "dispersion_ns": {name: dispersion(values) for name, values in samples.items()},
        "vs_off": ratio("OFF"),
        "vs_a": ratio("A"),
        # What the record costs to build, separated from what it costs to store: B-TRANS
        # builds and returns the document and writes nothing.
        "build_only_pct_vs_off": relative_overhead(medians["OFF"], medians["B-TRANS"]),
        "write_cost_ns": medians["B"] - medians["B-TRANS"],
        "output_bytes": output_bytes,
        "repetitions": repetitions,
        # Every sample, in round order. Conditions were interleaved, so sample i of each
        # condition comes from the same round, which is what lets eval.equivalence pair
        # them and bound a difference rather than only failing to resolve one.
        "samples_ns": samples,
        **extra,
    }


def _output_bytes(agent: Any, document: dict[str, Any], subject: str) -> int:
    """How many bytes of PROV-JSON this agent emits per record, under condition B."""
    with condition("B"):
        emitted, _ = agent.process("bytes", document, subject)
    return len(json.dumps(emitted, ensure_ascii=False).encode("utf-8"))


def _measure_ca1(
    agent_class: type, document: dict[str, Any], repetitions: int
) -> dict[str, Any]:
    """Time CA1's persist path, which is where its work and its instrumentation both are."""
    agent = agent_class(None)

    def run(condition_name: str) -> float:
        with condition(condition_name):
            started = time.perf_counter_ns()
            agent._do_persist("bench", document, ["ca2", "ca3", "cax"], "quorum", "benchmark")
            return float(time.perf_counter_ns() - started)

    samples = interleaved(CONDITIONS, run, repetitions)
    with condition("B"):
        emitted, _ = agent._do_persist(
            "bytes", document, ["ca2", "ca3", "cax"], "quorum", "benchmark"
        )
    return _summarise_agent(
        samples,
        repetitions,
        len(json.dumps(emitted, ensure_ascii=False).encode("utf-8")),
        note="persist path (_do_persist), not process(): CA1 persists on quorum or timeout",
    )


# ══════════════════════════════════════════════════════════════════════════════════
#  §7 — the scaling study
# ══════════════════════════════════════════════════════════════════════════════════
def level_scaling(
    out_dir: Path, sizes: tuple[int, ...] = (10, 100, 1_000, 10_000)
) -> dict[str, Any]:
    """Does the cost of a record stay linear in the size of the graph?

    ``docs/E6_OVERHEAD.md`` §7 asks for the complexity to be stated explicitly, because a
    long-lived agent accumulates activities and entities and ``to_prov_json`` walks all of
    them. It also named the suspect: the lineage loops that built a chain over the entity
    order. Phase 1 removed those, so this sweep now characterises the corrected tracker —
    which is the only version worth fitting a curve to.
    """
    import tracemalloc

    rows: list[dict[str, Any]] = []
    for size in sizes:
        tracker = _populated_tracker(size)

        def build_once(steps: int = size) -> ProvTracker:
            return _populated_tracker(steps)

        build = timeit(
            f"build {size}",
            build_once,
            iterations=3 if size >= 1_000 else 20,
            warmup=1,
        )
        serialise = timeit(
            f"to_prov_json {size}",
            tracker.to_prov_json,
            iterations=3 if size >= 1_000 else 50,
            warmup=1,
        )
        tracemalloc.start()
        held = _populated_tracker(size)
        _, peak = tracemalloc.get_traced_memory()
        tracemalloc.stop()
        document = held.to_prov_json()
        rows.append(
            {
                "activities": size,
                "entities": size + 1,
                "build_ns": build.median_ns,
                "to_prov_json_ns": serialise.median_ns,
                "peak_bytes": peak,
                "output_bytes": len(json.dumps(document, ensure_ascii=False).encode("utf-8")),
                "relations": sum(len(document.get(key) or {}) for key in RELATION_KEYS),
            }
        )

    activities = [float(row["activities"]) for row in rows]
    payload = {
        "level": "scaling",
        "environment": asdict(capture_environment(interpreter_clean=python_is_optimised())),
        "rows": rows,
        "exponents": {
            "build": fit_exponent(activities, [row["build_ns"] for row in rows]),
            "to_prov_json": fit_exponent(activities, [row["to_prov_json_ns"] for row in rows]),
            "peak_memory": fit_exponent(activities, [row["peak_bytes"] for row in rows]),
            "output_bytes": fit_exponent(activities, [row["output_bytes"] for row in rows]),
        },
    }
    write_results(out_dir / "scaling.json", payload)
    return payload


# ══════════════════════════════════════════════════════════════════════════════════
#  E6c — the tunable modes
# ══════════════════════════════════════════════════════════════════════════════════
#: Iterations for the mode comparison. The whole measured path is tens of microseconds, so
#: a few hundred samples leaves the median inside the noise — the first attempt at this
#: table reported METADATA as *slower* than DATA, which is impossible by construction and
#: was entirely scheduler jitter.
MODE_ITERATIONS = 3_000


def level_modes(out_dir: Path, iterations: int = MODE_ITERATIONS) -> dict[str, Any]:
    """What each mode buys and what it costs: time, bytes, and what stays in the record.

    Measured through ``emit()`` and a real sink rather than through ``to_prov_json()``,
    because persistence is a property of handing the document over. Timing the serialiser
    alone would make B-TRANS indistinguishable from B-DATA and hide the only thing that
    mode does.

    The output-size column is the one that matters most. At HPC rates the storage and bus
    cost of provenance binds before its CPU cost does, which makes "how many bytes" a more
    useful knob than "how many microseconds" — and unlike the timings, bytes are exact.
    """
    floor = timer_floor()
    modes: dict[str, dict[str, Any]] = {}

    def build_record(granularity: str, persistence: str, justify: bool, sink: Any) -> Any:
        tracker = ProvTracker(
            sid="bench",
            granularity=granularity,
            persistence=persistence,
            agent_label="Bench_Agent",
            sink=sink,
            verbose=False,
        )
        previous = tracker.register_entity("TaskEntity", "the task")
        for index in range(LIFECYCLE_STEPS):
            produced = tracker.register_entity(
                "ToolResultEntity", f"result {index}", suffix=f"_{index}", derived_from=previous
            )
            tracker.record_activity(
                "ToolCallActivity",
                f"step {index}",
                now_iso(),
                now_iso(),
                in_eid=previous,
                out_eid=produced,
                justification=SAMPLE_JUSTIFICATION if justify else None,
            )
            previous = produced
        return tracker

    specification = {
        "B-DATA (full)": ("DATA", "PERSISTENT", True),
        "B-META (activities only)": ("METADATA", "PERSISTENT", True),
        "B-TRANS (nothing written)": ("DATA", "TRANSIENT", True),
        "B-NOJUST (no justification)": ("DATA", "PERSISTENT", False),
    }

    with tempfile.TemporaryDirectory() as temporary:
        target = Path(temporary) / "record_prov.json"

        def sink(document: dict[str, Any]) -> None:
            target.write_text(json.dumps(document, ensure_ascii=False), encoding="utf-8")

        for label, (granularity, persistence, justify) in specification.items():

            def build_and_emit(
                g: str = granularity, p: str = persistence, j: bool = justify
            ) -> dict[str, Any]:
                return build_record(g, p, j, sink).emit()

            timing = timeit(
                label, build_and_emit, iterations=iterations, warmup=200, floor_ns=floor
            )
            document = build_record(granularity, persistence, justify, None).to_prov_json()
            modes[label] = {
                "median_ns": timing.median_ns,
                "p95_ns": timing.p95_ns,
                "output_bytes": len(json.dumps(document, ensure_ascii=False).encode("utf-8")),
                "activities": len(document.get("activity") or {}),
                "entities": len(document.get("entity") or {}),
                "relations": sum(len(document.get(key) or {}) for key in RELATION_KEYS),
                "justifications": sum(
                    1
                    for activity in (document.get("activity") or {}).values()
                    if str(activity.get("yp:justification", "")).strip()
                ),
            }

    full = modes["B-DATA (full)"]
    for row in modes.values():
        row["bytes_vs_full_pct"] = (
            100.0 * (row["output_bytes"] - full["output_bytes"]) / full["output_bytes"]
        )
        row["time_vs_full_pct"] = (
            100.0 * (row["median_ns"] - full["median_ns"]) / full["median_ns"]
        )
    justification_bytes = (
        full["output_bytes"] - modes["B-NOJUST (no justification)"]["output_bytes"]
    )
    payload = {
        "level": "modes",
        "environment": asdict(capture_environment(interpreter_clean=python_is_optimised())),
        "iterations": iterations,
        "modes": modes,
        "bytes_per_justification": justification_bytes / max(full["justifications"], 1),
        "justifications_in_full_mode": full["justifications"],
        "justification_share_of_a_record_pct": 100.0
        * justification_bytes
        / full["output_bytes"],
    }
    write_results(out_dir / "modes.json", payload)
    return payload


# ══════════════════════════════════════════════════════════════════════════════════
#  Level 3 — the whole pipeline
# ══════════════════════════════════════════════════════════════════════════════════
#: How long CA1 waits for consumer acknowledgements during a benchmark run. Short, because
#: the wait is a deliberate architectural delay rather than work, and timing it would
#: measure the timeout constant.
PIPELINE_PERSIST_TIMEOUT = 2

#: Environment per condition. OFF and A disable the explicit layer; A additionally attaches
#: the callback baseline, which `run_local.py --baseline` installs on the observation hook.
PIPELINE_CONDITIONS: dict[str, dict[str, str]] = {
    "OFF": {"PROV_INSTRUMENTATION": "off"},
    "A": {"PROV_INSTRUMENTATION": "off", "YPROV_BASELINE": "1"},
    "B-TRANS": {"PROV_PERSISTENCE": "TRANSIENT"},
    "B": {},
}


def level_pipeline(out_dir: Path, repetitions: int = 3) -> dict[str, Any]:
    """Run the six-agent pipeline end to end under each condition, in a fresh process.

    **Sequential and single-process, and the paper must say so.** ``run_local.py``'s bus
    awaits each handler inline in subscription order, so there is no concurrency here: the
    figure is a sequential sum, and a consumer's acknowledgement arrives from inside the
    producer's own publish call (``PLAN.md`` R8). It is still worth measuring — it isolates
    the instrumentation from broker and container noise — but any claim about *pipeline
    throughput* needs the NATS path, which this is not.

    A subprocess per repetition, for two reasons: the conditions cannot contaminate one
    another through module state, and peak memory is only meaningful for a process that
    did one thing.
    """
    import os
    import subprocess

    samples: dict[str, list[float]] = {name: [] for name in PIPELINE_CONDITIONS}
    artefacts: dict[str, dict[str, int]] = {}

    with tempfile.TemporaryDirectory() as temporary:
        for repetition in range(repetitions):
            for name, overrides in PIPELINE_CONDITIONS.items():
                run_dir = Path(temporary) / f"{name}-{repetition}"
                environment = {
                    **os.environ,
                    "OPENAI_API_KEY": "",
                    "STREAM_DELAY_MS": "0",
                    **overrides,
                }
                environment.pop("PROV_PERSISTENCE", None)
                environment.update(overrides)
                command = [
                    sys.executable,
                    "run_local.py",
                    "--out-dir",
                    str(run_dir),
                    "--persist-timeout",
                    str(PIPELINE_PERSIST_TIMEOUT),
                ]
                if overrides.get("YPROV_BASELINE"):
                    command.append("--baseline")

                started = time.perf_counter_ns()
                completed = subprocess.run(
                    command, env=environment, capture_output=True, text=True, check=False
                )
                elapsed = float(time.perf_counter_ns() - started)
                if completed.returncode != 0:
                    raise SystemExit(
                        f"pipeline run failed under {name}:\n{completed.stderr[-2000:]}"
                    )
                samples[name].append(elapsed)
                if name not in artefacts:
                    documents = sorted(run_dir.glob("*_prov.json"))
                    artefacts[name] = {
                        "documents": len(documents),
                        "bytes": sum(path.stat().st_size for path in documents),
                    }

    medians = {name: statistics.median(values) for name, values in samples.items()}
    # The pipeline replays whatever is in data/yprov4ml, and each record fans out to six
    # agents; records per second is reported against the input, not the fan-out.
    records = len(sorted(Path("data/yprov4ml").glob("**/*.json")))
    payload = {
        "level": "pipeline",
        "environment": asdict(capture_environment(interpreter_clean=python_is_optimised())),
        "topology": "sequential, single-process, in-process bus (PLAN.md R8)",
        "persist_timeout_s": PIPELINE_PERSIST_TIMEOUT,
        "repetitions": repetitions,
        "input_records": records,
        "median_ns": medians,
        "dispersion_ns": {name: dispersion(values) for name, values in samples.items()},
        "records_per_second": {
            name: records / (median / 1e9) if median else None
            for name, median in medians.items()
        },
        "artefacts": artefacts,
        "vs_off": {
            "pct": relative_overhead(medians["OFF"], medians["B"]),
            "resolvable": resolvable(samples["OFF"], samples["B"]),
            "noise_ns": max(dispersion(samples["OFF"]), dispersion(samples["B"])),
        },
        "caveat": (
            "Dominated by a fixed CA1 persist window and by process start-up, both of "
            "which are the same under every condition. Read the ratio, not the absolute."
        ),
    }
    write_results(out_dir / "pipeline.json", payload)
    return payload


# ══════════════════════════════════════════════════════════════════════════════════
def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.bench_overhead",
        description="Measure what instrumentation costs (docs/E6_OVERHEAD.md).",
    )
    parser.add_argument(
        "--level",
        default="all",
        choices=("micro", "agent", "scaling", "modes", "pipeline", "all"),
        help="which level to run (default: all)",
    )
    parser.add_argument("--out-dir", default="out/bench", help="where results land")
    parser.add_argument("--iterations", type=int, default=DEFAULT_ITERATIONS)
    parser.add_argument("--warmup", type=int, default=DEFAULT_WARMUP)
    parser.add_argument(
        "--repetitions", type=int, default=30, help="Level 2 reps per agent per condition"
    )
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir)
    levels = (
        ("micro", "agent", "scaling", "modes", "pipeline")
        if args.level == "all"
        else (args.level,)
    )

    if not python_is_optimised():
        print(
            "[bench] WARNING: a trace or profile hook is installed. These numbers measure "
            "the tool, not the library.",
            flush=True,
        )

    for level in levels:
        print(f"\n[bench] level: {level}", flush=True)
        if level == "micro":
            payload = level_micro(args.iterations, args.warmup, out_dir)
            print(
                f"[bench] timer floor {payload['timer_floor_ns']:.0f} ns; fast operations "
                f"batched {payload['fast_batch']}x (effective floor "
                f"{payload['timer_floor_ns'] / payload['fast_batch']:.1f} ns)"
            )
            for row in payload["timings"]:
                flag = "  <- near the floor" if row["near_floor"] else ""
                batch = f"  x{row['batch']}" if row["batch"] > 1 else ""
                print(f"  {row['name']:<44} {row['median_ns']:>10.1f} ns{batch}{flag}")
            print()
            for key, value in payload["derived"].items():
                if isinstance(value, dict):
                    verdict = (
                        f"{value['delta_ns']:>+10.1f} ns"
                        if value["resolvable"]
                        else (
                            f"{value['delta_ns']:>+.1f} ns, inside the "
                            f"±{value['noise_ns']:.1f} ns noise"
                        )
                    )
                    print(f"  = {key:<36} {verdict}")
                else:
                    print(f"  = {key:<36} {value:>10.1f}")
        elif level == "agent":
            payload = level_agent(args.repetitions, out_dir)
            header = (
                f"  {'agent':<28} {'OFF':>9} {'A':>9} {'B-TRANS':>9} {'B':>9} "
                f"{'B/OFF':>10} {'write':>9} {'bytes':>7}"
            )
            print(header)
            for label, row in payload["agents"].items():
                medians = row["median_ns"]
                overhead = row["vs_off"]
                ratio = (
                    f"{overhead['pct']:>+9.1f}%"
                    if overhead["resolvable"] and overhead["pct"] is not None
                    else "  in noise"
                )
                print(
                    f"  {label:<28} {medians['OFF'] / 1e6:>8.2f}m {medians['A'] / 1e6:>8.2f}m "
                    f"{medians['B-TRANS'] / 1e6:>8.2f}m {medians['B'] / 1e6:>8.2f}m "
                    f"{ratio:>10} {row['write_cost_ns'] / 1e6:>8.2f}m "
                    f"{row['output_bytes']:>7}"
                )
        elif level == "scaling":
            payload = level_scaling(out_dir)
            for row in payload["rows"]:
                print(
                    f"  {row['activities']:>6} activities  build "
                    f"{row['build_ns'] / 1e6:>9.2f} ms  to_prov_json "
                    f"{row['to_prov_json_ns'] / 1e6:>8.2f} ms  "
                    f"{row['output_bytes'] / 1024:>9.0f} KiB  "
                    f"peak {row['peak_bytes'] / 1024 / 1024:>7.1f} MiB"
                )
            for name, exponent in payload["exponents"].items():
                verdict = "—" if exponent is None else f"{exponent:.3f}"
                print(f"  exponent[{name}] = {verdict}")
        elif level == "modes":
            payload = level_modes(out_dir)
            print(
                f"  {'mode':<30} {'time':>10} {'vs full':>9} {'bytes':>7} {'vs full':>9}  just."
            )
            for label, row in payload["modes"].items():
                print(
                    f"  {label:<30} {row['median_ns'] / 1000:>7.1f} µs "
                    f"{row['time_vs_full_pct']:>+8.1f}% {row['output_bytes']:>7} "
                    f"{row['bytes_vs_full_pct']:>+8.1f}%  {row['justifications']}"
                )
            print(
                f"  {payload['bytes_per_justification']:.0f} bytes per justification, "
                f"{payload['justification_share_of_a_record_pct']:.1f}% of a record"
            )

        elif level == "pipeline":
            payload = level_pipeline(out_dir)
            print(f"  topology: {payload['topology']}")
            for name, median in payload["median_ns"].items():
                rate = payload["records_per_second"][name]
                written = payload["artefacts"][name]
                print(
                    f"  {name:<10} {median / 1e9:>7.2f} s  {rate:>6.2f} records/s  "
                    f"{written['documents']:>3} documents  "
                    f"{written['bytes'] / 1024:>7.1f} KiB written"
                )
            overhead = payload["vs_off"]
            verdict = f"{overhead['pct']:+.1f}%" if overhead["resolvable"] else "in noise"
            print(f"  B vs OFF: {verdict}")

    print(f"\n[bench] results in {out_dir}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
