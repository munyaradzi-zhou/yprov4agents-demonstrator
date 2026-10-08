"""CA3's mining algorithms, tested against the cases that used to break them.

The analytics agent derives everything from PROV itself, so these tests drive the
algorithms directly on synthetic graphs rather than through the bus. Each one pins a
defect that was previously unobservable: a check that could never fire, a timestamp
conversion that invented durations, a dead branch, two overlapping classifiers, and a
verdict that was true of essentially every input.
"""

from __future__ import annotations

from typing import Any

import pytest

from runtime.agents.ca3_analytics import (
    _ENERGY_PAT,
    _MIN_GAPS_FOR_OUTLIERS,
    _RESOURCE_PAT,
    CA3Analytics,
    _clean_ts,
)


def graph_with_starts(starts: list[str], *, ends: list[str] | None = None) -> dict[str, Any]:
    """A PROV document whose activities start at the given times."""
    activities: dict[str, Any] = {}
    for index, start in enumerate(starts):
        node: dict[str, Any] = {
            "prov:type": {"$": "yp:Step", "type": "prov:QUALIFIED_NAME"},
            "prov:startTime": start,
        }
        if ends is not None:
            node["prov:endTime"] = ends[index]
        activities[f"yp:step_{index:02d}"] = node
    return {"activity": activities, "entity": {}}


def entity_graph(labels: list[str]) -> dict[str, Any]:
    return {
        "activity": {},
        "entity": {
            f"yp:metric_{index}": {
                "prov:type": {"$": "yp:Metric", "type": "prov:QUALIFIED_NAME"},
                "prov:label": label,
            }
            for index, label in enumerate(labels)
        },
    }


# ──────────────────────────────────────────────────────────────────────────────────
# Timestamp handling
# ──────────────────────────────────────────────────────────────────────────────────
def test_offset_timestamps_are_converted_not_truncated() -> None:
    """Two spellings of the same instant must compare equal.

    The previous implementation discarded the offset rather than applying it, so a
    +02:00 timestamp and the Z timestamp naming the same moment appeared two hours apart.
    Mixed-offset sources therefore injected phantom gaps into the exact series the timing
    and outlier algorithms consume.
    """
    with_offset = _clean_ts("2026-01-01T12:00:00+02:00")
    as_utc = _clean_ts("2026-01-01T10:00:00Z")

    assert with_offset == as_utc


def test_yprov4ml_typed_literal_suffix_is_tolerated() -> None:
    assert _clean_ts("2026-01-01T10:00:00+00:00^^xsd:dateTime") == _clean_ts(
        "2026-01-01T10:00:00Z"
    )


def test_unparseable_timestamps_are_reported_as_absent() -> None:
    assert _clean_ts("not a timestamp") is None
    assert _clean_ts(None) is None
    assert _clean_ts({"$": "2026-01-01T10:00:00Z"}) is not None


def test_a_mixed_offset_run_measures_the_wall_clock_it_actually_took() -> None:
    """One hour of work, logged half in +01:00 and half in UTC."""
    timing = CA3Analytics._timing(
        graph_with_starts(
            [
                "2026-01-01T10:00:00+00:00",
                "2026-01-01T11:30:00+01:00",  # == 10:30 UTC
                "2026-01-01T11:00:00+00:00",
            ]
        )
    )

    assert timing["wall_clock_s"] == pytest.approx(3600.0)


# ──────────────────────────────────────────────────────────────────────────────────
# Outlier detection
# ──────────────────────────────────────────────────────────────────────────────────
def test_a_material_outlier_is_flagged() -> None:
    """Six activities a second apart, then a sixty-second stall."""
    starts = [f"2026-01-01T10:00:{second:02d}+00:00" for second in range(6)]
    starts.append("2026-01-01T10:01:05+00:00")

    findings = CA3Analytics._anomalies(CA3Analytics._timing(graph_with_starts(starts)))

    assert len(findings) == 1
    assert "step_06" in findings[0]
    assert "% of the run's" in findings[0], "the finding must state how much of the run it is"


def test_a_uniform_run_reports_no_outlier() -> None:
    starts = [f"2026-01-01T10:00:{second:02d}+00:00" for second in range(8)]

    assert CA3Analytics._anomalies(CA3Analytics._timing(graph_with_starts(starts))) == []


def test_the_check_abstains_rather_than_passing_on_too_few_gaps() -> None:
    """Below a handful of gaps there is no distribution to be an outlier against.

    This is the condition the previous rule got wrong in the other direction: with a
    population standard deviation over n samples the largest attainable z-score is
    (n-1)/sqrt(n), so a 3-sigma threshold needed 11 gaps before it could fire at all.
    Every graph in this repository has fewer, so the check always passed — silently.
    """
    starts = [
        "2026-01-01T10:00:00+00:00",
        "2026-01-01T10:00:01+00:00",
        "2026-01-01T11:00:00+00:00",
    ]
    timing = CA3Analytics._timing(graph_with_starts(starts))

    assert len(timing["gaps"]) < _MIN_GAPS_FOR_OUTLIERS
    assert CA3Analytics._anomalies(timing) == []


