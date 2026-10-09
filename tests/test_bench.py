"""The measurement apparatus, and the off switch it measures against.

A benchmark is the one kind of code whose bugs produce plausible numbers instead of
failures, so the harness needs tests more than most things here. Three properties matter:

* it reports its own resolution, and refuses to call a difference real when it is smaller
  than the noise it was measured against;
* ``NullTracker`` accepts everything ``ProvTracker`` accepts, because condition OFF is the
  denominator of every overhead figure and a shim that diverges measures the shim;
* condition A still *observes*, because a callback handler sees node boundaries whether or
  not explicit instrumentation is running.
"""

from __future__ import annotations

import inspect
from typing import Any

import pytest

from eval.bench_support import (
    Difference,
    dispersion,
    fit_exponent,
    paired_difference,
    relative_overhead,
    resolvable,
    timeit,
    timer_floor,
)
from yprov4agenticai import NullTracker, ProvTracker
from yprov4agenticai.null import NullScope
from yprov4agenticai.observer import StepEvent, observe


# ── the timer knows its own limits ────────────────────────────────────────────────
def test_the_timer_floor_is_measured_and_positive() -> None:
    """Every figure is reported against this, so it cannot be assumed."""
    floor = timer_floor(iterations=2_000)
    assert floor > 0
    assert floor < 1_000_000, "a millisecond to read the clock twice would be absurd"


def test_a_measurement_knows_when_it_is_at_the_floor() -> None:
    fast = timeit("nothing", lambda: None, iterations=2_000, warmup=100)
    assert fast.near_floor, "an empty callable must be reported as unresolvable"


def test_batching_lowers_the_effective_floor_by_its_factor() -> None:
    batched = timeit("nothing", lambda: None, iterations=2_000, warmup=100, batch=100)
    assert batched.batch == 100
    assert batched.effective_floor_ns == pytest.approx(batched.floor_ns / 100)


def test_batching_and_setup_are_refused_together() -> None:
    """A batch would amortise setup over its calls without timing it."""
    with pytest.raises(ValueError, match="batch and setup"):
        timeit("x", lambda: None, setup=lambda: None, batch=10, iterations=10)


def test_setup_runs_before_each_iteration_and_is_not_timed() -> None:
    calls: list[str] = []
    timeit(
        "x",
        lambda: calls.append("work"),
        setup=lambda: calls.append("setup"),
        iterations=5,
        warmup=0,
    )
    assert calls == ["setup", "work"] * 5


# ── a difference is only a difference if it beats the noise ───────────────────────
def test_a_paired_difference_detects_a_real_cost() -> None:
    def baseline() -> int:
        return sum(range(50))

    def variant() -> int:
        return sum(range(5_000))

    measured = paired_difference(
        "heavy", variant, "light", baseline, rounds=15, batch=200, warmup=50
    )
    assert measured.delta_ns > 0
    assert measured.resolvable


def test_a_paired_difference_of_identical_work_is_not_resolvable() -> None:
    """The guard that keeps noise out of the results tables.

    Two calls to the same function differ only by scheduling, so the harness must report
    the difference as unresolvable. If this ever passes as resolvable, every "cost of X"
    figure in the overhead study is suspect.
    """

    def work() -> int:
        return sum(range(500))

    measured = paired_difference(
        "same", work, "same again", work, rounds=25, batch=200, warmup=50
    )
    assert not measured.resolvable, (
        f"identical work reported a resolvable difference of {measured.delta_ns:.1f} ns "
        f"against noise of {measured.noise_ns:.1f} ns"
    )


def test_a_difference_serialises_with_its_verdict() -> None:
    difference = Difference("a", "b", delta_ns=100.0, noise_ns=10.0, rounds=5, batch=10)
    payload = difference.as_json()
    assert payload["resolvable"] is True
    assert payload["delta_ns"] == 100.0


# ── statistics ────────────────────────────────────────────────────────────────────
def test_dispersion_is_robust_to_one_outlier() -> None:
    """A scheduling hiccup must not widen the noise estimate it appears in."""
    clean = [10.0, 10.0, 11.0, 10.0, 11.0]
    with_spike = [*clean, 10_000.0]
    assert dispersion(with_spike) <= dispersion(clean) + 1.0


