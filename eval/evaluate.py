"""
evaluate.py — turn one demonstrator run into the paper's result tables.

Reads the PROV-JSON the agents emitted into the output directory and computes every
number Paper 1 needs, so the evaluation is reproducible from the artifacts rather than
hand-copied:

    E1 Coverage              typed activities / entities / relations, per agent
    E2 Coverage              % of DecisionActivities justified, plus SHACL conformance
    E3 Strategy A vs B       measured against the callback baseline (eval/baseline.py)
    E4 Reflexivity           which agents were themselves governed, by whom
    E5 Governance            MC verdict tally

E3 needs the Strategy A observation stream, which `python run_local.py --baseline`
records alongside the run. Without it, E3 reports that it was not measured rather than
substituting an assumption.

Usage:
    python -m eval.evaluate                      # defaults to ./out
    python -m eval.evaluate --out-dir ./out --markdown results.md
"""

from __future__ import annotations

import argparse
import json
from collections import defaultdict
from pathlib import Path
from typing import Any

from eval.baseline import EVENTS_FILE, compare, render

REL_KEYS = (
    "used",
    "wasGeneratedBy",
    "wasInformedBy",
    "wasDerivedFrom",
    "wasAssociatedWith",
    "actedOnBehalfOf",
)

#: display names, in lifecycle order
ROLE_NAMES = {
    "ca1": "CA1 StorageAgent",
    "cax": "CAx NL_Summarizer",
    "ca2": "CA2 Metrics_Visualizer",
    "ca3": "CA3 Analytics_Agent",
    "mc1": "MC1 ComplianceChecker",
    "mc3": "MC3 AuditabilityAgent",
}
OPERATIONAL = ("ca1", "cax", "ca2", "ca3")
META = ("mc1", "mc3")


def _type_of(node: dict[str, Any]) -> str:
    t = node.get("prov:type")
    return str(t.get("$", "")) if isinstance(t, dict) else str(t or "")


def _agent_label(doc: dict[str, Any]) -> str:
    for ag in (doc.get("agent") or {}).values():
        if ag.get("prov:label"):
            return str(ag["prov:label"])
    return "?"


def load_runs(out_dir: Path) -> list[tuple[str, str, dict[str, Any]]]:
    """Return (session, role, doc) for every emitted PROV file.

    Filenames look like  <session>__<role>_prov.json, where a governed record's
    session itself carries the governed agent, e.g. example_0__cax__mc1_prov.json.
    """
    runs = []
    for path in sorted(out_dir.glob("*_prov.json")):
        stem = path.stem[: -len("_prov")]  # strip the trailing _prov
        session, _, role = stem.rpartition("__")
        if not session:
            session, role = stem, "?"
        try:
            runs.append((session, role, json.loads(path.read_text(encoding="utf-8"))))
        except Exception as exc:
            print(f"  ! skipped {path.name}: {exc}")
    return runs


#: How each relation kind came to be in the record. The distinction is the substance of
#: review finding B3: a single "relations" total mixes claims established in three
#: different ways, and quoting it invites the reply that it counts steps rather than
#: structure.
#:
#:   observed   a step stated that it read this entity, or wrote it
#:   derived    computed from those statements by PROV's own definition
#:   declared   a step stated that one entity came from another
#:   attributed one edge per activity, to the agent that ran it — mechanical by
#:              construction, and the largest single contributor to the old total
RELATION_PROVENANCE = {
    "used": "observed",
    "wasGeneratedBy": "observed",
    "wasInformedBy": "derived",
    "wasDerivedFrom": "declared",
    "wasAssociatedWith": "attributed",
    "actedOnBehalfOf": "attributed",
}


def coverage(doc: dict[str, Any]) -> dict[str, int]:
    counts = {
        "activities": len(doc.get("activity") or {}),
        "entities": len(doc.get("entity") or {}),
        "relations": sum(len(doc.get(k) or {}) for k in REL_KEYS),
    }
    for key in REL_KEYS:
        counts[key] = len(doc.get(key) or {})
    for kind in ("observed", "derived", "declared", "attributed"):
        counts[kind] = sum(
            len(doc.get(key) or {})
            for key, group in RELATION_PROVENANCE.items()
            if group == kind
        )
    return counts


def decisions(doc: dict[str, Any]) -> tuple[int, int]:
    """(number of decision activities, number carrying a justification).

    Counts ``yp:DecisionActivity`` only. The previous definition also admitted
    ``ComplianceCheckActivity`` and ``AuditActivity``, which widened the denominator to
    include the meta agents' work steps — and did so precisely because those agents
    recorded a verdict without a decision activity. Rather than fitting the metric to that
    omission, the agents now emit the decision step, so the metric can name one thing.

    This is a coverage figure: it says every decision point is instrumented, not that the
    justifications are informative. See todo.md B1 on why coverage cannot be the headline.
    """
    total = justified = 0
    for a in (doc.get("activity") or {}).values():
        if _type_of(a) == "yp:DecisionActivity":
            total += 1
            if str(a.get("yp:justification", "")).strip():
                justified += 1
    return total, justified


