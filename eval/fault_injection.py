"""Make the alternative verdicts fire, so reachability is observed rather than asserted.

A verdict that only ever takes one value carries no information. The demonstrator's happy
path produces ACCEPT, PASS, COMPLETE and (because the sample data is what it is) REVISE and
ANOMALY — which leaves the failure verdicts reachable in code and unobserved in any
artifact. `paper/OUTLINE.md` E5 is explicit that a tally without reachability invites the
question of whether any of these checks *can* fail.

    python -m eval.fault_injection --list
    python -m eval.fault_injection --scenario mc1-unjustified
    python -m eval.fault_injection --all --out-dir out/faults

**One rule governs every scenario here: the fault goes into the input or the environment,
never into the check being tested.** A fault injection that patches the predicate it is
exercising proves only that the patch works. So a corrupted payload is produced by a store
that writes different bytes than it hashed — a faithful simulation of silent disk or
transport corruption — and a non-conforming record is produced by publishing one, not by
weakening the agent that reads it.

Seven scenarios cover every verdict of every agent that the happy path leaves unobserved,
so the verdict matrix closes at 12 of 12. Three of them are not failures at all — CA2's
ACCEPT, CAx's REVISE and CA3's OK are unreachable from the bundled sample data, which is a
finding about the *input* rather than about the agents: the real yProv4ML records carry no
numeric values to plot and declare start times without end times. Separating "this agent
only ever says one thing" from "this data only ever produces one answer" is the whole
reason to run these.

That rule turned up something worth stating in the paper. **MC1's FAIL verdict is
unreachable from records this library produces**, because `ProvTracker.decide()` refuses a
blank justification at the call site: an agent instrumented with this library cannot emit
an unjustified decision. FAIL is reachable for records the library did not write — which is
the case MC1 exists for, since a compliance checker is pointed at agents you do not
control. The scenario below therefore injects a *third-party* record, and that is the
honest framing rather than a workaround.
"""

from __future__ import annotations

import argparse
import datetime
import json
import shutil
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from yprov4agenticai import LightweightStore, now_iso

#: A session identifier per scenario, so emitted filenames say which fault produced them.
_SESSION = "fault"


@dataclass(frozen=True)
class Scenario:
    """One injected fault, and the verdict it should provoke."""

    name: str
    agent: str
    verdict: str
    condition: str
    how: str
    run: Callable[[Path], dict[str, Any]]

    def describe(self) -> str:
        return f"{self.name:<20} {self.agent:<5} expects {self.verdict:<9} — {self.how}"


# ══════════════════════════════════════════════════════════════════════════════════
#  The faults
# ══════════════════════════════════════════════════════════════════════════════════
class CorruptingStore(LightweightStore):
    """A store that hashes one payload and writes another.

    Simulates silent corruption between writing and reading — a bad sector, a truncated
    write, a transport that mangles a byte. The point is that it is silent: ``put``
    succeeds, returns a well-formed pointer, and the recorded hash is of data that is no
    longer there. Nothing but a round-trip read detects it, which is why CA1 performs one.

    Note what is *not* patched: CA1's integrity check, its verdict logic, and its
    justification are untouched. The fault is in the storage layer, where a real one would
    be.
    """

    def put(self, payload: str, kind: str = "artifact") -> dict:
        pointer = super().put(payload, kind=kind)
        if self.root is None:  # pragma: no cover - the scenarios always use a root
            return pointer
        path = self._payload_path(pointer["yp:pid"])
        if path is not None and path.is_file():
            raw = bytearray(path.read_bytes())
            raw[0] = raw[0] ^ 0x01  # one bit, which is all it takes
            path.write_bytes(bytes(raw))
        return pointer


#: The input record the scenarios read; ``--inputs`` repoints it, one record at a time.
_INPUT: Path | None = None

#: Scenarios whose fault is in the environment, not the record, so they mean the same thing
#: on any input and can be repeated over a corpus.
INPUT_INDEPENDENT = ("ca1-integrity", "ca1-timeout", "ca1-incomplete", "stream-gap")


