"""Strategy A observed through a real framework: LangGraph nodes, LangChain's callback handler.

    uv sync --extra eval --extra frameworks
    python -m eval.framework_baseline --data-dir corpus/yprov4ml --out-dir out/framework

Strategy A elsewhere is a handler we wrote, fed a :class:`~yprov4agenticai.observer.StepEvent`
per lifecycle step. A reviewer is entitled to ask whether a handler we wrote sees what a real
one would. This runs the four operational agents, unmodified, as the nodes of a LangGraph
``StateGraph`` (the producer's record fans out to CAx, CA2 and CA3, and CA1 persists once all
three have finished) and observes the run with a subclass of LangChain's own
``BaseCallbackHandler``, registered the way a deployment registers one: through the graph's
run config. Everything the handler is handed (each chain's inputs and outputs, its name, and
the graph's state at every node boundary) is kept as its visible world.

The agents run under the real tracker, so each decision's record names the values its rule
read; those records go to disk, never into the graph state, so the handler cannot see them.
What it sees is what the framework passes: the input record, and each agent's one-line
result. The question is the one Table 3 asks of the hand-built handler, of each rule input:
did the framework's own observer see it?
"""

from __future__ import annotations

import argparse
import json
import operator
from pathlib import Path
from typing import Annotated, Any, TypedDict
from uuid import UUID

from eval.baseline import AGENT_ROLES
from eval.replay import input_visibility, render_inputs


class PipelineState(TypedDict):
    """What a LangGraph pipeline of these agents would carry between nodes."""

    session: str
    record: dict[str, Any]
    subject: str
    # Each agent's one-line result, keyed by role. Merged, because CAx, CA2 and CA3 run as
    # parallel branches and each writes its own key.
    results: Annotated[dict[str, str], operator.or_]


def _handler_class() -> type:
    from langchain_core.callbacks import BaseCallbackHandler

    class VisibleWorld(BaseCallbackHandler):
        """Keep everything LangChain hands a callback handler, serialised as it arrives."""

        def __init__(self) -> None:
            self.payloads: list[str] = []
            self.events = 0

        def _keep(self, *values: Any) -> None:
            self.events += 1
            for value in values:
                if value is not None:
                    self.payloads.append(json.dumps(value, ensure_ascii=False, default=str))

        def on_chain_start(
            self,
            serialized: dict[str, Any] | None,
            inputs: dict[str, Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> None:
            self._keep(serialized, inputs, kwargs.get("name"), kwargs.get("metadata"))

        def on_chain_end(
            self,
            outputs: dict[str, Any],
            *,
            run_id: UUID,
            parent_run_id: UUID | None = None,
            **kwargs: Any,
        ) -> None:
            self._keep(outputs)

        def on_chain_error(self, error: BaseException, **kwargs: Any) -> None:
            self._keep(repr(error))

    return VisibleWorld


def build_graph(records_out: list[tuple[str, dict[str, Any]]]) -> Any:
    """The pipeline as a LangGraph app. ``records_out`` collects each agent's PROV record
    beside the graph, so the record never enters the state a handler is shown."""
    from langgraph.graph import END, START, StateGraph

    from runtime.agents.ca1_storage import CA1Storage
    from runtime.agents.ca2_metrics import CA2Metrics
    from runtime.agents.ca3_analytics import CA3Analytics
    from runtime.agents.cax_summariser import CAxSummariser

    # No bus: the graph is the transport, so each agent is built without one.
    classes: dict[str, type] = {"cax": CAxSummariser, "ca2": CA2Metrics, "ca3": CA3Analytics}
    consumers = {role: cls(None) for role, cls in classes.items()}
    storage_class: type = CA1Storage
    storage = storage_class(None)

    def consumer(role: str) -> Any:
        agent = consumers[role]

        def node(state: PipelineState) -> dict[str, Any]:
            record, line = agent.process(state["session"], state["record"], state["subject"])
            records_out.append((f"{state['session']}__{role}", record))
            return {"results": {role: line}}

        return node

    def persist(state: PipelineState) -> dict[str, Any]:
        acked = sorted(state["results"])
        record, line = storage._do_persist(
            state["session"], state["record"], acked, "quorum", "all consumers finished"
        )
        records_out.append((f"{state['session']}__ca1", record))
        return {"results": {"ca1": line}}

    graph = StateGraph(PipelineState)
    for role in consumers:
        graph.add_node(role, consumer(role))
        graph.add_edge(START, role)
        graph.add_edge(role, "ca1")
    graph.add_node("ca1", persist)
    graph.add_edge("ca1", END)
    return graph.compile()


def run(data_dir: Path, out_dir: Path) -> dict[str, Any]:
    import os

    out_dir.mkdir(parents=True, exist_ok=True)
    os.environ.pop("PROV_INSTRUMENTATION", None)
    from eval.bench_overhead import _redirect_output

    _redirect_output(out_dir)

    records: list[tuple[str, dict[str, Any]]] = []
    app = build_graph(records)
    # One handler per invocation, as a deployment attaches one per run, so each session's
    # visible world is kept apart and a value is looked for where it could have been seen.
    haystacks: dict[str, str] = {}
    events = chars = 0
    paths = sorted(data_dir.glob("**/*.json"))
    for path in paths:
        handler = _handler_class()()
        state: PipelineState = {
            "session": path.stem,
            "record": json.loads(path.read_text(encoding="utf-8")),
            "subject": "producer.yprov4ml.framework",
            "results": {},
        }
        app.invoke(state, config={"callbacks": [handler]})
        haystacks[path.stem] = "\n".join(handler.payloads)
        events += handler.events
        chars += len(haystacks[path.stem])

    named = [(Path(f"{name}_prov.json"), doc) for name, doc in records]
    visibility = input_visibility(named, haystacks)
    return {
        "records": len(paths),
        "decisions": len(named),
        "callback_events": events,
        "payload_chars": chars,
        "inputs": {
            role: vars(tally)
            for role, tally in sorted(
                visibility.items(), key=lambda item: AGENT_ROLES.index(item[0])
            )
        },
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--data-dir", type=Path, default=Path("corpus/yprov4ml"))
    parser.add_argument("--out-dir", type=Path, default=Path("out/framework"))
    args = parser.parse_args(argv)
    report = run(args.data_dir, args.out_dir)
    (args.out_dir / "framework.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
    print(
        f"{report['records']} records, {report['decisions']} decisions through LangGraph; "
        f"the LangChain handler received {report['callback_events']} events, "
        f"{report['payload_chars']:,} characters.\n"
    )
    print(render_inputs(report["inputs"]))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
