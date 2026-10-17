"""Does the record help an operator diagnose what happened? A panel of models stands in.

    python -m eval.diagnose --dry-run
    python -m eval.diagnose --out-dir out/study            # makes the calls
    python -m eval.diagnose --out-dir out/study --offline  # re-scores from the cache

The paper's claim that a justification is *useful* needs an operator. This is the cheapest
honest version of that study: an operator's question, asked of the record each condition
produces, and answered by a panel of models in place of people. Each case is one decision by
an operational agent; the operator is shown that agent's PROV record for the run, either the
callback baseline's (A) or the explicit one (B), and asked which of four statements describes
what happened, each statement implying a different response:

* CA1: the stored copy is corrupt / part of the record was lost in transport / a consumer
  did not confirm it used the record / nothing is wrong;
* CAx: the summary asserts figures the graph lacks / it was cut off / the input arrived
  incomplete / nothing is wrong;
* CA2: the input has no numeric values, so a fallback chart was drawn / the chart failed to
  render / the input arrived incomplete / nothing is wrong;
* CA3: a timing stall in the run / only gaps in what the producer recorded / the input
  arrived incomplete / nothing anomalous.

The right answer comes from the decision's own rule and inputs, never from a model. Every
case is asked three times with the options in a different order, so a position bias cannot
pass for knowledge, and of two models, so one model's habits cannot either. The comparison
is paired: the same case, the same model and the same order, with only the record changed.

A model panel is not a user study. It measures whether the information needed to tell the
causes apart is in the record, which is a precondition of an operator telling them apart;
it says nothing about how long a person would take or what they would trust.
"""

from __future__ import annotations

import argparse
import json
import math
import random
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.cases import Case, compact, load, load_faults, stratified
from eval.llm_calls import OPERATOR_MODELS, SEED, accounting, as_record, ask

SHUFFLES = 3
CORPUS_PER_ROLE = 6

OPTIONS: dict[str, dict[str, str]] = {
    "ca1": {
        "integrity": "The copy in the store does not match what was written: the stored data "
        "is corrupt. Investigate the storage backend.",
        "incomplete-input": "Part of the record never arrived, so what was stored is partial. "
        "Have the producer re-send it.",
        "consumption-unconfirmed": "Not every consumer confirmed it had used the record before "
        "it was archived; a consumer may be down. Check the consumers.",
        "none": "Nothing is wrong: the record was stored as intended.",
    },
    "cax": {
        "ungrounded": "The summary states figures the provenance graph does not support. "
        "Correct or regenerate the summary.",
        "truncated": "The summary was cut off by its token budget. Regenerate it with a larger "
        "budget.",
        "incomplete": "The input record arrived incomplete, so no summary was judged. Have the "
        "producer re-send it.",
        "none": "Nothing is wrong: the summary was accepted.",
    },
    "ca2": {
        "fallback": "The input carries no numeric metric values, so a fallback chart of the "
        "graph's composition was drawn instead. Fix the producer's metric logging.",
        "render_failed": "The chart failed to render and the file is empty. Check the plotting "
        "environment.",
        "incomplete": "The input record arrived incomplete, so no chart was judged. Have the "
        "producer re-send it.",
        "none": "Nothing is wrong: the run's metrics were plotted.",
    },
    "ca3": {
        "stall": "The run stalled: one gap between its steps is an outlier and a material "
        "share of its wall clock. Investigate what blocked the run.",
        "completeness_only": "Only the producer's record is incomplete (missing end times or "
        "types); the run itself shows nothing unusual.",
        "incomplete": "The input record arrived incomplete, so it was not analysed. Have the "
        "producer re-send it.",
        "none": "Nothing anomalous was found in the run.",
    },
}

SYSTEM = """You are the on-call operator for a pipeline of AI agents that process machine \
learning provenance. You are shown the W3C PROV-JSON record that one agent produced for one \
run. Choose the statement that best describes what happened in that run, which decides what \
you do next. Use only the record. Answer with a JSON object: \
{"choice": "<the option letter>", "confidence": <a number from 0 to 1>}"""


def cause(case: Case) -> str:
    """The right answer, from the decision's verdict, escalation reason and rule inputs."""
    inputs = case.rule_inputs
    if case.verdict == "INCOMPLETE":
        return "incomplete"
    if case.role == "ca1":
        return case.escalation_reason or "none"
    if case.role == "cax" and case.rule == "cax.review/v1":
        # The model decided: the cause is what the model objected to, read by the rule.
        if inputs.get("truncated"):
            return "truncated"
        return "none" if case.verdict == "ACCEPT" else "ungrounded"
    if case.role == "cax":
        if inputs.get("ungrounded_figures"):
            return "ungrounded"
        return "truncated" if inputs.get("truncated") else "none"
    if case.role == "ca2":
        if not inputs.get("rendered_bytes"):
            return "render_failed"
        return "none" if inputs.get("chart_subject") == "numeric-metrics" else "fallback"
    return "stall" if case.verdict == "ANOMALY" else "none"