def _producer_record() -> dict[str, Any]:
    """A real yProv4ML record from the data the demonstrator replays."""
    if _INPUT is not None:
        return json.loads(_INPUT.read_text(encoding="utf-8"))
    candidates = sorted(Path("data/yprov4ml").glob("**/*.json"))
    if not candidates:
        raise SystemExit("no yProv4ML records under data/yprov4ml")
    return json.loads(candidates[0].read_text(encoding="utf-8"))


def _rogue_record(*, justified: bool = True, timed: bool = True) -> dict[str, Any]:
    """A record from an agent this library did not instrument.

    Built by hand precisely because the library cannot produce it: ``decide()`` rejects a
    blank justification, and the capture API always writes both instants. A third-party
    agent is under no such discipline, and that is the input a governance layer has to
    cope with.
    """
    activity: dict[str, Any] = {
        "prov:type": {"$": "yp:DecisionActivity", "type": "prov:QUALIFIED_NAME"},
        "prov:label": "rogue agent: decide",
        "yp:verdict": "ACCEPT",
    }
    if justified:
        activity["yp:justification"] = "The third-party agent gave a reason."
    if timed:
        activity["prov:startTime"] = now_iso()
        activity["prov:endTime"] = now_iso()
    else:
        # Started and never closed: the shape of a crashed step, and the condition that
        # makes a trail unorderable.
        activity["prov:startTime"] = now_iso()

    return {
        "prefix": {
            "yp": "http://hpci-lab.org/yProv4Agents#",
            "prov": "http://www.w3.org/ns/prov#",
        },
        "agent": {
            "yp:agent_rogue": {
                "prov:type": {"$": "yp:OperationalAgent", "type": "prov:QUALIFIED_NAME"},
                "prov:label": "Rogue_Agent",
            }
        },
        "activity": {"yp:DecisionActivity_rogue_00": activity},
        "entity": {},
        "wasAssociatedWith": {
            "_:a0": {
                "prov:activity": "yp:DecisionActivity_rogue_00",
                "prov:agent": "yp:agent_rogue",
            }
        },
    }


# ══════════════════════════════════════════════════════════════════════════════════
#  Running one agent against one fault
# ══════════════════════════════════════════════════════════════════════════════════
def _well_instrumented_record(*, activities: int = 8) -> dict[str, Any]:
    """A producer record with nothing wrong with it.

    Needed because CA3's clean verdict is as unobserved as its anomalous one, and for the
    opposite reason: the bundled yProv4ML records trip two completeness checks, so OK has
    never fired. Every condition CA3 tests for is satisfied here — every activity closed,
    every entity typed, energy and resource streams present, and ordering gaps regular
    enough that no outlier exists.

    Synthetic, and that is the point: this is what a fully instrumented producer looks
    like, and the fact that the real records are not it *is* CA3's finding.
    """
    document: dict[str, Any] = {
        "prefix": {
            "yp": "http://hpci-lab.org/yProv4Agents#",
            "prov": "http://www.w3.org/ns/prov#",
        },
        "agent": {
            "yp:agent_tidy": {
                "prov:type": {"$": "yp:OperationalAgent", "type": "prov:QUALIFIED_NAME"},
                "prov:label": "Well_Instrumented_Producer",
            }
        },
        "activity": {},
        "entity": {},
        "wasAssociatedWith": {},
    }
    # Evenly spaced, so every ordering gap is identical and no gap can be an outlier.
    base = datetime.datetime(2026, 10, 9, 12, 0, 0, tzinfo=datetime.UTC)
    for index in range(activities):
        start = base + datetime.timedelta(seconds=index * 10)
        document["activity"][f"yp:Step_{index:02d}"] = {
            "prov:type": {"$": "yp:ToolCallActivity", "type": "prov:QUALIFIED_NAME"},
            "prov:label": f"training step {index}",
            "prov:startTime": start.isoformat(),
            "prov:endTime": (start + datetime.timedelta(seconds=5)).isoformat(),
        }
        document["wasAssociatedWith"][f"_:a{index}"] = {
            "prov:activity": f"yp:Step_{index:02d}",
            "prov:agent": "yp:agent_tidy",
        }
    # Typed entities, including the energy and resource streams CA3 looks for.
    for name, label, value in (
        ("yp:metric_loss", "CrossEntropyLoss", 0.412),
        ("yp:metric_accuracy", "Accuracy", 0.913),
        ("yp:metric_energy", "energy_consumed_kwh", 0.0241),
        ("yp:metric_emissions", "carbon_emissions_kg", 0.0081),
        ("yp:metric_cpu", "cpu_utilisation_pct", 61.4),
        ("yp:metric_gpu", "gpu_memory_usage_mb", 7814.0),
    ):
        document["entity"][name] = {
            "prov:type": {"$": "yp:Metric", "type": "prov:QUALIFIED_NAME"},
            "prov:label": label,
            "prov:value": value,
        }
    return document


