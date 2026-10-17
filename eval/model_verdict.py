"""When the model decides CAx's verdict: how often it agrees with the code check, and what
the record holds of its reason.

    python -m eval.model_verdict --prov-dir out/mv-ref out/mv-corpus

Reads CAx's records from runs made with ``CAX_VERDICT=model``. For each decision it reports
the model's verdict, the code check's verdict recorded beside it, the issue the model named,
and whether the decision replays from its record (which re-applies ``cax.review/v1`` to the
model's recorded answer; the model itself is not re-run).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path
from typing import Any

import eval.replay  # noqa: F401  registers every agent's rule
from yprov4agenticai.rules import replay


def decisions(prov_dirs: list[Path]) -> list[dict[str, Any]]:
    rows = []
    for prov_dir in prov_dirs:
        for path in sorted(prov_dir.glob("*__cax_prov.json")):
            record = json.loads(path.read_text(encoding="utf-8"))
            decision = next(a for a in record["activity"].values() if "yp:verdict" in a)
            if decision.get("yp:verdict_source") != "model":
                continue
            inputs = json.loads(decision["yp:rule_inputs"])
            rows.append(
                {
                    "record": f"{prov_dir.name}/{path.name}",
                    "verdict": decision["yp:verdict"],
                    "code_verdict": decision["yp:code_verdict"],
                    "issue": inputs.get("model_issue"),
                    "evidence": inputs.get("model_evidence"),
                    "parsed": inputs.get("review_parsed"),
                    "replays": {r.status for r in replay(record)} == {"replayed"},
                }
            )
    return rows


def summarise(rows: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "decisions": len(rows),
        "replayed": sum(r["replays"] for r in rows),
        "unparsed": sum(not r["parsed"] for r in rows),
        "model_verdicts": dict(Counter(r["verdict"] for r in rows)),
        "code_verdicts": dict(Counter(r["code_verdict"] for r in rows)),
        "agree": sum(r["verdict"] == r["code_verdict"] for r in rows),
        "pairs": dict(
            Counter(f"model {r['verdict']} / code {r['code_verdict']}" for r in rows)
        ),
        "issues": dict(Counter(r["issue"] for r in rows)),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--prov-dir", type=Path, nargs="+", required=True)
    parser.add_argument("--json", type=Path, help="also write the rows and summary here")
    args = parser.parse_args(argv)
    rows = decisions(args.prov_dir)
    summary = summarise(rows)
    if args.json:
        args.json.write_text(
            json.dumps({"summary": summary, "rows": rows}, indent=1), encoding="utf-8"
        )
    print(json.dumps(summary, indent=1))
    disagreements = [r for r in rows if r["verdict"] != r["code_verdict"]]
    for row in disagreements:
        print(
            f"  {row['record']}: model {row['verdict']} ({row['issue']}: "
            f"{row['evidence']!r}), code {row['code_verdict']}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
