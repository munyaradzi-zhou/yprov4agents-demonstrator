"""Can a model reconstruct a decision's reason from what the callback baseline observed?

    python -m eval.reconstruct --dry-run
    python -m eval.reconstruct --out-dir out/study            # makes the calls
    python -m eval.reconstruct --out-dir out/study --offline  # re-scores from the cache

The strongest retrieval a reviewer can propose is not a regex but a model: hand it what the
handler saw and ask it what the agent decided and why. This does that, for every
operational decision in the reference run, the 24-run corpus and the fault scenarios, under
three conditions that give the model successively more:

* ``trace``: the handler's events for that agent's run (step names, inputs, outputs, times);
* ``trace+code``: the same, plus the agent's source module, so the model can read the rule;
* ``trace+code+input``: also the producer's input record, so the model can, in principle,
  re-run the computation the rule read. This is Proposition 1's escape clause, re-running
  ``g``, with a model as the interpreter. The fault scenarios have no single input file and
  are left out of it.

Each reconstruction is scored four ways, none by the model that made it:

* the verdict, and for CA1 the escalation reason, against the record;
* each value the rule read (``yp:rule_inputs``), searched for in the reconstruction, with
  the same chance control as Table 3: the same value searched for in the reconstruction of a
  different case of the same agent;
* the reason, graded 0 to 2 against the recorded justification by a separate judge call.
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.cases import Case, load, load_faults
from eval.llm_calls import DEFAULT_MODEL, Answer, accounting, as_record, ask
from eval.replay import _found

CONDITIONS = ("trace", "trace+code", "trace+code+input")

MODULES = {
    "ca1": "runtime/agents/ca1_storage.py",
    "ca2": "runtime/agents/ca2_metrics.py",
    "ca3": "runtime/agents/ca3_analytics.py",
    "cax": "runtime/agents/cax_summariser.py",
}

RECONSTRUCT = """You are auditing an AI agent after the fact. You are given what a framework \
callback handler observed while the agent ran: one event per step, with the step's name, the \
inputs and outputs the handler was handed, and its start and end times. You may also be given \
the agent's source code and the input record it processed.

The agent's run ended in a decision. Reconstruct it as precisely as the material allows.
Answer with a JSON object:
{"verdict": "<the verdict token the agent recorded>",
 "escalation_reason": "<integrity | incomplete-input | consumption-unconfirmed | null>",
 "reason": "<one to three sentences: why the agent reached that verdict>",
 "values": {"<name>": <value>, "...": "..."}}
"values" holds every value you believe the decision depended on, with its number or label. \
Do not invent values you cannot support from the material; leave them out instead."""

JUDGE = """You compare two statements of why an AI agent reached a decision. REFERENCE is \
the reason the agent itself recorded at the moment of decision. CANDIDATE is a reconstruction \
by an auditor who did not see the reference.

