"""
evaluate.py — turn one demonstrator run into the paper's result tables.

Reads the PROV-JSON the agents emitted into the output directory and computes every
number Paper 1 needs, so the evaluation is reproducible from the artifacts rather than
hand-copied:

    E1 Coverage              typed activities / entities / relations, per agent
    E2 Justification         % of DecisionActivities carrying a non-empty yp:justification
    E3 Strategy A vs B       what callback-level instrumentation could have captured
    E4 Reflexivity           which agents were themselves governed, by whom
    E5 Governance            MC verdict tally

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


def coverage(doc: dict[str, Any]) -> dict[str, int]:
    return {
        "activities": len(doc.get("activity") or {}),
        "entities": len(doc.get("entity") or {}),
        "relations": sum(len(doc.get(k) or {}) for k in REL_KEYS),
    }


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


def main() -> None:
    ap = argparse.ArgumentParser(description="Evaluate a demonstrator run.")
    ap.add_argument("--out-dir", default="out", help="directory holding *_prov.json")
    ap.add_argument("--markdown", help="also write the tables to this markdown file")
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

    # ── E2 justification ───────────────────────────────────────────────────────
    tot_d = tot_j = 0
    emit("## E2 — Justification coverage (the headline claim)")
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

    # ── E3 Strategy A vs B ─────────────────────────────────────────────────────
    tot = {"activities": 0, "entities": 0, "relations": 0}
    for _, _, doc in runs:
        c = coverage(doc)
        for k in tot:
            tot[k] += c[k]
    emit("## E3 — Strategy A (callbacks) vs Strategy B (explicit instrumentation)")
    emit()
    emit(
        "Same agents, same input. Strategy A figures are structural: a framework callback "
        "fires *after* a node returns and cannot read that node's local variables, so the "
        "decision rationale is unreachable by construction."
    )
    emit()
    emit("| Signal | Strategy A | Strategy B (this demo) |")
    emit("|---|--:|--:|")
    emit(f"| Typed activities | 0 | {tot['activities']} |")
    emit(f"| Typed entities | 0 | {tot['entities']} |")
    emit(f"| Lineage relations | 0 | {tot['relations']} |")
    emit(f"| Decisions with `yp:justification` | 0 (impossible) | {tot_j} |")
    emit("| W3C PROV-JSON output | no | yes |")
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
