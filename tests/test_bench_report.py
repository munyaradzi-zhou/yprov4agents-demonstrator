"""The overhead report: every verdict it prints is a judgement made from the stored samples.

The code that writes the paper's performance sentences had no tests (docs/AUDIT.md [11]),
and the two wrong figures it published — 254 bytes against 278, and "the verdicts were
unchanged for every agent" — were both sentences typed into it rather than computed. These
tests feed it small results files whose right answers can be worked out by hand.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from eval.bench_report import _resolves, _verdict, render_agent, render_stability

MS = 1_000_000.0


def _agent(
    off: float, built: float, written: float, spread: float = 0.1 * MS
) -> dict[str, Any]:
    """One stored agent measurement: medians in ms, one dispersion for every condition."""
    medians = {"OFF": off * MS, "A": off * MS, "B-TRANS": built * MS, "B": written * MS}
    return {
        "median_ns": medians,
        "dispersion_ns": dict.fromkeys(medians, spread),
        "vs_off": {"pct": 100.0 * (written - off) / off, "noise_ns": spread},
        "write_cost_ns": (written - built) * MS,
        "output_bytes": 5000,
    }


#: Built in noise, written resolved: the shape every operational agent has.
OPERATIONAL = _agent(off=5.0, built=5.01, written=7.0)
#: Resolved both ways.
META = _agent(off=0.1, built=0.5, written=2.0, spread=0.01 * MS)
#: Within the noise both ways.
QUIET = _agent(off=100.0, built=100.05, written=100.2)


def test_a_difference_resolves_only_at_three_times_the_larger_dispersion() -> None:
    assert _resolves(OPERATIONAL) == (True, pytest.approx(20.0))
    assert _resolves(OPERATIONAL, condition="B-TRANS") == (False, pytest.approx(0.1))
    borderline = _agent(off=5.0, built=5.0, written=5.3)
    assert _resolves(borderline) == (False, pytest.approx(3.0))


def test_the_noise_is_the_larger_of_the_two_conditions_dispersions() -> None:
    row = _agent(off=5.0, built=5.0, written=6.0)
    row["dispersion_ns"]["B"] = 0.5 * MS
    # 1 ms against B's 0.5 ms spread, not OFF's 0.1 ms: 2×, which does not resolve.
    assert _resolves(row) == (False, pytest.approx(2.0))


def test_a_cell_prints_an_overhead_only_when_it_resolves() -> None:
    assert _verdict(OPERATIONAL) == "**+40%** (20.0×)"
    assert _verdict(OPERATIONAL, "B-TRANS") == "*in noise* (0.1×)"


def test_the_agent_table_judges_building_and_writing_separately() -> None:
    text = "\n".join(
        render_agent({"repetitions": 30, "agents": {"OP": OPERATIONAL, "META": META}})
    )
    assert "| OP | 5.00 ms | 5.00 ms | 5.01 ms | 7.00 ms | *in noise* (0.1×) |" in text
    assert "**Building the record is unresolvable for 1 of the 2 agents**" in text
    assert "Lead with the worst case: META, at +1900%" in text


def _occasion(directory: Path, agents: dict[str, Any]) -> Path:
    directory.mkdir()
    (directory / "agent.json").write_text(json.dumps({"agents": agents}), encoding="utf-8")
    return directory


def test_stability_names_the_agents_whose_verdict_changed(tmp_path: Path) -> None:
    """The sentence that was typed in as "unchanged for every agent", now computed."""
    first = _occasion(tmp_path / "one", {"OP": QUIET, "META": META})
    second = _occasion(tmp_path / "two", {"OP": OPERATIONAL, "META": META})
    text = "\n".join(render_stability(first, second))
    assert "Whether building the record resolves held for every agent" in text
    assert "whether building and writing it resolves changed for OP" in text


def test_stability_says_so_when_every_verdict_held(tmp_path: Path) -> None:
    first = _occasion(tmp_path / "one", {"OP": OPERATIONAL})
    second = _occasion(tmp_path / "two", {"OP": OPERATIONAL})
    text = "\n".join(render_stability(first, second))
    assert "building and writing it resolves held for every agent" in text