def _shacl_section(out_dir: Path) -> str:
    """The same claim, judged externally: do the records satisfy the declared shapes?

    This is what gives E2 something to stand on. The rule — every yp:DecisionActivity
    carries a non-empty yp:justification — is declared in vocab/shapes.ttl and applied
    here by pyshacl over RDF converted by the reference `prov` library. Neither the rule
    nor the engine is ours, which is the difference between a conformance statement and
    a self-report.
    """
    try:
        from eval.validate_shapes import validate_run
    except ImportError:
        return "*Shape validation not run: the `eval` extra provides rdflib and pyshacl.*"
    try:
        report = validate_run(out_dir)
    except Exception as exc:  # pragma: no cover - a toolchain problem, not a result
        return f"*Shape validation could not run: {exc}*"

    verdict = "conform" if report.conforms else "**do not conform**"
    return (
        f"**{report.conforming}/{report.documents} records {verdict} to "
        f"`vocab/shapes.ttl`**, validated with pyshacl over RDF converted by the "
        f"reference `prov` library. The rule being checked — every decision carries a "
        f"non-empty justification — is declared in the vocabulary rather than written "
        f"into the agent that enforces it, so this is a statement about a standard and "
        f"not about an implementation. Asking the two to agree found a real divergence: "
        f"MC1 accepted a justification of pure whitespace and the shape did not."
    )


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate a demonstrator run.")
    ap.add_argument("--out-dir", default="out", help="directory holding *_prov.json")
    ap.add_argument("--markdown", help="also write the tables to this markdown file")
    ap.add_argument(
        "--baseline-dir",
        default="out/baseline",
        help="Strategy A's observation stream, for E3 (default: out/baseline)",
    )
    args = ap.parse_args()

    out_dir = Path(args.out_dir)
    if not out_dir.is_dir():
        raise SystemExit(f"no such output directory: {out_dir} — run the demo first")

    runs = load_runs(out_dir)
    if not runs:
        raise SystemExit(f"no *_prov.json found in {out_dir} — run the demo first")

    lines: list[str] = []

    def emit(text: str = "") -> None:
        print(text)
        lines.append(text)

    emit(f"# Demonstrator evaluation — {len(runs)} emitted PROV records")
    emit()
    emit(f"Source: `{out_dir}`")
    emit()

    # ── E1 coverage ────────────────────────────────────────────────────────────
    per_role: dict[str, list[dict[str, int]]] = defaultdict(list)
    for _, role, doc in runs:
        per_role[role].append(coverage(doc))

    emit("## E1 — Structural coverage (per agent, mean over sessions)")
    emit()
    emit("| Agent | Records | Activities | Entities | Relations |")
    emit("|---|--:|--:|--:|--:|")
    for role in list(OPERATIONAL) + list(META):
        rows = per_role.get(role)
        if not rows:
            continue
        n = len(rows)
        emit(
            f"| {ROLE_NAMES.get(role, role)} | {n} | "
            f"{sum(r['activities'] for r in rows) / n:.1f} | "
            f"{sum(r['entities'] for r in rows) / n:.1f} | "
            f"{sum(r['relations'] for r in rows) / n:.1f} |"
        )
    emit()

    # ── B3: the relation total, split by what established each edge ────────────
    every = [coverage(doc) for _, _, doc in runs]
    total = {
        key: sum(row.get(key, 0) for row in every)
        for key in (
            "relations",
            "observed",
            "derived",
            "declared",
            "attributed",
            *REL_KEYS,
        )
    }
    emit("### E1b — Relations, by what established them")
    emit()
    emit(
        "A single relation total mixes three kinds of claim and one piece of "
        "bookkeeping, which is why it should not be quoted on its own (todo.md B3). "
        "**Observed** edges are what a step said it read or wrote. **Derived** edges "
        "follow from those by PROV's own definition of `wasInformedBy`. **Declared** "
        "edges are derivations a step asserted. **Attributed** edges are one per "
        "activity, to the agent that ran it — mechanical by construction, and the "
        "largest single contributor to the total."
    )
    emit()
    emit("| How it was established | Relations | Share | Which |")
    emit("|---|--:|--:|---|")
    for kind, keys in (
        ("Observed", ("used", "wasGeneratedBy")),
        ("Derived", ("wasInformedBy",)),
        ("Declared", ("wasDerivedFrom",)),
        ("Attributed", ("wasAssociatedWith", "actedOnBehalfOf")),
    ):
        count = total[kind.lower()]
        share = 100.0 * count / total["relations"] if total["relations"] else 0.0
        names = ", ".join(f"`{key}` {total[key]}" for key in keys if total[key])
        emit(f"| {kind} | {count} | {share:.0f}% | {names or '—'} |")
    emit(f"| **Total** | **{total['relations']}** | 100% | |")
    emit()
    structural = total["observed"] + total["derived"] + total["declared"]
    emit(
        f"**The structural claim is {structural}, not {total['relations']}.** The "
        f"remaining {total['attributed']} are agent attributions: one per activity, so "
        f"they measure how many steps ran rather than how they were connected. Report "
        f"the split."
    )
    emit()

    # ── E2 justification ───────────────────────────────────────────────────────
    #
    # Not the headline claim, and the table says so. The code that counts decisions is
    # the code that justifies them, so no run of this system could report another number
    # (todo.md B1). What makes the figure worth printing is the judge, not the ratio:
    # since Phase 6 the rule is declared in vocab/shapes.ttl and applied by an
    # independent SHACL engine, so "every decision is justified" is a conformance
    # statement against an external standard rather than a self-report.
    tot_d = tot_j = 0
    emit("## E2 — Instrumentation coverage (not the headline claim)")
    emit()
    emit(
        "Read this as a conformance check, not a result. Every decision point in this "
        "system is instrumented, which is worth establishing — but the code that counts "
        "decisions is the code that justifies them, so no run could produce another "
        "number. The figure says nothing about whether a justification is *informative*; "
        "see E3 for the claim that does the work."
    )
    emit()
    emit("| Agent | Decisions | With `yp:justification` | Coverage |")
    emit("|---|--:|--:|--:|")
    for role in list(OPERATIONAL) + list(META):
        rows = [doc for _, r, doc in runs if r == role]
        if not rows:
            continue
        d = j = 0
        for doc in rows:
            a, b = decisions(doc)
            d += a
            j += b
        tot_d += d
        tot_j += j
        pct = f"{100.0 * j / d:.0f}%" if d else "—"
        emit(f"| {ROLE_NAMES.get(role, role)} | {d} | {j} | {pct} |")
    overall = f"{100.0 * tot_j / tot_d:.0f}%" if tot_d else "—"
    emit(f"| **All** | **{tot_d}** | **{tot_j}** | **{overall}** |")
    emit()
    emit(_shacl_section(out_dir))
    emit()

    # ── E3 Strategy A vs B ─────────────────────────────────────────────────────
    #
    # Measured, not asserted. The figures come from eval.baseline, which reconstructs the
    # same run from the stream a framework callback handler would have seen. The previous
    # version of this table filled Strategy A's column with zeros, and three of the four
    # were false: a callback handler recovers activities, entities and dataflow. Only the
    # rationale is out of reach. See todo.md B2 and PLAN.md R2.
    baseline_dir = Path(args.baseline_dir)
    events = baseline_dir / EVENTS_FILE
    if events.is_file():
        for line in render(compare(out_dir, baseline_dir)):
            emit(line)
    else:
        emit("## E3 — Strategy A (callbacks) vs Strategy B (explicit instrumentation)")
        emit()
        emit(
            f"*Not measured for this run: no observation stream at `{events}`. Produce "
            f"one with `python run_local.py --baseline`, which records what a framework "
            f"callback handler would have seen over the same work.*"
        )
        emit()

    # ── E4 reflexivity ─────────────────────────────────────────────────────────
    governed: dict[str, set] = defaultdict(set)
    for session, role, _ in runs:
        if role in META and "__" in session:
            target = session.split("__")[-1]
            governed[target].add(role)
    emit("## E4 — Reflexivity (the system tracks its own trackers)")
    emit()
    emit("| Operational agent | Governed by |")
    emit("|---|---|")
    for target in sorted(governed):
        emit(
            f"| {ROLE_NAMES.get(target, target)} | "
            f"{', '.join(ROLE_NAMES.get(m, m) for m in sorted(governed[target]))} |"
        )
    if not governed:
        emit("| (none observed) | — |")
    emit()

    # ── E5 governance verdicts ─────────────────────────────────────────────────
    verdicts: dict[str, int] = defaultdict(int)
    for _, role, doc in runs:
        for a in (doc.get("activity") or {}).values():
            v = a.get("yp:verdict")
            if v:
                verdicts[f"{role}:{v}"] += 1
    emit("## E5 — Verdict tally")
    emit()
    emit("| Agent:verdict | Count |")
    emit("|---|--:|")
    for key in sorted(verdicts):
        emit(f"| `{key}` | {verdicts[key]} |")
    if not verdicts:
        emit("| (none) | 0 |")
    emit()

    if args.markdown:
        Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"\n[eval] wrote {args.markdown}")


if __name__ == "__main__":
    main()