def _order(case: Case, shuffle: int) -> list[str]:
    keys = sorted(OPTIONS[case.role])
    random.Random(f"{SEED}/{case.case_id}/{shuffle}").shuffle(keys)
    return keys


def _user(record: dict[str, Any], keys: list[str], role: str) -> str:
    letters = "ABCD"
    options = "\n".join(f"{letters[i]}. {OPTIONS[role][k]}" for i, k in enumerate(keys))
    return f"RECORD\n{compact(record)}\n\nOPTIONS\n{options}"


@dataclass
class Trial:
    case_id: str
    source: str
    role: str
    truth: str
    model: str
    shuffle: int
    condition: str
    chosen: str | None
    correct: bool
    confidence: float | None
    call: dict[str, Any]


def run(cases: list[Case], cache: Path, *, offline: bool) -> tuple[list[Trial], list[Any]]:
    trials, spent = [], []
    for model in OPERATOR_MODELS:
        for case in cases:
            truth = cause(case)
            for shuffle in range(SHUFFLES):
                keys = _order(case, shuffle)
                for condition, record in (("A", case.record_a), ("B", case.record_b)):
                    answer = ask(
                        SYSTEM,
                        _user(record, keys, case.role),
                        cache=cache,
                        model=model,
                        offline=offline,
                        max_tokens=60,
                    )
                    spent.append(answer)
                    letter = str(answer.data.get("choice", "")).strip().upper()[:1]
                    chosen = keys["ABCD".index(letter)] if letter and letter in "ABCD" else None
                    confidence = answer.data.get("confidence")
                    trials.append(
                        Trial(
                            case_id=case.case_id,
                            source=case.source,
                            role=case.role,
                            truth=truth,
                            model=model,
                            shuffle=shuffle,
                            condition=condition,
                            chosen=chosen,
                            correct=chosen == truth,
                            confidence=float(confidence)
                            if isinstance(confidence, int | float)
                            else None,
                            call=as_record(answer),
                        )
                    )
    return trials, spent


def wilson(k: int, n: int, z: float = 1.96) -> tuple[float, float]:
    if n == 0:
        return (0.0, 0.0)
    p = k / n
    centre = (p + z * z / (2 * n)) / (1 + z * z / n)
    half = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / (1 + z * z / n)
    return (centre - half, centre + half)


def mcnemar_exact(b_only: int, a_only: int) -> float:
    """Two-sided exact McNemar p-value from the discordant pairs."""
    n = b_only + a_only
    if n == 0:
        return 1.0
    k = min(b_only, a_only)
    tail = sum(math.comb(n, i) for i in range(k + 1)) / 2**n
    return min(1.0, 2 * tail)


def summarise(trials: list[Trial]) -> dict[str, Any]:
    def accuracy(rows: list[Trial]) -> dict[str, Any]:
        k = sum(t.correct for t in rows)
        low, high = wilson(k, len(rows))
        return {"correct": k, "trials": len(rows), "ci95": [low, high]}

    paired: dict[tuple[str, str, int], dict[str, bool]] = defaultdict(dict)
    for t in trials:
        paired[(t.case_id, t.model, t.shuffle)][t.condition] = t.correct
    b_only = sum(1 for v in paired.values() if v.get("B") and not v.get("A"))
    a_only = sum(1 for v in paired.values() if v.get("A") and not v.get("B"))

    out: dict[str, Any] = {
        "overall": {c: accuracy([t for t in trials if t.condition == c]) for c in ("A", "B")},
        "paired": {"b_only": b_only, "a_only": a_only, "p": mcnemar_exact(b_only, a_only)},
        "by_model": {
            m: {
                c: accuracy([t for t in trials if t.model == m and t.condition == c])
                for c in "AB"
            }
            for m in OPERATOR_MODELS
        },
        "by_role": {
            r: {
                c: accuracy([t for t in trials if t.role == r and t.condition == c])
                for c in "AB"
            }
            for r in ("ca1", "ca2", "ca3", "cax")
        },
        "by_truth": {
            truth: {
                c: accuracy([t for t in trials if t.truth == truth and t.condition == c])
                for c in "AB"
            }
            for truth in sorted({t.truth for t in trials})
        },
        "confident_and_wrong": {
            c: sum(
                1
                for t in trials
                if t.condition == c and not t.correct and (t.confidence or 0) >= 0.8
            )
            for c in "AB"
        },
    }
    return out


