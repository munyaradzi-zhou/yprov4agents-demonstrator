"""Bound the cost instead of failing to resolve it.

    python -m eval.equivalence --agent experiments/overhead-samples/agent.json \
        --energy experiments/energy-session/energy.json

"Not resolvable at three times its spread" says the measurement could not see a
difference; it does not say there is none, and a small study fails to resolve large effects
too. The claims the paper makes are claims of *equivalence*, so they are tested as such:
two one-sided tests (TOST), which at the 5% level reduce to asking whether the 90%
confidence interval of the difference lies inside a stated margin. Each difference is also
given its 95% interval, so a reader can apply a margin of their own.

The intervals are percentile bootstraps of the median paired difference. Pairing is by
round: every study here interleaves its conditions, so sample *i* of each condition comes
from the same round and shares its background load. The resampling is seeded, so the
intervals are reproducible to the digit.

Margins are stated with their reason, not tuned to the result:

* **Time, 1 ms.** The fastest model call recorded in ``experiments/audit-llm`` took 1.65 s;
  a millisecond is 0.06% of it. A cost inside a millisecond is negligible beside any step
  that calls a model, whatever it is beside a step that does not.
* **Energy, no margin.** There is no external yardstick for a session's CPU joules on this
  host, so the energy difference is reported as an interval and an upper bound only.
"""

from __future__ import annotations

import argparse
import json
import random
import statistics
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

RESAMPLES = 10_000
SEED = 20261017

#: The equivalence margin for time, in nanoseconds; see the module docstring.
TIME_MARGIN_NS = 1_000_000.0


@dataclass(frozen=True)
class Bound:
    """A paired difference, its intervals, and the equivalence verdict against a margin."""

    estimate: float
    ci95: tuple[float, float]
    ci90: tuple[float, float]
    baseline: float
    rounds: int
    margin: float | None = None

    @property
    def equivalent(self) -> bool | None:
        if self.margin is None:
            return None
        low, high = self.ci90
        return -self.margin < low and high < self.margin

    def pct(self, value: float) -> float:
        return 100.0 * value / self.baseline


def _quantile(sorted_values: list[float], q: float) -> float:
    position = q * (len(sorted_values) - 1)
    low = int(position)
    high = min(low + 1, len(sorted_values) - 1)
    return sorted_values[low] + (sorted_values[high] - sorted_values[low]) * (position - low)


def paired_bound(
    baseline: list[float],
    measured: list[float],
    *,
    margin: float | None = None,
    resamples: int = RESAMPLES,
    seed: int = SEED,
) -> Bound:
    """The median round-wise difference ``measured - baseline``, with bootstrap intervals."""
    if len(baseline) != len(measured):
        raise ValueError("paired samples must come from the same rounds")
    differences = [m - b for b, m in zip(baseline, measured, strict=True)]
    rng = random.Random(seed)
    n = len(differences)
    medians = sorted(statistics.median(rng.choices(differences, k=n)) for _ in range(resamples))
    return Bound(
        estimate=statistics.median(differences),
        ci95=(_quantile(medians, 0.025), _quantile(medians, 0.975)),
        ci90=(_quantile(medians, 0.05), _quantile(medians, 0.95)),
        baseline=statistics.median(baseline),
        rounds=n,
        margin=margin,
    )


def overhead_bounds(agent_json: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per agent: what building the record costs (B-TRANS), and building and writing it (B)."""
    out = {}
    for label, row in agent_json["agents"].items():
        samples = row["samples_ns"]
        out[label] = {
            condition: _row(
                paired_bound(samples["OFF"], samples[condition], margin=TIME_MARGIN_NS)
            )
            for condition in ("B-TRANS", "B")
        }
    return out


def energy_bounds(energy_json: dict[str, Any]) -> dict[str, dict[str, Any]]:
    """Per condition: CPU joules per session against OFF, paired by round."""
    windows = energy_json["windows"]

    def per_session(name: str) -> list[float]:
        return [w["cpu_j"] / w["sessions"] for w in windows[name]]

    return {
        name: _row(paired_bound(per_session("OFF"), per_session(name)))
        for name in ("A", "B-TRANS", "B")
    }


def _row(bound: Bound) -> dict[str, Any]:
    return {
        **asdict(bound),
        "equivalent": bound.equivalent,
        "pct": bound.pct(bound.estimate),
        "ci95_pct": [bound.pct(v) for v in bound.ci95],
    }


def render(overhead: dict[str, Any] | None, energy: dict[str, Any] | None) -> str:
    lines = []
    if overhead:
        lines += [
            f"Time, against OFF: median paired difference and 95% interval; equivalent means "
            f"the 90% interval lies inside +/-{TIME_MARGIN_NS / 1e6:g} ms (TOST, 5%).",
            "",
            "| Agent | Build (B-TRANS) | Equivalent | Build and write (B) | Equivalent |",
            "|---|--:|:-:|--:|:-:|",
        ]
        for label, row in overhead.items():
            cells = []
            for condition in ("B-TRANS", "B"):
                r = row[condition]
                low, high = (v / 1e3 for v in r["ci95"])
                cells += [
                    f"{r['estimate'] / 1e3:+,.0f} us [{low:+,.0f}, {high:+,.0f}]",
                    "yes" if r["equivalent"] else "no",
                ]
            lines.append(f"| {label} | " + " | ".join(cells) + " |")
    if energy:
        lines += [
            "",
            "CPU energy per session, against OFF (gross, paired by round):",
            "",
            "| Condition | Difference | 95% interval | Upper bound |",
            "|---|--:|--:|--:|",
        ]
        for name, r in energy.items():
            low, high = r["ci95"]
            lines.append(
                f"| {name} | {r['estimate']:+.3f} J ({r['pct']:+.0f}%) | "
                f"[{low:+.3f}, {high:+.3f}] J | {r['ci95_pct'][1]:+.0f}% |"
            )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--agent", type=Path, help="an agent.json carrying samples_ns")
    parser.add_argument("--energy", type=Path, help="an energy.json from eval.bench_energy")
    parser.add_argument("--json", type=Path, help="also write the bounds here")
    args = parser.parse_args(argv)
    overhead = (
        overhead_bounds(json.loads(args.agent.read_text(encoding="utf-8")))
        if args.agent
        else None
    )
    energy = (
        energy_bounds(json.loads(args.energy.read_text(encoding="utf-8")))
        if args.energy
        else None
    )
    if args.json:
        payload = {
            "resamples": RESAMPLES,
            "seed": SEED,
            "time_margin_ns": TIME_MARGIN_NS,
            "overhead": overhead,
            "energy": energy,
        }
        args.json.parent.mkdir(parents=True, exist_ok=True)
        args.json.write_text(json.dumps(payload, indent=2), encoding="utf-8")
    print(render(overhead, energy))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
