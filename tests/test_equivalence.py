"""Equivalence bounds: paired, seeded, and honest about what a margin can and cannot show."""

from __future__ import annotations

import random

import pytest

from eval.equivalence import TIME_MARGIN_NS, energy_bounds, overhead_bounds, paired_bound


def test_a_difference_well_inside_the_margin_is_equivalent() -> None:
    rng = random.Random(1)
    base = [1e6 + rng.gauss(0, 1e4) for _ in range(30)]
    shifted = [b + 2e4 + rng.gauss(0, 1e3) for b in base]  # +20 us, tightly paired
    bound = paired_bound(base, shifted, margin=TIME_MARGIN_NS)
    assert bound.equivalent is True
    assert bound.ci95[0] < 2e4 < bound.ci95[1]


def test_a_large_difference_is_not_equivalent_and_its_interval_excludes_zero() -> None:
    base = [1e6 + i for i in range(30)]
    bound = paired_bound(base, [b + 5e6 for b in base], margin=TIME_MARGIN_NS)
    assert bound.equivalent is False
    assert bound.ci95[0] > 0


def test_pairing_removes_drift_shared_by_a_round() -> None:
    # Each round drifts by up to a second; the conditions differ by exactly 10 us within it.
    drift = [i * 3.3e7 for i in range(30)]
    bound = paired_bound(drift, [d + 1e4 for d in drift], margin=TIME_MARGIN_NS)
    assert bound.ci95 == (1e4, 1e4)


def test_the_bootstrap_is_reproducible() -> None:
    base, other = [float(i) for i in range(12)], [float(i * i % 7) for i in range(12)]
    assert paired_bound(base, other) == paired_bound(base, other)


def test_samples_must_come_from_the_same_rounds() -> None:
    with pytest.raises(ValueError, match="same rounds"):
        paired_bound([1.0, 2.0], [1.0])


def test_the_readers_take_the_stored_shapes() -> None:
    samples = {name: [1e6, 1.1e6, 0.9e6] for name in ("OFF", "B-TRANS", "B")}
    rows = overhead_bounds({"agents": {"X": {"samples_ns": samples}}})
    assert set(rows["X"]) == {"B-TRANS", "B"}
    window = {"cpu_j": 140.0, "sessions": 97}
    energy = energy_bounds({"windows": {n: [window] * 3 for n in ("OFF", "A", "B-TRANS", "B")}})
    assert energy["B"]["estimate"] == 0.0
