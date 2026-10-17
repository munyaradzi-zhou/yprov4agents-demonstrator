"""Replay every recorded decision from its own record, and measure what its rule read.

    python -m eval.replay --prov-dir out --baseline-dir out/baseline
    python -m eval.replay --prov-dir experiments/decision-replay --json

Two questions, both answered from ``yp:rule`` and ``yp:rule_inputs`` rather than from prose.

*Is the record faithful to the code?* Each decision names a versioned rule and the values it
read. Re-applying the rule to those values must give the recorded verdict (and escalation
reason). A justification is a sentence and can say anything; a replayed verdict cannot.

*What did the decision read, and who could see it?* The fact-level measure in
:mod:`eval.baseline` extracts facts from justification text with a regex, which makes the
ground truth a matter of pattern design. Rule inputs are the ground truth the code itself
declares. For each one, this asks whether the callback handler saw its value anywhere, and
whether the justification states it. Booleans are reported apart: ``true`` is not a fact a
text search can attribute to one test rather than another, so they are neither counted as
seen nor as unseen.
"""

from __future__ import annotations

import argparse
import json
from collections import Counter, defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

# Importing the agents registers their rules; a replay of a rule nobody registered is
# reported as such, not silently passed.
import runtime.agents.ca1_storage
import runtime.agents.ca2_metrics
import runtime.agents.ca3_analytics
import runtime.agents.cax_summariser
import runtime.agents.mc1_compliance
import runtime.agents.mc3_audit
import runtime.base_agent  # noqa: F401
from yprov4agenticai.rules import replay

from .baseline import (
    AGENT_ROLES,
    DEFAULT_TYPE_MAP,
    EVENTS_FILE,
    CallbackLogger,
    _visible,
    read_events,
    role_of,
)


@dataclass
class InputVisibility:
    """One agent's rule inputs, against what the callback handler observed."""

    decisions: int = 0
    inputs: int = 0
    tests: int = 0  # boolean inputs: outcomes of a check inside the step
    unset: int = 0  # inputs with no value (a statistic that was not defined)
    searchable: int = 0
    seen_by_a: int = 0
    stated: int = 0  # searchable inputs the justification states


#: A rounding of a float stands for it only while it stays this close, relatively. Without
#: the bound, 7.8e-05 rounds to "0.000" and is "seen" wherever a sentence says zero.
ROUNDING_TOLERANCE = 0.05


def renderings(value: Any) -> list[str]:
    """The forms a value can take in text. Generous on purpose: a float counts as seen if
    any rounding of it a sentence might use, within :data:`ROUNDING_TOLERANCE`, is seen."""
    if isinstance(value, float):
        forms = {repr(value)}
        for places in (3, 2, 1):
            rounded = f"{value:.{places}f}"
            if abs(float(rounded) - value) <= ROUNDING_TOLERANCE * abs(value):
                forms.add(rounded)
        if value.is_integer():
            forms.add(str(int(value)))
        return sorted(forms, key=len, reverse=True)
    return [str(value)]


def _found(value: Any, haystack: str) -> bool:
    return any(_visible(form, haystack) for form in renderings(value))


def input_visibility(
    named: Iterable[tuple[Path, dict[str, Any]]], payloads: list[str]
) -> dict[str, InputVisibility]:
    haystack = "\n".join(payloads)
    out: dict[str, InputVisibility] = defaultdict(InputVisibility)
    for path, doc in named:
        for activity in (doc.get("activity") or {}).values():
            if not activity.get("yp:rule"):
                continue
            tally = out[role_of(path)]
            tally.decisions += 1
            justification = str(activity.get("yp:justification", ""))
            for value in json.loads(activity.get("yp:rule_inputs") or "{}").values():
                tally.inputs += 1
                if isinstance(value, bool):
                    tally.tests += 1
                elif value is None:
                    tally.unset += 1
                else:
                    tally.searchable += 1
                    tally.seen_by_a += _found(value, haystack)
                    tally.stated += _found(value, justification)
    order = {role: index for index, role in enumerate(AGENT_ROLES)}
    return dict(sorted(out.items(), key=lambda item: (order.get(item[0], 99), item[0])))


def run(prov_dir: Path, baseline_dir: Path | None) -> dict[str, Any]:
    named = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(prov_dir.glob("*_prov.json"))
    ]
    results = [result for _, doc in named for result in replay(doc)]
    report: dict[str, Any] = {
        "records": len(named),
        "decisions": len(results),
        "replay": dict(Counter(result.status for result in results)),
        "rules": dict(Counter(result.rule for result in results)),
        "mismatches": [asdict(result) for result in results if result.status == "mismatch"],
    }
    events = (baseline_dir or prov_dir / "baseline") / EVENTS_FILE
    if events.is_file():
        # The same visible world eval.baseline searches: the mapped handler's payloads.
        logger = CallbackLogger.from_events(read_events(events), type_map=DEFAULT_TYPE_MAP)
        payloads = logger.observed_payloads()
        report["inputs"] = {
            role: asdict(tally) for role, tally in input_visibility(named, payloads).items()
        }
    return report


def render(report: dict[str, Any]) -> str:
    replayed = report["replay"].get("replayed", 0)
    lines = [
        f"{replayed} of {report['decisions']} decisions in {report['records']} records replay "
        f"from their own yp:rule and yp:rule_inputs "
        f"({', '.join(f'{k}: {v}' for k, v in sorted(report['replay'].items()))}).",
    ]
    inputs = report.get("inputs")
    if inputs:
        lines += [
            "",
            "| Agent | Decisions | Rule inputs | Checks (bool) | Searchable | Seen by A "
            "| Stated in justification |",
            "|---|--:|--:|--:|--:|--:|--:|",
        ]
        total = InputVisibility()
        for role, tally in inputs.items():
            lines.append(
                f"| {role} | {tally['decisions']} | {tally['inputs']} | {tally['tests']} | "
                f"{tally['searchable']} | {tally['seen_by_a']} | {tally['stated']} |"
            )
            for key, value in tally.items():
                setattr(total, key, getattr(total, key) + value)
        lines.append(
            f"| **all** | {total.decisions} | {total.inputs} | {total.tests} | "
            f"{total.searchable} | {total.seen_by_a} | {total.stated} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prov-dir", type=Path, default=Path("out"))
    parser.add_argument("--baseline-dir", type=Path, default=None)
    parser.add_argument("--json", action="store_true", help="print the report as JSON")
    args = parser.parse_args(argv)
    report = run(args.prov_dir, args.baseline_dir)
    print(json.dumps(report, indent=2) if args.json else render(report))
    return 0 if not report["mismatches"] else 1


if __name__ == "__main__":
    raise SystemExit(main())