class ScriptedModel:
    """A model client that returns exactly what a scenario needs it to.

    Injects at the model boundary, not at the check: CAx's grounding check, its verdict
    logic and its justification are untouched. What changes is what the model said — which
    is the thing that varies in production anyway, and the reason the check exists.
    """

    def __init__(self, reply: str, finish_reason: str = "stop") -> None:
        self.model = "scripted"
        self.temperature = 0.0
        self.max_tokens = 400
        self._reply = reply
        self._finish_reason = finish_reason

    def complete(self, prompt: Any, user: str) -> Any:
        from runtime.llm import Completion

        return Completion(
            text=self._reply,
            model=self.model,
            temperature=self.temperature,
            max_tokens=self.max_tokens,
            latency_s=0.0,
            prompt=prompt,
            prompt_tokens=len(user) // 4,
            completion_tokens=len(self._reply) // 4,
            finish_reason=self._finish_reason,
        )


def _verdicts(document: dict[str, Any]) -> dict[str, Any]:
    """Pull the verdict, its reason and its justification out of an emitted record."""
    for activity in (document.get("activity") or {}).values():
        if activity.get("yp:verdict"):
            return {
                "verdict": activity["yp:verdict"],
                "escalation_reason": activity.get("yp:escalation_reason"),
                "justification": activity.get("yp:justification"),
            }
    return {"verdict": None, "escalation_reason": None, "justification": None}


def _headless(agent_class: type, out_dir: Path, **attributes: Any) -> Any:
    """Build an agent with no bus, writing to ``out_dir``.

    ``OUT_DIR`` is bound at import time by every module that writes a record, so
    redirecting output means rebinding it in each of them — the same reason the benchmark
    harness does this.
    """
    import sys

    import runtime.util

    setattr(runtime.util, "OUT_DIR", out_dir)  # noqa: B010
    module = sys.modules[agent_class.__module__]
    if hasattr(module, "OUT_DIR"):
        setattr(module, "OUT_DIR", out_dir)  # noqa: B010

    agent = agent_class(None)
    for name, value in attributes.items():
        setattr(agent, name, value)
    return agent


def _ca1_integrity(out_dir: Path) -> dict[str, Any]:
    """CA1 persists a record the store then corrupts: integrity check fails → ESCALATE."""
    from runtime.agents.ca1_storage import CA1Storage

    agent = _headless(CA1Storage, out_dir, store=CorruptingStore(root=str(out_dir / "store")))
    document, log = agent._do_persist(
        _SESSION, _producer_record(), ["ca2", "ca3", "cax"], "quorum", "all consumers acked"
    )
    return {"log": log, **_verdicts(document)}


def _ca1_timeout(out_dir: Path) -> dict[str, Any]:
    """CA1 persists with no consumer acknowledgement: ordering forfeited → ESCALATE."""
    from runtime.agents.ca1_storage import CA1Storage

    agent = _headless(CA1Storage, out_dir, store=LightweightStore(root=str(out_dir / "store")))
    document, log = agent._do_persist(
        _SESSION,
        _producer_record(),
        [],
        "timeout",
        "timeout after 3.0s; no acknowledgement from ['ca2', 'ca3', 'cax']",
    )
    return {"log": log, **_verdicts(document)}


def _mc1_unjustified(out_dir: Path) -> dict[str, Any]:
    """MC1 reviews a third-party record whose decision carries no reason → FAIL."""
    from runtime.agents.mc1_compliance import MC1Compliance

    agent = _headless(MC1Compliance, out_dir)
    document, log = agent.process(
        _SESSION, _rogue_record(justified=False), "agent.lifecycle.rogue.fault"
    )
    return {"log": log, **_verdicts(document)}