def test_resolvable_compares_a_delta_against_the_samples_own_spread() -> None:
    quiet = [100.0] * 10
    assert resolvable(quiet, [200.0] * 10)
    noisy = [100.0, 500.0, 100.0, 500.0, 100.0, 500.0]
    assert not resolvable(noisy, [300.0, 700.0, 300.0, 700.0, 300.0, 700.0])


def test_relative_overhead_refuses_a_ratio_against_zero() -> None:
    assert relative_overhead(0.0, 5.0) is None
    assert relative_overhead(100.0, 150.0) == pytest.approx(50.0)


@pytest.mark.parametrize(
    ("exponent", "expected"),
    [(1.0, 1.0), (2.0, 2.0), (0.5, 0.5)],
)
def test_the_complexity_fit_recovers_a_known_exponent(exponent: float, expected: float) -> None:
    """The scaling study's whole conclusion is this number, so it is checked against
    curves whose exponent is known."""
    sizes = [10.0, 100.0, 1_000.0, 10_000.0]
    costs = [3.0 * size**exponent for size in sizes]
    assert fit_exponent(sizes, costs) == pytest.approx(expected, abs=1e-6)


def test_the_complexity_fit_declines_when_there_is_too_little_to_fit() -> None:
    assert fit_exponent([10.0, 100.0], [1.0, 10.0]) is None


# ── the off switch ────────────────────────────────────────────────────────────────
def _public(cls: type) -> set[str]:
    return {
        name
        for name, member in inspect.getmembers(cls)
        if not name.startswith("_") and callable(member)
    }


def test_the_null_tracker_accepts_everything_the_real_one_does() -> None:
    """Condition OFF is the denominator of every overhead figure.

    If NullTracker lacks a method an agent calls, OFF is not the same agent with
    instrumentation removed — it is a crash, or worse, a different code path.
    """
    missing = _public(ProvTracker) - _public(NullTracker)
    assert not missing, f"NullTracker is missing: {sorted(missing)}"


def test_the_null_scope_accepts_everything_the_real_scope_does() -> None:
    from yprov4agenticai.api import ActivityScope

    missing = _public(ActivityScope) - _public(NullScope)
    assert not missing, f"NullScope is missing: {sorted(missing)}"


def test_the_null_tracker_builds_no_document() -> None:
    written: list[dict[str, Any]] = []
    tracker = NullTracker(sid="s", sink=written.append)
    with tracker.activity("ToolCallActivity", "step") as prov:
        entity = prov.produces("ToolResultEntity", "the payload")
        prov.note(**{"yp:anything": 1})
    tracker.decide("ACCEPT", "a justification", consumed=entity)

    assert tracker.emit() == {}
    assert tracker.to_prov_json() == {}
    assert written == [], "a disabled tracker must not reach its sink"


def test_the_null_tracker_still_refuses_an_unjustified_verdict() -> None:
    """A check a caller relies on must not depend on whether provenance is enabled."""
    tracker = NullTracker(sid="s")
    with pytest.raises(ValueError, match="justification"):
        tracker.decide("ACCEPT", "   ")


# ── condition A: no explicit layer, but still observed ────────────────────────────
def test_condition_a_still_sees_every_step() -> None:
    """A framework calls the nodes, so a callback handler sees them under any condition.

    This is what makes OFF and A differ only by an installed observer, and it is why the
    baseline cannot be accidentally weakened by turning the explicit layer off.
    """
    seen: list[StepEvent] = []
    tracker = NullTracker(sid="s", agent_label="Test_Agent")
    with observe(seen.append):
        with tracker.activity("ReceiveTaskActivity", "receive") as prov:
            task = prov.produces("TaskEntity", "the task")
        with tracker.activity("ToolCallActivity", "work") as prov:
            prov.consumes(task)
        tracker.decide("ACCEPT", "because the evidence said so", consumed=task)

    assert [event.name for event in seen] == ["receive", "work", "decide: ACCEPT"]
    assert seen[0].outputs == ("the task",)
    assert seen[1].inputs == ("the task",), "a handler is handed values, not identifiers"


def test_condition_a_never_sees_a_verdict_or_a_justification() -> None:
    """The restriction has to survive the off switch as well as the on one."""
    seen: list[StepEvent] = []
    tracker = NullTracker(sid="s")
    with observe(seen.append):
        tracker.decide("ESCALATE", "the integrity check failed")

    (event,) = seen
    assert not any(
        "ESCALATE" in value or "integrity" in value for value in (*event.inputs, *event.outputs)
    )
    assert "integrity" not in event.name
