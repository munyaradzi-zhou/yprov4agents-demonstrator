"""Measurement apparatus for the overhead study: timing, statistics, provenance of a run.

Three things here, and the third is the one that makes the numbers worth printing.

**Timing that knows its own resolution.** Several operations under test cost a microsecond
or less, which is within an order of magnitude of what it costs to read the clock twice.
:func:`timer_floor` measures that cost on this machine, every run, and every
:class:`Timing` carries it — so a figure that is really a measurement of the timer says so
instead of being read as a measurement of the library. A benchmark that reports 180 ns
without reporting that its floor is 150 ns is not reporting anything.

**Robust statistics.** Median as the headline, p95 and p99 for the tail. A mean over
wall-clock samples on a scheduled operating system is a measurement of the scheduler;
the median is the number that transfers.

**Environment capture.** Machine, OS, interpreter, git commit, whether an LLM was
involved. Absolute nanosecond figures are machine-specific, so a results file that does not
say which machine it came from cannot be compared with anything, including itself a month
later (``docs/E6_OVERHEAD.md`` §8).
"""

from __future__ import annotations

import json
import os
import platform
import statistics
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Sequence
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

#: Iterations and warm-up for a microbenchmark. The warm-up exists because the first calls
#: pay for import-time laziness, branch prediction and allocator growth that a steady-state
#: figure should not carry.
DEFAULT_ITERATIONS = 10_000
DEFAULT_WARMUP = 1_000

#: A measurement within this multiple of the timer floor is reported as near-floor: the
#: figure is an upper bound on the operation's cost, not an estimate of it.
FLOOR_MULTIPLE = 3.0


@dataclass(frozen=True)
class Timing:
    """The distribution of one operation's cost, in nanoseconds."""

    name: str
    median_ns: float
    mean_ns: float
    p95_ns: float
    p99_ns: float
    min_ns: float
    iterations: int
    floor_ns: float
    #: Calls per timed sample. Above 1, each sample is a batch mean and the per-call floor
    #: falls by the same factor — which is how an operation costing less than a clock read
    #: becomes measurable at all.
    batch: int = 1
    #: Median absolute deviation of the samples. This, not the timer floor, is what a
    #: *difference* between two measurements has to beat: batching lowers the resolution
    #: limit of the clock and does nothing about the run-to-run variance of a loaded
    #: machine. Comparing a 50 ns difference against a 0.2 ns batched floor and calling it
    #: resolved is how a benchmark reports noise as a finding.
    mad_ns: float = 0.0

    @property
    def effective_floor_ns(self) -> float:
        """The floor as it applies to this figure, after batching."""
        return self.floor_ns / self.batch

    @property
    def near_floor(self) -> bool:
        """Whether this operation is too fast for the clock to resolve honestly."""
        return self.median_ns < FLOOR_MULTIPLE * self.effective_floor_ns

    @property
    def resolves(self) -> bool:
        """Whether a *difference* of this size could be distinguished from zero."""
        return self.median_ns >= self.effective_floor_ns

    @property
    def median_us(self) -> float:
        return self.median_ns / 1_000.0

    def __str__(self) -> str:
        flag = "  (near timer floor)" if self.near_floor else ""
        return f"{self.name:<44} {self.median_ns:>10.1f} ns{flag}"


def dispersion(samples: Sequence[float]) -> float:
    """Spread of a sample set, as the median absolute deviation from its median.

    Robust to the outliers a scheduled operating system produces, where the standard
    deviation is not: one 50 ms scheduling hiccup in thirty samples moves a standard
    deviation a long way and a MAD hardly at all.
    """
    if len(samples) < 2:
        return 0.0
    centre = statistics.median(samples)
    return statistics.median([abs(value - centre) for value in samples])


def _summarise(name: str, samples: list[float], floor_ns: float, batch: int = 1) -> Timing:
    ordered = sorted(samples)
    count = len(ordered)
    return Timing(
        name=name,
        median_ns=float(statistics.median(ordered)),
        mean_ns=statistics.fmean(ordered),
        p95_ns=float(ordered[min(int(0.95 * count), count - 1)]),
        p99_ns=float(ordered[min(int(0.99 * count), count - 1)]),
        min_ns=float(ordered[0]),
        iterations=count * batch,
        floor_ns=floor_ns,
        batch=batch,
        mad_ns=dispersion(ordered),
    )


def timer_floor(iterations: int = DEFAULT_ITERATIONS) -> float:
    """Median cost of taking one measurement: two clock reads around an empty callable.

    This is the noise floor of every other figure in the study. Measured per run rather
    than assumed, because it depends on the platform's clock source — a Windows host
    reading ``QueryPerformanceCounter`` and a Linux host reading ``CLOCK_MONOTONIC`` do not
    have the same floor.
    """

    def nothing() -> None:
        return None

    samples: list[int] = []
    for _ in range(iterations):
        started = time.perf_counter_ns()
        nothing()
        samples.append(time.perf_counter_ns() - started)
    return float(statistics.median(samples))