def _mc3_untimed(out_dir: Path) -> dict[str, Any]:
    """MC3 audits a record with an unclosed activity: trail not fully timed → PARTIAL."""
    from runtime.agents.mc3_audit import MC3Audit

    agent = _headless(MC3Audit, out_dir)
    document, log = agent.process(
        _SESSION, _rogue_record(timed=False), "agent.lifecycle.rogue.fault"
    )
    return {"log": log, **_verdicts(document)}


def _ca2_accept(out_dir: Path) -> dict[str, Any]:
    """CA2 given a graph that actually carries measurements → plots them → ACCEPT.

    CA2 revises whenever it has to fall back to charting the graph's own composition
    instead of the run's metrics, and the bundled yProv4ML records carry no numeric
    `prov:value` entities — so REVISE is the only verdict the sample data can produce.
    That is a finding about the input, not about CA2, and this scenario separates the two.
    """
    from runtime.agents.ca2_metrics import CA2Metrics

    agent = _headless(CA2Metrics, out_dir, store=LightweightStore(root=str(out_dir / "store")))
    document, log = agent.process(
        _SESSION, _well_instrumented_record(), "producer.yprov4ml.fault"
    )
    return {"log": log, **_verdicts(document)}


def _cax_revise(out_dir: Path) -> dict[str, Any]:
    """CAx given a model that invents a figure → the grounding check catches it → REVISE.

    The faithfulness failure the grounding check exists for: a summary naming a quantity
    that appears nowhere in the graph it summarises. Injected by scripting the model's
    reply, which is the thing that varies in production.
    """
    from runtime.agents.cax_summariser import CAxSummariser

    agent = _headless(
        CAxSummariser,
        out_dir,
        store=LightweightStore(root=str(out_dir / "store")),
        llm=ScriptedModel("This run logged 4096 training steps across 512 epochs."),
    )
    document, log = agent.process(
        _SESSION, _well_instrumented_record(), "producer.yprov4ml.fault"
    )
    return {"log": log, **_verdicts(document)}


def _ca3_ok(out_dir: Path) -> dict[str, Any]:
    """CA3 given a fully instrumented graph with regular timing → no findings → OK.

    The clean verdict, which the real records cannot produce: they declare start times
    without end times and carry no energy or resource streams, so every run of CA3 so far
    has had something true to report. Observing OK is what establishes that ANOMALY is
    informative rather than inevitable.
    """
    from runtime.agents.ca3_analytics import CA3Analytics

    agent = _headless(
        CA3Analytics, out_dir, store=LightweightStore(root=str(out_dir / "store"))
    )
    document, log = agent.process(
        _SESSION, _well_instrumented_record(), "producer.yprov4ml.fault"
    )
    return {"log": log, **_verdicts(document)}


def _ca1_incomplete(out_dir: Path) -> dict[str, Any]:
    """CA1's own copy arrived with a gap: stored, said to be partial → ESCALATE."""
    from runtime.agents.ca1_storage import CA1Storage

    agent = _headless(CA1Storage, out_dir, store=LightweightStore(root=str(out_dir / "store")))
    document, log = agent._do_persist(
        _SESSION,
        _producer_record(),
        ["ca2", "ca3", "cax"],
        "quorum",
        "all consumers acked",
        [3],
        True,
    )
    return {"log": log, **_verdicts(document)}


class _CapturingBus:
    """Records what an agent publishes, so a stream can be replayed with a gap in it."""

    def __init__(self) -> None:
        self.published: list[tuple[str, Any]] = []

    async def publish(self, subject: str, envelope: Any) -> None:
        self.published.append((subject, envelope))