def render(summary: dict[str, Any]) -> str:
    def cell(row: dict[str, Any]) -> str:
        low, high = row["ci95"]
        pct = 100 * row["correct"] / row["trials"] if row["trials"] else 0
        share = f"{pct:.0f}%, {100 * low:.0f} to {100 * high:.0f}"
        return f"{row['correct']}/{row['trials']} ({share})"

    lines = ["| Slice | A: callback baseline | B: explicit capture |", "|---|--:|--:|"]
    lines.append(f"| all | {cell(summary['overall']['A'])} | {cell(summary['overall']['B'])} |")
    for model, row in summary["by_model"].items():
        lines.append(f"| {model} | {cell(row['A'])} | {cell(row['B'])} |")
    for role, row in summary["by_role"].items():
        lines.append(f"| {role} | {cell(row['A'])} | {cell(row['B'])} |")
    for truth, row in summary["by_truth"].items():
        lines.append(f"| cause: {truth} | {cell(row['A'])} | {cell(row['B'])} |")
    p = summary["paired"]
    lines.append(
        f"\nPaired: B right where A wrong {p['b_only']}, A right where B wrong {p['a_only']}, "
        f"exact McNemar p = {p['p']:.2g}. Confident (>= 0.8) and wrong: "
        f"A {summary['confident_and_wrong']['A']}, B {summary['confident_and_wrong']['B']}."
    )
    return "\n".join(lines)


def gather(
    reference: Path, corpus: Path, faults: Path, per_scenario: int | None = None
) -> list[Case]:
    """The cases: the reference run, a fixed corpus sample, and the fault runs.

    ``per_scenario`` keeps a fixed sample of that many runs from each fault directory that
    holds several (eval.review_faults writes 24 per kind); None keeps them all.
    """
    fault_cases = load_faults(faults)
    if per_scenario is not None:
        by_scenario: dict[str, list[Case]] = defaultdict(list)
        for case in fault_cases:
            by_scenario[case.case_id.split("/")[1]].append(case)
        rng = random.Random(SEED)
        fault_cases = [
            case
            for name in sorted(by_scenario)
            for case in sorted(
                rng.sample(
                    sorted(by_scenario[name], key=lambda c: c.case_id),
                    min(per_scenario, len(by_scenario[name])),
                ),
                key=lambda c: c.case_id,
            )
        ]
    return (
        load(reference, reference / "baseline" / "_events.json", "reference")
        + stratified(
            load(corpus, corpus / "baseline" / "_events.json", "corpus"), CORPUS_PER_ROLE, SEED
        )
        + fault_cases
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", type=Path, default=Path("experiments/decision-replay"))
    parser.add_argument("--corpus", type=Path, default=Path("experiments/corpus-24"))
    parser.add_argument("--faults", type=Path, default=Path("out/study/faults"))
    parser.add_argument(
        "--per-scenario",
        type=int,
        default=None,
        help="sample this many runs per fault directory",
    )
    parser.add_argument("--out-dir", type=Path, default=Path("out/study"))
    parser.add_argument("--offline", action="store_true")
    parser.add_argument("--roles", default="ca1,ca2,ca3,cax", help="agents to include")
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args(argv)

    roles = {r.strip() for r in args.roles.split(",")}
    every = gather(args.reference, args.corpus, args.faults, args.per_scenario)
    cases = [c for c in every if c.role in roles]
    if args.dry_run:
        chars = sum(
            len(_user(r, _order(c, 0), c.role)) + len(SYSTEM)
            for c in cases
            for r in (c.record_a, c.record_b)
        )
        calls = len(cases) * 2 * SHUFFLES * len(OPERATOR_MODELS)
        truths: dict[str, int] = defaultdict(int)
        for c in cases:
            truths[f"{c.role}:{cause(c)}"] += 1
        tokens = chars * SHUFFLES * len(OPERATOR_MODELS) / 4 / 1e3
        print(f"{len(cases)} cases, {calls} calls, ~{tokens:,.0f}k input tokens")
        print(dict(sorted(truths.items())))
        return 0

    trials, spent = run(cases, args.out_dir / "cache", offline=args.offline)
    summary = summarise(trials)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "diagnose.json").write_text(
        json.dumps(
            {
                "models": OPERATOR_MODELS,
                "shuffles": SHUFFLES,
                "cases": len(cases),
                "accounting": accounting(spent),
                "summary": summary,
                "trials": [t.__dict__ for t in trials],
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    table = render(summary)
    (args.out_dir / "DIAGNOSE.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(json.dumps(accounting(spent)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
