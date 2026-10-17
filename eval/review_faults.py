"""Plant fabrications in CAx's summaries and let the model decide: where do model and code
differ?

    python -m eval.review_faults --inputs corpus/yprov4ml --out-dir out/mv/planted

Faithful summaries give the model and the code check nothing to disagree about. This writes
four kinds of summary for every record in a corpus and lets the real model review each one,
with the code check recorded beside it:

* ``none``: a faithful summary, the deterministic one CAx writes without a model;
* ``large_figure``: the same, plus a figure the record does not contain and that the code
  check is built to catch ("48,000 training samples");
* ``small_figure``: plus an invented figure below the code check's floor ("4 GPUs");
* ``name``: plus an invented name ("trained on the ImageNet dataset"), which a figure check
  cannot see at all.

Only the writer is scripted; the review is a real model call through ``runtime.llm``. Each
kind lands in its own directory with the callback baseline's events, in the layout
``eval.cases.load_faults`` reads, so the reconstruction and operator studies run on it.
"""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from eval.baseline import DEFAULT_TYPE_MAP, EVENTS_FILE, CallbackLogger, write_events
from eval.fault_injection import _headless
from runtime.agents.cax_summariser import CAxSummariser
from runtime.llm import Completion, get_llm
from runtime.util import prov_counts
from yprov4agenticai import LightweightStore
from yprov4agenticai.observer import observe
from yprov4agenticai.prompts import ACT_SYSTEM

PLANTED = {
    "none": "",
    "large_figure": " The run processed 48,000 training samples.",
    "small_figure": " Training used 4 GPUs.",
    "name": " The model was trained on the ImageNet dataset.",
}


class PlantedWriter:
    """The summary is scripted; every other call goes to the real model."""

    def __init__(self, real: Any, addition: str) -> None:
        self.real, self.addition = real, addition
        self.model, self.temperature, self.max_tokens = real.model, 0.0, real.max_tokens

    def complete(self, prompt: Any, user: str) -> Completion:
        if prompt.id != ACT_SYSTEM.id:
            return self.real.complete(prompt, user)
        return Completion(
            text=self.faithful + self.addition,
            model="planted",
            temperature=0.0,
            max_tokens=0,
            latency_s=0.0,
            prompt=prompt,
            finish_reason="stop",
        )

    faithful = ""


def run(inputs: Path, out_dir: Path) -> dict[str, Any]:
    real = get_llm()
    if real is None:
        raise SystemExit("this study needs a model: set OPENAI_API_KEY")
    rows = []
    for kind, addition in PLANTED.items():
        target = out_dir / kind
        target.mkdir(parents=True, exist_ok=True)
        logger = CallbackLogger(type_map=DEFAULT_TYPE_MAP)
        for path in sorted(inputs.glob("*.json")):
            doc = json.loads(path.read_text(encoding="utf-8"))
            writer = PlantedWriter(real, addition)
            agent = _headless(
                CAxSummariser,
                target,
                store=LightweightStore(root=str(target / "store")),
                llm=writer,
            )
            # The faithful text is the agent's own deterministic summary of this record.
            agent.llm = None
            writer.faithful, _, _ = agent._summarise(doc, prov_counts(doc))
            agent.llm = writer
            with observe(logger.record):
                record, line = agent.process(path.stem, doc, "producer.yprov4ml.planted")
            decision = next(a for a in record["activity"].values() if "yp:verdict" in a)
            rows.append(
                {
                    "kind": kind,
                    "record": path.stem,
                    "verdict": decision["yp:verdict"],
                    "code_verdict": decision.get("yp:code_verdict"),
                }
            )
            print(f"[planted] {kind:<13} {path.stem}: {line}", flush=True)
        write_events(target / "baseline" / EVENTS_FILE, logger.events())
    return {"rows": rows}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--inputs", type=Path, default=Path("corpus/yprov4ml"))
    parser.add_argument("--out-dir", type=Path, default=Path("out/mv/planted"))
    args = parser.parse_args(argv)
    result = run(args.inputs, args.out_dir)
    (args.out_dir / "planted.json").write_text(json.dumps(result, indent=1), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