def _stream_gap(out_dir: Path) -> dict[str, Any]:
    """A consumer is handed a stream whose middle chunk was lost in transport → INCOMPLETE.

    The fault is in the delivery: the real sender numbers the stream, one envelope is
    withheld, and the real agent receives the rest. Before this was checked, the agent
    processed the partial document and emitted a well-formed record about it.
    """
    import asyncio

    from runtime.agents.cax_summariser import CAxSummariser
    from runtime.base_agent import stream_document

    sent = _CapturingBus()
    asyncio.run(
        stream_document(
            sent,  # type: ignore[arg-type]
            "producer.yprov4ml.fault",
            session_id=_SESSION,
            producer_id="yprov4ml-replay",
            doc=_producer_record(),
        )
    )
    envelopes = [envelope for _, envelope in sent.published]
    lost = envelopes[2]

    received = _CapturingBus()
    agent = _headless(CAxSummariser, out_dir, bus=received)

    async def deliver() -> None:
        for envelope in envelopes:
            if envelope is not lost:
                await agent._on_envelope("producer.yprov4ml.fault", envelope)

    asyncio.run(deliver())
    document: dict[str, Any] = {}
    for _, envelope in received.published:
        for section, value in (envelope.payload or {}).items():
            if isinstance(value, dict):
                document.setdefault(section, {}).update(value)
    return {
        "log": f"withheld envelope {lost.sequence_no} of {len(envelopes)}",
        **_verdicts(document),
    }


SCENARIOS: tuple[Scenario, ...] = (
    Scenario(
        name="ca1-integrity",
        agent="ca1",
        verdict="ESCALATE",
        condition="integrity",
        how="the store writes bytes it did not hash; the round-trip read catches it",
        run=_ca1_integrity,
    ),
    Scenario(
        name="ca1-timeout",
        agent="ca1",
        verdict="ESCALATE",
        condition="consumption-unconfirmed",
        how="no consumer acknowledged; the record is archived anyway, ordering forfeited",
        run=_ca1_timeout,
    ),
    Scenario(
        name="ca1-incomplete",
        agent="ca1",
        verdict="ESCALATE",
        condition="incomplete-input",
        how="CA1's own copy lost an envelope; what arrived is stored and said to be partial",
        run=_ca1_incomplete,
    ),
    Scenario(
        name="stream-gap",
        agent="cax",
        verdict="INCOMPLETE",
        condition="envelope-lost",
        how="one envelope of the input stream is lost in transport; the agent records the "
        "gap instead of processing a partial document",
        run=_stream_gap,
    ),
    Scenario(
        name="mc1-unjustified",
        agent="mc1",
        verdict="FAIL",
        condition="decision-justified",
        how="a third-party record whose decision carries no justification",
        run=_mc1_unjustified,
    ),
    Scenario(
        name="mc3-untimed",
        agent="mc3",
        verdict="PARTIAL",
        condition="activity-ended",
        how="a third-party record with an activity that was started and never closed",
        run=_mc3_untimed,
    ),
    Scenario(
        name="ca2-accept",
        agent="ca2",
        verdict="ACCEPT",
        condition="numeric-metrics",
        how="a producer record that actually carries measurements, so no fallback is needed",
        run=_ca2_accept,
    ),
    Scenario(
        name="cax-revise",
        agent="cax",
        verdict="REVISE",
        condition="grounding",
        how="a scripted model reply naming figures that appear nowhere in the graph",
        run=_cax_revise,
    ),
    Scenario(
        name="ca3-ok",
        agent="ca3",
        verdict="OK",
        condition="no-invariant-breached",
        how="a fully instrumented graph with regular timing: nothing true left to report",
        run=_ca3_ok,
    ),
)