Score the candidate:
2 = the same decisive reason, with the specifics that determined the verdict (the same \
condition, and the values or thresholds that triggered it);
1 = the right kind of reason, but the decisive specifics are missing or wrong;
0 = a different reason, a contradiction, or no reason.
Answer with a JSON object: {"score": 0 | 1 | 2, "why": "<one sentence>"}"""


def _events(case: Case) -> str:
    shown = [
        {k: e.get(k) for k in ("name", "inputs", "outputs", "started_at", "ended_at", "error")}
        for e in case.events
    ]
    return json.dumps(shown, ensure_ascii=False, indent=0)


def _input_record(case: Case) -> Path | None:
    if case.source == "corpus":
        return Path("corpus/yprov4ml") / f"{case.session}.json"
    if case.source == "reference":
        found = sorted(
            Path("data/yprov4ml").glob(
                f"**/prov_{case.session.replace('example', 'example_GR0')}.json"
            )
        )
        return found[0] if found else None
    return None


def prompt(case: Case, condition: str) -> str | None:
    parts = [f"OBSERVED EVENTS\n{_events(case)}"]
    if condition in ("trace+code", "trace+code+input"):
        source = Path(MODULES[case.role]).read_text(encoding="utf-8")
        parts.append(f"AGENT SOURCE ({MODULES[case.role]})\n{source}")
    if condition == "trace+code+input":
        path = _input_record(case)
        if path is None:
            return None
        parts.append(f"INPUT RECORD ({path.name})\n{path.read_text(encoding='utf-8')}")
    return "\n\n".join(parts)


@dataclass
class Scored:
    case_id: str
    source: str
    role: str
    condition: str
    verdict_correct: bool
    escalation_correct: bool | None
    searchable: int
    values_found: int
    values_by_chance: int
    judge_score: int | None
    answer: dict[str, Any]
    judge: dict[str, Any]
    calls: list[dict[str, Any]]


def same_verdict(given: str, recorded: str) -> bool:
    """Whether a free-text verdict names the recorded one ("escalated" for ESCALATE)."""
    letters = "".join(ch for ch in given.upper() if ch.isalpha())
    return letters[:5] == recorded.upper()[:5]


def gather(reference: Path, corpus: Path, faults: Path) -> list[Case]:
    return (
        load(reference, reference / "baseline" / "_events.json", "reference")
        + load(corpus, corpus / "baseline" / "_events.json", "corpus")
        + load_faults(faults)
    )


def run(
    cases: list[Case], cache: Path, *, model: str, offline: bool
) -> tuple[list[Scored], list[Answer]]:
    answers: dict[tuple[str, str], Answer] = {}
    spent: list[Answer] = []
    for condition in CONDITIONS:
        for case in cases:
            user = prompt(case, condition)
            if user is None:
                continue
            answer = ask(
                RECONSTRUCT, user, cache=cache, model=model, offline=offline, max_tokens=600
            )
            answers[(case.case_id, condition)] = answer
            spent.append(answer)

    # The chance control: the next case of the same agent, under the same condition.
    by_role: dict[str, list[Case]] = defaultdict(list)
    for case in cases:
        by_role[case.role].append(case)
    control: dict[str, str] = {}
    for group in by_role.values():
        ordered = sorted(group, key=lambda c: c.case_id)
        for i, case in enumerate(ordered):
            if len(ordered) > 1:
                control[case.case_id] = ordered[(i + 1) % len(ordered)].case_id

    scored = []
    for (case_id, condition), answer in answers.items():
        case = next(c for c in cases if c.case_id == case_id)
        data = answer.data
        text = json.dumps(data, ensure_ascii=False)
        other = answers.get((control.get(case_id, ""), condition))
        other_text = json.dumps(other.data, ensure_ascii=False) if other else ""
        searchable = {
            k: v
            for k, v in case.rule_inputs.items()
            if not isinstance(v, bool) and v is not None and v != ""
        }
        reason = str(data.get("reason") or "")
        judged = ask(
            JUDGE,
            f"REFERENCE\n{case.justification}\n\nCANDIDATE\n{reason or '(no reason given)'}",
            cache=cache,
            model=model,
            offline=offline,
            max_tokens=150,
        )
        spent.append(judged)
        score = judged.data.get("score")
        recorded_reason = case.escalation_reason or None
        given_reason = data.get("escalation_reason") or None
        if given_reason in ("null", "none", ""):
            given_reason = None
        scored.append(
            Scored(
                case_id=case_id,
                source=case.source,
                role=case.role,
                condition=condition,
                verdict_correct=same_verdict(str(data.get("verdict", "")), case.verdict),
                escalation_correct=(given_reason == recorded_reason)
                if case.role == "ca1"
                else None,
                searchable=len(searchable),
                values_found=sum(_found(v, text) for v in searchable.values()),
                values_by_chance=sum(_found(v, other_text) for v in searchable.values()),
                judge_score=score if score in (0, 1, 2) else None,
                answer=data,
                judge=judged.data,
                calls=[as_record(answer), as_record(judged)],
            )
        )
    return scored, spent


def summarise(scored: list[Scored]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    for condition in CONDITIONS:
        rows = [s for s in scored if s.condition == condition]
        if not rows:
            continue
        ca1 = [s for s in rows if s.escalation_correct is not None and s.source == "fault"]
        judged = [s.judge_score for s in rows if s.judge_score is not None]
        searchable = sum(s.searchable for s in rows)
        out[condition] = {
            "decisions": len(rows),
            "verdict_correct": sum(s.verdict_correct for s in rows),
            "ca1_escalations": len(ca1),
            "ca1_escalation_reason_correct": sum(bool(s.escalation_correct) for s in ca1),
            "searchable_values": searchable,
            "values_found": sum(s.values_found for s in rows),
            "values_by_chance": sum(s.values_by_chance for s in rows),
            "judge": {score: judged.count(score) for score in (0, 1, 2)},
            "by_role": {
                role: {
                    "decisions": len(r),
                    "judge_2": sum(s.judge_score == 2 for s in r),
                    "values_found": sum(s.values_found for s in r),
                    "values_by_chance": sum(s.values_by_chance for s in r),
                    "searchable": sum(s.searchable for s in r),
                }
                for role in ("ca1", "ca2", "ca3", "cax")
                if (r := [s for s in rows if s.role == role])
            },
        }
    return out


def render(summary: dict[str, Any]) -> str:
    lines = [
        "| Condition | Decisions | Verdict right | CA1 cause right | Values found (chance) "
        "| Reason 2 / 1 / 0 |",
        "|---|--:|--:|--:|--:|--:|",
    ]
    for condition, row in summary.items():
        j = row["judge"]
        lines.append(
            f"| {condition} | {row['decisions']} | {row['verdict_correct']} | "
            f"{row['ca1_escalation_reason_correct']} of {row['ca1_escalations']} | "
            f"{row['values_found']} ({row['values_by_chance']}) of "
            f"{row['searchable_values']} | "
            f"{j[2]} / {j[1]} / {j[0]} |"
        )
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--reference", type=Path, default=Path("experiments/decision-replay"))
    parser.add_argument("--corpus", type=Path, default=Path("experiments/corpus-24"))
    parser.add_argument("--faults", type=Path, default=Path("out/study/faults"))
    parser.add_argument("--out-dir", type=Path, default=Path("out/study"))
    parser.add_argument("--model", default=DEFAULT_MODEL)
    parser.add_argument("--offline", action="store_true", help="answer only from the cache")
    parser.add_argument("--roles", default="ca1,ca2,ca3,cax", help="agents to include")
    parser.add_argument("--dry-run", action="store_true", help="count prompts and tokens only")
    args = parser.parse_args(argv)

    roles = {r.strip() for r in args.roles.split(",")}
    cases = [c for c in gather(args.reference, args.corpus, args.faults) if c.role in roles]
    if args.dry_run:
        chars: dict[str, int] = defaultdict(int)
        calls: dict[str, int] = defaultdict(int)
        for condition in CONDITIONS:
            for case in cases:
                user = prompt(case, condition)
                if user is not None:
                    calls[condition] += 1
                    chars[condition] += len(user) + len(RECONSTRUCT)
        for condition in CONDITIONS:
            tokens = chars[condition] / 4 / 1e3
            print(f"{condition:<18} {calls[condition]:>4} calls  ~{tokens:,.0f}k input tokens")
        sources = ", ".join(sorted({c.source for c in cases}))
        print(f"cases: {len(cases)} ({sources}); plus one judge call per answer")
        return 0

    scored, spent = run(cases, args.out_dir / "cache", model=args.model, offline=args.offline)
    summary = summarise(scored)
    args.out_dir.mkdir(parents=True, exist_ok=True)
    (args.out_dir / "reconstruct.json").write_text(
        json.dumps(
            {
                "model": args.model,
                "cases": len(cases),
                "accounting": accounting(spent),
                "summary": summary,
                "scored": [s.__dict__ for s in scored],
            },
            indent=1,
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    table = render(summary)
    (args.out_dir / "RECONSTRUCT.md").write_text(table + "\n", encoding="utf-8")
    print(table)
    print(json.dumps(accounting(spent)))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