def timeit(
    name: str,
    fn: Callable[[], Any],
    *,
    iterations: int = DEFAULT_ITERATIONS,
    warmup: int = DEFAULT_WARMUP,
    floor_ns: float | None = None,
    setup: Callable[[], None] | None = None,
    batch: int = 1,
) -> Timing:
    """Time ``fn`` repeatedly and return the distribution of its cost.

    Parameters
    ----------
    setup
        Called before each timed iteration and *not* included in the measurement. For
        operations that mutate state — a tracker that accumulates activities — this is what
        keeps iteration 10,000 measuring the same thing as iteration 1.
    floor_ns
        The timer floor, measured once per run and passed in rather than re-measured per
        operation.
    batch
        Calls per timed sample. The default of 1 gives a true per-call distribution, with
        p95 and p99 that mean something. Above 1, one clock pair spans ``batch`` calls and
        each sample is their mean: the per-call floor drops by that factor, which is the
        only way to measure an operation that costs less than reading the clock twice. The
        price is that the tail is a tail of batch means rather than of calls, so it is
        narrower than reality — use batching for the median and read p99 from an unbatched
        measurement.

        Not available with ``setup``: a batch would amortise the setup over its calls
        without timing it, which is a different and much more confusing measurement.
    """
    if batch > 1 and setup is not None:
        raise ValueError("batch and setup cannot be combined: see the docstring")
    floor = timer_floor() if floor_ns is None else floor_ns
    for _ in range(warmup):
        if setup is not None:
            setup()
        fn()

    samples: list[float] = []
    if batch == 1:
        for _ in range(iterations):
            if setup is not None:
                setup()
            started = time.perf_counter_ns()
            fn()
            samples.append(float(time.perf_counter_ns() - started))
    else:
        rounds = max(iterations // batch, 1)
        span = range(batch)
        for _ in range(rounds):
            started = time.perf_counter_ns()
            for _ in span:
                fn()
            samples.append((time.perf_counter_ns() - started) / batch)
    return _summarise(name, samples, floor, batch)


@dataclass(frozen=True)
class Difference:
    """The cost of one variant over another, measured under matched conditions."""

    name: str
    baseline: str
    delta_ns: float
    noise_ns: float
    rounds: int
    batch: int

    @property
    def resolvable(self) -> bool:
        """Whether the difference exceeds the variation between paired rounds."""
        return abs(self.delta_ns) > self.noise_ns

    def as_json(self) -> dict[str, Any]:
        return {
            "of": self.name,
            "over": self.baseline,
            "delta_ns": self.delta_ns,
            "noise_ns": self.noise_ns,
            "rounds": self.rounds,
            "batch": self.batch,
            "resolvable": self.resolvable,
        }


def paired_difference(
    name: str,
    variant: Callable[[], Any],
    baseline_name: str,
    baseline: Callable[[], Any],
    *,
    rounds: int = 40,
    batch: int = 1_000,
    warmup: int = 200,
) -> Difference:
    """Time two variants alternately and report the difference between them.

    Subtracting one whole measurement from another does not work for a difference this
    small. The two runs happen at different moments, under different cache, scheduler and
    frequency conditions, and on this machine the cost of adding a justification to
    ``record_activity`` came out at +52 ns, −30 ns and +201 ns on three consecutive
    attempts — a spread far larger than the quantity being measured, and with an unstable
    sign.

    Interleaving fixes it the way it fixes the agent-level conditions: one round times the
    baseline and the variant back to back, so whatever the machine was doing applies to
    both, and the difference is taken *within* the round. The reported delta is the median
    of those per-round differences and the noise is their dispersion. If the delta does not
    exceed the noise, the honest answer is that the difference is below what this machine
    can measure — which, for a dictionary insertion, is the expected and informative
    result.
    """
    for _ in range(warmup):
        baseline()
        variant()

    deltas: list[float] = []
    span = range(batch)
    for _ in range(rounds):
        started = time.perf_counter_ns()
        for _ in span:
            baseline()
        middle = time.perf_counter_ns()
        for _ in span:
            variant()
        finished = time.perf_counter_ns()
        deltas.append(((finished - middle) - (middle - started)) / batch)

    return Difference(
        name=name,
        baseline=baseline_name,
        delta_ns=float(statistics.median(deltas)),
        noise_ns=dispersion(deltas),
        rounds=rounds,
        batch=batch,
    )


def interleaved(
    conditions: Sequence[str],
    run: Callable[[str], float],
    repetitions: int,
) -> dict[str, list[float]]:
    """Run conditions round-robin and collect their samples.

    Round-robin rather than in blocks, so that thermal drift, a background process or a
    CPU frequency change hits every condition equally instead of landing entirely on
    whichever one ran last (``docs/E6_OVERHEAD.md`` §8). Blocked ordering is the single
    easiest way to produce a convincing and completely wrong overhead figure.
    """
    samples: dict[str, list[float]] = {condition: [] for condition in conditions}
    for _ in range(repetitions):
        for condition in conditions:
            samples[condition].append(run(condition))
    return samples


@dataclass
class Environment:
    """Where a measurement came from. Without this the numbers are not reproducible."""

    captured_at: str
    python: str
    implementation: str
    platform: str
    machine: str
    processor: str
    cpu_logical: int | None
    cpu_physical: int | None
    memory_bytes: int | None
    git_commit: str | None
    git_dirty: bool | None
    llm_involved: bool
    energy_backend: str
    notes: dict[str, Any] = field(default_factory=dict)


def _git(*args: str) -> str | None:
    try:
        result = subprocess.run(
            ["git", *args], capture_output=True, text=True, timeout=10, check=False
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.strip() if result.returncode == 0 else None


def capture_environment(**notes: Any) -> Environment:
    """Describe the machine and the configuration this run happened under."""
    cpu_logical = cpu_physical = memory = None
    try:
        import psutil

        cpu_logical = psutil.cpu_count(logical=True)
        cpu_physical = psutil.cpu_count(logical=False)
        memory = psutil.virtual_memory().total
    except ImportError:  # pragma: no cover - psutil is an eval extra
        cpu_logical = os.cpu_count()

    return Environment(
        captured_at=datetime.now(UTC).isoformat(),
        python=platform.python_version(),
        implementation=platform.python_implementation(),
        platform=platform.platform(),
        machine=platform.machine(),
        processor=platform.processor(),
        cpu_logical=cpu_logical,
        cpu_physical=cpu_physical,
        memory_bytes=memory,
        git_commit=_git("rev-parse", "HEAD"),
        git_dirty=bool(_git("status", "--porcelain")),
        llm_involved=bool(os.environ.get("OPENAI_API_KEY")),
        energy_backend=os.environ.get("PROV_ENERGY", "off"),
        notes=notes,
    )


def write_results(path: Path, payload: dict[str, Any]) -> Path:
    """Write a results file, environment included, creating the directory if needed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    return path


def read_results(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def timings_to_json(timings: Iterator[Timing] | Sequence[Timing]) -> list[dict[str, Any]]:
    return [
        asdict(timing)
        | {
            "near_floor": timing.near_floor,
            "effective_floor_ns": timing.effective_floor_ns,
        }
        for timing in timings
    ]


def resolvable(
    baseline: Sequence[float], measured: Sequence[float], *, factor: float = 1.0
) -> bool:
    """Whether two sample sets differ by more than their own noise.

    The guard against reporting a measurement of the scheduler as a measurement of the
    library. An agent whose own work takes 160 ms and whose instrumentation takes 2 ms
    cannot have that 2 ms resolved from thirty samples — and this demonstrator produced
    exactly that case, reporting instrumentation as a 5% *speed-up* before this check
    existed. A difference inside the noise is reported as not resolvable, which is the
    true statement.
    """
    delta = abs(statistics.median(measured) - statistics.median(baseline))
    noise = max(dispersion(baseline), dispersion(measured))
    return delta > factor * noise


def relative_overhead(baseline: float, measured: float) -> float | None:
    """``(measured - baseline) / baseline`` as a percentage, or None when undefined.

    None rather than a large number when the baseline rounds to zero: a ratio against
    nothing is not an overhead figure, and printing one invites a reader to believe it.
    """
    if baseline <= 0:
        return None
    return 100.0 * (measured - baseline) / baseline


def fit_exponent(sizes: Sequence[float], costs: Sequence[float]) -> float | None:
    """Fit ``cost ≈ k · size^b`` and return *b*, the complexity exponent.

    A least-squares fit on log-log axes, which is the whole scaling study in one number:
    *b* ≈ 1 is linear, *b* ≈ 2 is quadratic. ``docs/E6_OVERHEAD.md`` §7 asks for the
    complexity to be stated explicitly rather than asserted, and this is the statement.

    Returns None when there is too little spread to fit, which is honest — three points
    over one decade cannot distinguish 1.0 from 1.2.
    """
    points = [
        (size, cost) for size, cost in zip(sizes, costs, strict=True) if size > 0 and cost > 0
    ]
    if len(points) < 3:
        return None
    import math

    xs = [math.log(size) for size, _ in points]
    ys = [math.log(cost) for _, cost in points]
    mean_x, mean_y = statistics.fmean(xs), statistics.fmean(ys)
    denominator = sum((x - mean_x) ** 2 for x in xs)
    if denominator == 0:
        return None
    return sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys, strict=True)) / denominator


def python_is_optimised() -> bool:
    """Whether this interpreter is running without assertions or tracing hooks.

    A benchmark run under a coverage tool or a debugger measures the tool. Reported in the
    results so a figure taken under one is not mistaken for a clean measurement.
    """
    return sys.gettrace() is None and sys.getprofile() is None