def run(scenario: Scenario, out_dir: Path, *, baseline: bool = False) -> dict[str, Any]:
    """Run one scenario and report what verdict it provoked.

    With ``baseline``, the callback baseline watches the scenario through the same
    observation hook it uses on the reference run, and its event stream is kept under
    ``baseline/`` (events only: the scenario's PROV records stay the only ``*_prov.json``).
    The escalations are where the two records differ most, so they are where an operator
    study needs both.
    """
    target = out_dir / scenario.name
    # The directory belongs to this scenario alone. Cleared first because records are
    # never overwritten (runtime.util.write_prov), so a second run into the same place
    # would otherwise leave the first run's records beside its own.
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    if baseline:
        from eval.baseline import DEFAULT_TYPE_MAP, EVENTS_FILE, CallbackLogger, write_events
        from yprov4agenticai.observer import observe

        logger = CallbackLogger(type_map=DEFAULT_TYPE_MAP)
        with observe(logger.record):
            observed = scenario.run(target)
        write_events(target / "baseline" / EVENTS_FILE, logger.events())
    else:
        observed = scenario.run(target)
    observed |= {
        "scenario": scenario.name,
        "agent": scenario.agent,
        "expected": scenario.verdict,
        "condition": scenario.condition,
        "how": scenario.how,
        "as_expected": observed["verdict"] == scenario.verdict,
    }
    (target / "_scenario.json").write_text(
        json.dumps(observed, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
    )
    return observed


def _over_inputs(
    selected: tuple[Scenario, ...], inputs: Path, out_dir: Path, per: int, baseline: bool
) -> int:
    """Each input-independent scenario on ``per`` records, a different slice for each.

    Scenario *i* takes records ``i*per`` onwards (wrapping), so across scenarios the faults
    fall on different runs, and an operator cannot learn the cause from the run.
    """
    global _INPUT
    records = sorted(inputs.glob("*.json"))
    missed = []
    chosen = [s for s in selected if s.name in INPUT_INDEPENDENT]
    for index, scenario in enumerate(chosen):
        for k in range(per):
            _INPUT = records[(index * per + k) % len(records)]
            named = Scenario(**{**scenario.__dict__, "name": f"{scenario.name}@{_INPUT.stem}"})
            row = run(named, out_dir, baseline=baseline)
            if not row["as_expected"]:
                missed.append(named.name)
    _INPUT = None
    print(f"{len(chosen) * per} runs over {inputs}; not as expected: {missed or 'none'}")
    return 1 if missed else 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.fault_injection",
        description="Provoke the alternative verdicts, so E5 reports reachability.",
    )
    parser.add_argument("--list", action="store_true", help="list the scenarios and exit")
    parser.add_argument("--scenario", help="run one scenario by name")
    parser.add_argument("--all", action="store_true", help="run every scenario")
    parser.add_argument("--out-dir", default="out/faults", help="where records land")
    parser.add_argument(
        "--baseline", action="store_true", help="also keep the callback baseline's events"
    )
    parser.add_argument(
        "--inputs",
        type=Path,
        help="repeat the input-independent scenarios over every record in this directory",
    )
    parser.add_argument(
        "--per-scenario", type=int, default=8, help="with --inputs: records per scenario"
    )
    args = parser.parse_args(argv)

    by_name = {scenario.name: scenario for scenario in SCENARIOS}

    if args.list:
        for scenario in SCENARIOS:
            print(f"  {scenario.describe()}")
        return 0

    selected: tuple[Scenario, ...]
    if args.scenario:
        if args.scenario not in by_name:
            raise SystemExit(f"unknown scenario {args.scenario!r}; try --list")
        selected = (by_name[args.scenario],)
    elif args.all:
        selected = SCENARIOS
    else:
        raise SystemExit("give --scenario NAME, --all, or --list")

    # Resolved, because an emitted record's store pointer is a file URI and a relative
    # path has none.
    out_dir = Path(args.out_dir).resolve()
    if args.inputs:
        return _over_inputs(selected, args.inputs, out_dir, args.per_scenario, args.baseline)
    results = [run(scenario, out_dir, baseline=args.baseline) for scenario in selected]

    print(f"{'scenario':<20} {'agent':<6} {'expected':<10} {'observed':<10} reason")
    for row in results:
        mark = "" if row["as_expected"] else "   <-- NOT AS EXPECTED"
        print(
            f"{row['scenario']:<20} {row['agent']:<6} {row['expected']:<10} "
            f"{row['verdict']!s:<10} {row['escalation_reason'] or row['condition']}{mark}"
        )
    for row in results:
        print(f"\n[{row['scenario']}] {row['justification']}")

    missed = [row["scenario"] for row in results if not row["as_expected"]]
    if missed:
        print(f"\n! scenarios that did not provoke the expected verdict: {missed}")
    return 1 if missed else 0


if __name__ == "__main__":
    raise SystemExit(main())
