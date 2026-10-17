"""The operational decisions the model-based studies are run on, each seen two ways.

A case is one decision by one operational agent, with both records of it: the explicit
record (B), which names the rule, the values it read and the justification, and the record
the callback baseline reconstructed from what it observed of the same run (A), with the
event stream it was built from. Cases come from three archives:

* the reference run: the two records bundled with yProv4ML;
* the corpus: 24 real training runs;
* the fault scenarios, run with the baseline attached, which are the only place the
  escalations and the rarer verdicts occur.
"""

from __future__ import annotations

import json
import random
from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from eval.baseline import DEFAULT_TYPE_MAP, EVENTS_FILE, CallbackLogger, read_events

#: Operational roles and the agent label each runs under (the label the handler sees).
OPERATIONAL = {
    "ca1": "StorageAgent",
    "ca2": "Metrics_Visualizer",
    "ca3": "Analytics_Agent",
    "cax": "NL_Summarizer",
}


@dataclass(frozen=True)
class Case:
    case_id: str
    source: str  # "reference", "corpus" or "fault"
    role: str
    session: str
    verdict: str
    escalation_reason: str
    rule: str
    rule_inputs: dict[str, Any]
    justification: str
    record_b: dict[str, Any]
    record_a: dict[str, Any]
    events: list[dict[str, Any]]


def _decision(record: dict[str, Any]) -> dict[str, Any]:
    return next(a for a in record["activity"].values() if "yp:verdict" in a)


def load(prov_dir: Path, events_path: Path, source: str) -> list[Case]:
    """Every operational decision in a run directory, paired with the baseline's view."""
    raw = json.loads(events_path.read_text(encoding="utf-8"))
    logger = CallbackLogger.from_events(read_events(events_path), type_map=DEFAULT_TYPE_MAP)
    a_docs = logger.documents()
    cases = []
    for path in sorted(prov_dir.glob("*_prov.json")):
        parts = path.name.removesuffix("_prov.json").split("__")
        if len(parts) != 2 or parts[1] not in OPERATIONAL:
            continue
        session, role = parts
        label = OPERATIONAL[role]
        record = json.loads(path.read_text(encoding="utf-8"))
        decision = _decision(record)
        cases.append(
            Case(
                case_id=f"{source}/{session}/{role}",
                source=source,
                role=role,
                session=session,
                verdict=decision["yp:verdict"],
                escalation_reason=decision.get("yp:escalation_reason", ""),
                rule=decision.get("yp:rule", ""),
                rule_inputs=json.loads(decision.get("yp:rule_inputs") or "{}"),
                justification=decision["yp:justification"],
                record_b=record,
                record_a=a_docs.get(f"{session}__{label}", {}),
                events=[e for e in raw if e["session"] == session and e["agent"] == label],
            )
        )
    return cases


def load_faults(faults_dir: Path) -> list[Case]:
    """The fault scenarios run with ``--baseline``: one directory per scenario."""
    cases = []
    for scenario in sorted(p for p in faults_dir.iterdir() if p.is_dir()):
        events = scenario / "baseline" / EVENTS_FILE
        if events.is_file():
            for case in load(scenario, events, "fault"):
                cases.append(
                    Case(**{**case.__dict__, "case_id": f"fault/{scenario.name}/{case.role}"})
                )
    return cases


def stratified(cases: Iterable[Case], per_role: int, seed: int) -> list[Case]:
    """A fixed sample of up to ``per_role`` cases per agent, the same on every run."""
    rng = random.Random(seed)
    by_role: dict[str, list[Case]] = {}
    for case in cases:
        by_role.setdefault(case.role, []).append(case)
    picked = []
    for role in sorted(by_role):
        pool = sorted(by_role[role], key=lambda c: c.case_id)
        picked += sorted(rng.sample(pool, min(per_role, len(pool))), key=lambda c: c.case_id)
    return picked


def compact(record: dict[str, Any]) -> str:
    """A record as the model is shown it: JSON without the indentation."""
    return json.dumps(record, ensure_ascii=False, separators=(",", ":"))