def test_sub_second_noise_is_not_reported_beside_a_real_stall() -> None:
    """A near-zero median makes any gap a statistical outlier; materiality filters it.

    Producers log setup activities within the same millisecond, so the median gap and the
    median absolute deviation both collapse toward zero and the normalised score explodes.
    Without a materiality floor a 0.098s gap was reported at 11 MAD alongside a 103s one.
    """
    starts = [
        "2026-01-01T10:00:00.000+00:00",
        "2026-01-01T10:00:00.008+00:00",
        "2026-01-01T10:00:00.016+00:00",
        "2026-01-01T10:00:00.024+00:00",
        "2026-01-01T10:00:00.032+00:00",
        "2026-01-01T10:00:00.130+00:00",  # 0.098s: unusual here, but immaterial
        "2026-01-01T10:01:40.130+00:00",  # 100s: the real finding
    ]

    findings = CA3Analytics._anomalies(CA3Analytics._timing(graph_with_starts(starts)))

    assert len(findings) == 1, f"expected only the material stall, got {findings}"
    assert "step_06" in findings[0]


# ──────────────────────────────────────────────────────────────────────────────────
# Metric-family classification
# ──────────────────────────────────────────────────────────────────────────────────
def test_energy_and_resource_families_partition_the_streams() -> None:
    """A label like ``gpu_power_usage`` matches both patterns and must be counted once.

    Reporting the two counts as independent while double-counting their overlap made the
    sustainability and resource inventories unaddable.
    """
    doc = entity_graph(
        [
            "cpu_energy_Training_codecarbon",  # energy
            "emissions_rate_Training",  # energy
            "gpu_power_usage_Training",  # matches both: energy wins
            "cpu_usage_Training",  # resource
            "gpu_temperature_Training",  # resource
            "disk_usage_Training",  # resource
        ]
    )

    energy = CA3Analytics._streams(doc, _ENERGY_PAT)
    resource = CA3Analytics._streams(doc, _RESOURCE_PAT, exclude=_ENERGY_PAT)

    assert energy["count"] == 3
    assert resource["count"] == 3
    assert energy["count"] + resource["count"] == len(doc["entity"])


def test_streams_are_classified_by_label_not_by_schema() -> None:
    """Domain-agnosticism: nothing may depend on an ML-specific entity type."""
    doc = entity_graph(["carbon_intensity_of_region"])
    doc["entity"]["yp:metric_0"]["prov:type"] = {
        "$": "yp:SomethingElseEntirely",
        "type": "prov:QUALIFIED_NAME",
    }

    assert CA3Analytics._streams(doc, _ENERGY_PAT)["count"] == 1


# ──────────────────────────────────────────────────────────────────────────────────
# Anomalies versus completeness
# ──────────────────────────────────────────────────────────────────────────────────
def test_missing_end_times_are_a_completeness_gap_not_an_anomaly() -> None:
    """The distinction that gives the verdict its information content.

    Unclosed activities and partial typing describe what the producer recorded, not how
    the run behaved. Counting them as anomalies made ANOMALY the verdict for essentially
    every real graph — including both bundled yProv4ML records — so the verdict said
    nothing. The finding is still reported; it is just reported as what it is.
    """
    starts = [f"2026-01-01T10:00:{second:02d}+00:00" for second in range(8)]
    doc = graph_with_starts(starts)  # start times only, no end times

    timing = CA3Analytics._timing(doc)
    struct = CA3Analytics._structure(doc)
    empty = {"count": 0}

    assert CA3Analytics._anomalies(timing) == []

    completeness = CA3Analytics._completeness(struct, timing, empty, empty)
    kinds = [finding.split(":")[0] for finding in completeness]
    assert "timing-completeness" in kinds
    assert "sustainability-completeness" in kinds
    assert "resource-completeness" in kinds


def test_a_fully_instrumented_uniform_run_reports_nothing() -> None:
    starts = [f"2026-01-01T10:00:{second:02d}+00:00" for second in range(8)]
    ends = [f"2026-01-01T10:00:{second:02d}.500+00:00" for second in range(8)]
    doc = graph_with_starts(starts, ends=ends)
    doc["entity"] = entity_graph(["cpu_energy_Training", "cpu_usage_Training"])["entity"]

    timing = CA3Analytics._timing(doc)
    struct = CA3Analytics._structure(doc)
    energy = CA3Analytics._streams(doc, _ENERGY_PAT)
    resource = CA3Analytics._streams(doc, _RESOURCE_PAT, exclude=_ENERGY_PAT)

    assert CA3Analytics._anomalies(timing) == []
    assert CA3Analytics._completeness(struct, timing, energy, resource) == []
