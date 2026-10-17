"""Decision rules as named, versioned functions, so a recorded decision can be replayed.

A justification is a sentence, and a sentence can say anything. Nothing in the record ties
"Integrity check failed" to the branch of code that produced ESCALATE, which leaves a
hand-written justification exactly as weak as a model's chain of thought: plausible, and
unchecked. A :class:`Rule` closes that gap from the other side. The agent states its
verdict as the output of a named function applied to named values:

    def persist(e):
        return {"yp:verdict": "ACCEPT" if e["ok"] else "ESCALATE"}

    STORE_RULE = Rule("ca1.persist/v1", persist)

    tracker.decide(verdict, justification, rule=STORE_RULE, evidence={"ok": integrity_ok})

and :meth:`~yprov4agenticai.tracker.ProvTracker.decide` refuses a verdict its own rule does
not produce. The record then carries ``yp:rule`` and ``yp:rule_inputs``, and anyone holding the
code at that version can re-apply the rule to the evidence and get the verdict back
(:func:`replay`). A justification that disagrees with its evidence is then detectable, and a
verdict that disagrees with its rule cannot be recorded at all.

What this does not do is make the rule *right*. It makes the record faithful to the code
that ran, which is the property a justification written by the same code otherwise lacks.
"""

from __future__ import annotations

import json
import math
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field

#: An evidence value: what a rule may read. Scalars only, so the canonical form is exact
#: and a replay compares like with like.
Scalar = str | int | float | bool | None

#: The outputs a rule decides, keyed by the attribute that records each one. ``yp:verdict``
#: always; ``yp:escalation_reason`` where a verdict has more than one cause.
Outcome = Mapping[str, str]

_REGISTRY: dict[str, Rule] = {}


@dataclass(frozen=True)
class Rule:
    """A decision rule: a pure function from evidence to the decision's outputs.

    ``name`` carries a version suffix (``/v1``). A rule whose logic changes gets a new
    name, never an edited body under the old one, so a record that names a rule names
    exactly one function.
    """

    name: str
    apply: Callable[[Mapping[str, Scalar]], Outcome] = field(compare=False)

    def __post_init__(self) -> None:
        if "/v" not in self.name:
            raise ValueError(f"rule name {self.name!r} carries no version (expected '.../vN')")
        existing = _REGISTRY.get(self.name)
        if existing is not None and existing.apply is not self.apply:
            raise ValueError(f"rule {self.name!r} is already registered with another body")
        _REGISTRY[self.name] = self


def lookup(name: str) -> Rule | None:
    return _REGISTRY.get(name)


def registered() -> dict[str, Rule]:
    return dict(_REGISTRY)


def canonical(evidence: Mapping[str, Scalar]) -> str:
    """The evidence as it is recorded: sorted keys, no whitespace, finite numbers only."""
    for key, value in evidence.items():
        if not isinstance(value, str | int | float | bool | type(None)):
            raise TypeError(f"evidence {key!r} is a {type(value).__name__}, not a scalar")
        if isinstance(value, float) and not math.isfinite(value):
            raise ValueError(f"evidence {key!r} is {value}, which has no exact JSON form")
    return json.dumps(dict(evidence), sort_keys=True, separators=(",", ":"))


def check(rule: Rule, evidence: Mapping[str, Scalar], recorded: Mapping[str, str]) -> list[str]:
    """Where the rule, applied to the evidence, disagrees with the record. Empty if nowhere."""
    produced = rule.apply(evidence)
    return [
        f"{key}: rule {rule.name} gives {value!r}, record says {recorded.get(key, '')!r}"
        for key, value in produced.items()
        if (recorded.get(key) or "") != (value or "")
    ]


@dataclass(frozen=True)
class Replay:
    """One recorded decision, re-derived from its own record."""

    activity: str
    rule: str
    verdict: str
    status: str  # "replayed", "mismatch", "unknown-rule", "no-rule"
    detail: str = ""


def replay(doc: Mapping) -> list[Replay]:
    """Re-apply every recorded rule to its recorded evidence and compare with the record."""
    results = []
    for aid, act in (doc.get("activity") or {}).items():
        verdict = act.get("yp:verdict")
        if verdict is None:
            continue
        name = act.get("yp:rule")
        if not name:
            results.append(Replay(aid, "", verdict, "no-rule"))
            continue
        rule = lookup(name)
        if rule is None:
            results.append(Replay(aid, name, verdict, "unknown-rule"))
            continue
        evidence = json.loads(act.get("yp:rule_inputs") or "{}")
        problems = check(rule, evidence, act)
        status = "mismatch" if problems else "replayed"
        results.append(Replay(aid, name, verdict, status, "; ".join(problems)))
    return results
