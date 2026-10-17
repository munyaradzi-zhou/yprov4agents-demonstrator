# yProv4Agents — Provenance-as-a-Service Demonstrator

**A containerised multi-agent provenance system in which every agent tracks its own
behaviour as W3C PROV — including *why* it decided what it decided, in a form that replays.**

Producers stream real provenance records onto a message bus; operational agents consume
them and do real work; meta agents govern those agents by reading the provenance the
agents emit about *themselves*. The system tracks its own trackers — one level deep, for a
reason given below.

> MC Zhou · HPCI Lab, University of Trento · supervisor Prof. Sandro Fiore
> Part of the yProv ecosystem (`yProv4ML`, `yProv4WFs`, `yProv4Agents`).

---

## Run it

**No Docker** — everything in one process:

```bash
uv sync --frozen --extra eval --extra dev
uv run python run_local.py --persist-timeout 2 --baseline
uv run python -m eval.evaluate --out-dir out --markdown out/RESULTS.md
uv run python -m eval.replay --prov-dir out          # every decision re-derived from its record
```

A run takes a few seconds plus CA1's persist window (`--persist-timeout`, 30 s by default),
which the runner waits out so that no record is still being held when it stops.
`--baseline` also records the callback baseline (Strategy A) the paper compares against.

**The real topology** — seven containers around a NATS broker:

```bash
cp .env.example .env            # optional: OPENAI_API_KEY enables LLM mode
docker compose -f deploy/docker-compose.yml up --build
uv run python -m eval.evaluate --out-dir out
```

Both paths run the *same agents with the same instrumentation* and emit the same PROV —
only the transport differs. CI runs both on every commit and holds each to the same
lineage comparison against `experiments/decision-replay` and the same SHACL shapes; set
`OUT_HOST_DIR=../out-nats` to keep a container run's records apart from a local one's. The
broker is published on `127.0.0.1` only: it has no authentication.

No API key is required: CAx falls back to a deterministic summary and CA3 reports its
computed findings without LLM narration. With a key, both call the model, and the record
carries the model requested, the snapshot that answered, the provider's fingerprint, the
prompt's id, version and hash, and the token counts. `run_local.py` reads `.env` itself, so
to force the keyless path set the key to empty (`OPENAI_API_KEY= uv run python run_local.py`);
unsetting it is not enough.

**Who decides CAx's verdict is a switch.** By default code does: the summary's figures are
checked against the graph. With `CAX_VERDICT=model` and a key, gpt-4o reviews the summary
against the producer's record and its answer decides; the code check still runs and is
recorded beside it as `yp:code_verdict`.

Artifacts land in `./out`: one PROV-JSON per agent per session, the governance records
written *about* those agents, rendered charts, the content-addressed store, and
`RESULTS.md`.

---

## What happens when you run it

```
                                  ┌─────────────────────────────────────────┐
  data/yprov4ml/*.json            │        OPERATIONAL AGENTS               │
  (real yProv4ML records)         │                                         │
          │                       │  CAx  NL_Summarizer      (LLM)          │
          ▼                       │  CA2  Metrics_Visualizer (deterministic)│
   ┌─────────────┐  producer.*    │  CA3  Analytics_Agent    (heuristic+LLM)│
   │  PRODUCER   │───────────────▶│                                         │
   │  (replay)   │                │  CA1  StorageAgent ── persists only      │
   └─────────────┘                │       AFTER consumers have used it      │
          │                       └──────────────────┬──────────────────────┘
          │                                          │ each agent emits its OWN
          ▼                                          │ lifecycle as typed PROV
   ┌──────────────────────────────────┐              │ agent.lifecycle.<role>.*
   │       NATS PROVENANCE BUS        │◀─────────────┘
   └──────────────────────────────────┘
          │                                          ┌──────────────────────┐
          │ agent.lifecycle.>                        │    META AGENTS       │
          └─────────────────────────────────────────▶│  MC1 Compliance      │
                                                     │  MC3 Auditability    │
                               governance.<role>.* ◀─┤  (themselves tracked)│
                                                     └──────────────────────┘
   CA1's store sits BEHIND CA1 (store API), not on the bus —
   the bus carries the storage *receipt*, never the payload.
```

**The ordering that matters.** CA1 holds each record and persists only once the
consumers have acknowledged it:

```
[ca1] holding 'example_0' — awaiting consumers ['ca2', 'ca3', 'cax']
[ca1] ack from 'cax' for 'example_0' (1/3), waiting on ['ca2', 'ca3']
[ca1] ack from 'ca2' for 'example_0' (2/3), waiting on ['ca3']
[ca1] ack from 'ca3' for 'example_0' (3/3), quorum reached
[ca1] persisted 'example_0' -> 21.T11999/yprovstore-abfe4946dd38 [ACCEPT] via quorum, acked by ['ca2', 'ca3', 'cax']
```

A timeout guard persists anyway if a consumer dies, and the decision records which case
applied — so a crashed agent can never silently lose a record.

---

## The agents

Every operational agent traverses the same canonical **6-step lifecycle** —
S1 Receive · S2 Plan · S3 Act · S4 Observe · **S5 Decide ★** · S6 Respond — grouped into
three phases: *Ingestion & Scoping* {S1,S2} · *Execution* {S3} · *Governance &
Reflexion* {S4,S5,S6}. ★ is the step that carries `yp:justification`, and the named rule
and inputs that make it replayable.

| | Agent | LLM | Rule | S5 verdicts | What it does |
|---|---|:--:|---|---|---|
| **CA1** | `StorageAgent` | — | `ca1.persist/v1` | ACCEPT / ESCALATE ×3 | persists after consumption; sha-256 + `yp:pid`; round-trip integrity check |
| **CAx** | `NL_Summarizer` | optional | `cax.summary/v1`, or `cax.review/v1` when the model decides | ACCEPT / REVISE | natural-language summary of a provenance document |
| **CA2** | `Metrics_Visualizer` | — | `ca2.chart/v1` | ACCEPT / REVISE | renders charts from the graph |
| **CA3** | `Analytics_Agent` | optional | `ca3.timing-outlier/v1` | OK / ANOMALY | **5 mining algorithms** over any PROV graph |
| **MC1** | `ComplianceChecker` | — | `mc1.compliance/v1` | PASS / FAIL | checks a record against `vocab/shapes.ttl` |
| **MC3** | `AuditabilityAgent` | — | `mc3.trail/v1` | COMPLETE / PARTIAL | reconstructs the who-did-what-when trail |

The LLMs write text (CAx's summary, CA3's narration); code decides every verdict unless
`CAX_VERDICT=model` hands CAx's to the model.

**Every verdict in that table has been observed in an archived run** — twelve of twelve,
six from the ordinary pipeline and six from nine injected scenarios
(`experiments/decision-replay/faults`), which also observe CA1's ESCALATE by each of its
three reasons and `INCOMPLETE`. A verdict that has never fired is a claim rather than a
result, which is why the distinction is tracked.

CA1's `ESCALATE` arrives down three independent paths, and `yp:escalation_reason`
separates them: `integrity` means the stored bytes are wrong, `incomplete-input` means part
of the record was lost in transport and what was stored is partial, and
`consumption-unconfirmed` means the data is fine and a consumer is down. They demand
different responses, which is why the record has to say which.

**An agent can also record `INCOMPLETE`.** The bus is at-most-once, so an envelope can
be lost. Each envelope is numbered, and an agent whose input arrives with a gap records the
missing sequence numbers as its decision instead of processing the part that did arrive.
CA1 does not stop at the gap: it stores what arrived, because losing the record is worse,
and escalates with `incomplete-input`. Reproduce any of it with
`python -m eval.fault_injection --all`; `--inputs corpus/yprov4ml` repeats the
environment faults over the corpus.

**CA3 is an analytics agent, not an anomaly detector.** It applies a *set* of algorithms
to any W3C PROV document — structure, timing, sustainability, resource, anomaly — with
anomaly detection as one algorithm among them. Everything is derived from PROV itself
(types, roles, timestamps, relations), so it works unchanged on yProv4ML, yProv4WFs, or
an agent's own lifecycle record.

---

## What it proves

**The thesis, stated precisely.** A decision step computes values `z = g(x)` from its
inputs and returns a verdict `v = r(x, z)`. An observer at the step's boundary (a callback,
a span, a query afterwards) sees `x` where it is passed and `v`, and from that record `r` is
not identifiable and `z` is recoverable only by re-running `g`. This is *capture, not
retrieve*.

**Every decision replays.** Each verdict is the output of a named, versioned rule, and the
record carries the rule (`yp:rule`) and the values it read (`yp:rule_inputs`). `decide()`
refuses a verdict its rule does not give; `eval.replay` re-derives every verdict from the
record, and an edited verdict or input does not replay. 33 of 33 reference and fault
decisions, 288 of 288 on the corpus, and 24 of 24 over Docker replay.

The comparison is against a callback-level baseline built from the **same run**,
deliberately generous: it is handed every structural fact the explicit instrumentation
declared.

| Signal | Strategy A (callbacks, with a type map) | Strategy B (this demonstrator) |
|---|--:|--:|
| Activities, typed | 96 | 96 |
| Lineage relations | 324 | 324 |
| Decisions carrying a verdict | 24 (scraped from node labels) | 24 |
| Decisions carrying a justification | **0** | **24** |
| Values the decision rules read, seen | **2 of 36**, both input fields | 36 of 36, replayable |
| Same, over 24 real training runs | **24 of 432**, all input fields | 432 of 432 |

**Five ways of trying to retrieve the reason, and what each got:**

| Observer | Result | Archive |
|---|---|---|
| The callback baseline | 2 of 36 values the rules read; 24 of 432 on the corpus | `decision-replay`, `corpus-24` |
| A line tracer inside the step (`sys.settrace`) | 7 of 12 values, among 15–116 per step, at 2–13× the step time; values computed inline are never seen | `line-tracer` |
| LangChain's own callback handler on a LangGraph port | 110 of 240 matched, 106 of them also in an unrelated run (chance) | `framework-baseline` |
| gpt-4o, from what the baseline saw | the reason rebuilt for 1 of 143 decisions; CA1's cause at chance (9 of 27) | `model-studies` |
| Operators (gpt-4o and gpt-4o-mini) choosing the cause | 53% from the baseline's record, **100%** from ours | `model-studies` |

**When the model decides, the boundary moves.** With CAx's verdict chosen by gpt-4o, the
model caught every planted fabrication (72 of 72) where the code check caught only invented
large figures (24 of 72); recording both is what makes the 48 disagreements visible. And
because the model's answer is a step output, a callback sees it: the reason was rebuilt from
the trace for all 72 REVISE verdicts. A reason that crosses the step's boundary is
retrievable whoever wrote it; capture matters most for reasons computed in code
(`experiments/model-verdict`, `experiments/model-verdict-studies`).

**Reflexivity is visible in the filenames.** `example_0__cax_prov.json` is CAx's own
lifecycle; `example_0__cax__mc1_prov.json` is MC1's provenance *about* CAx;
`example_0__cax__mc3_prov.json` is MC3's audit of the same. Governance is where capture
pays first: MC1 run over the baseline's 24 records fails all 24.

**And it stops there, deliberately.** The meta agents publish to `governance.*`, which
nothing subscribes to, so reflexivity in a run is **one level deep**. An agent that governs
its own governance output produces another record to govern, and the recursion does not
terminate on its own. MC1 does satisfy its own standard, which
`tests/test_reflexive_governance.py` checks.

---

## What it costs

Measured on a 4-core laptop; the ratios and byte counts transfer, absolute times do not.
Each cost is a bound, not a failure to resolve a difference (`eval.equivalence`: paired
bootstrap intervals, two one-sided tests against a 1 ms margin).

- **Building the record is equivalent to no provenance** for five of six agents, at 48–81 µs
  (100 paired rounds, `experiments/overhead-samples`). CA2's 95 ms chart render jitters by
  more than the margin, so for it equivalence can be shown neither way.
- **Writing it is the cost**: built and written, a record adds 0.75–1.3 ms. Beside a model
  call of 1.7–4.3 s that is under 0.1%. `PROV_PERSISTENCE=TRANSIENT` removes 89% of the time
  cost, `PROV_GRANULARITY=METADATA` 38% of the bytes, keeping every justification.
- **Energy: the write costs +16% CPU per session** (95% interval +9 to +22%), building alone
  +3% (−5 to +9%), paired by round over 12 rounds (`experiments/energy-session`; CPU from the
  Windows EMI counter, RAM modelled and reported separately).
- **Bytes**: justifications are 6.4% of the records, a decision's rule and inputs 1.7%.
- **It scales linearly**: exponents 1.09 (build) and 1.13 (serialise) up to 10,000 activities.

Reproduce: `uv run python -m eval.bench_overhead --level agent --repetitions 100`, then
`uv run python -m eval.equivalence --agent out/bench/agent.json --energy experiments/energy-session/energy.json`.

## The vocabulary

`vocab/yprov4agents.ttl` publishes the `yp:` terms as an OWL ontology over PROV-O — 19
classes and 71 properties, every one of them emitted by the system or listed with a reason —
and `vocab/shapes.ttl` declares the standard as 18 named SHACL constraints: every decision is
justified and concluded, names a versioned rule if it names one and records that rule's
inputs with it; every step is typed, dated and attributed; every agent decided something;
every pointer carries a SHA-256. Any SHACL engine can apply it:

```bash
uv run python -m eval.validate_shapes --prov-dir out      # 24/24 conform
```

## Evidence and reproducibility

Every number above and in the paper is computed from a frozen archive under `experiments/`,
each with a `MANIFEST.json` naming the commit it was frozen from (clean trees only) and the
SHA-256 of every file. CI re-verifies all of them on every commit, re-runs both pipelines,
replays every decision, and fails if any lineage edge changes without being declared.

| Archive | What it holds |
|---|---|
| `experiments/decision-replay` | the reference run with replayable decisions, the baseline, all nine fault scenarios; CI's provenance gate |
| `experiments/corpus-24` | the demonstrator over 24 real yProv4ML training runs (288 decisions) |
| `experiments/line-tracer` | the in-step observer: what a `sys.settrace` tracer holds, and its cost |
| `experiments/framework-baseline` | the agents as LangGraph nodes, observed by LangChain's callback handler |
| `experiments/overhead-samples` | 100 paired rounds per agent, and the equivalence bounds for time and energy |
| `experiments/model-studies` | gpt-4o reconstruction and the operator panel, every call cached |
| `experiments/model-verdict` · `model-verdict-studies` | CAx with the model deciding, planted fabrications, and the two studies on it |
| `experiments/audit-reference` | the audit-era reference run and its two overhead occasions |
| `experiments/audit-llm` | the model-backed run (gpt-4o → `gpt-4o-2024-08-06`, 4 calls) |
| `experiments/energy-session` | the session-level energy study, with its excluded pilot |

The model studies re-score from their cache with no key: extract `cache.tar.gz` into the
output directory and pass `--offline`.

```bash
uv run python -m eval.archive verify --name decision-replay
uv run python -m eval.compare --before experiments/decision-replay --after out --strict
uv run python -m eval.fault_injection --all          # all 13 verdicts
uv run pytest                                        # 301 tests
```

---

## Layout

```
run_local.py          one-process runner (no Docker, no broker)
runtime/              the agent runtime
  envelope.py         the on-the-wire record format
  net_bus.py          NetworkBus — pub/sub over NATS
  base_agent.py       subscribe → buffer a stream → process → re-emit
  producer.py         replays real yProv4ML records onto the bus
  agent_runner.py     one entrypoint; ROLE env var selects the agent
  llm.py              the model client and the provenance of each call
  agents/             the six agents, each with its decision rule
yprov4agenticai/      the instrumentation library (ProvTracker, capture API, decision rules
                      and replay, store, energy, prompt registry, observer hook)
vocab/                the yp: ontology and its SHACL shapes
data/yprov4ml/        the two yProv4ML example records the reference run replays
corpus/               24 real yProv4ML records from real training runs, and the script
                      that produced them
deploy/               Dockerfile + docker-compose.yml
eval/                 evaluation: evaluate, baseline, replay, compare, validate_shapes,
                      fault_injection, line_tracer, framework_baseline, equivalence,
                      reconstruct, diagnose, review_faults, model_verdict, bench_*, archive
experiments/          frozen, hash-manifested evidence archives
tests/                the test suite, including the PROV conformance suite
docs/                 system layout, design, instrumentation, audit, results, phase notes
paper/                DRAFT.md, figures, and the IEEEtran build (tex/)
```

Optional extras: `eval` (evaluation), `dev` (tests and linters), `energy` (CodeCarbon),
`frameworks` (LangGraph, for `eval.framework_baseline`).

## Documentation

| Document | What it covers |
|---|---|
| `docs/SYSTEM_LAYOUT.md` | **the whole system, phase by phase**: data flow, information model, the `yp:` ontology, UML |
| `docs/DESIGN.md` | why the demonstrator is shaped this way; scope; build plan; risks |
| `docs/INSTRUMENTATION.md` | **how to instrument any agent** — library vs API vs OpenTelemetry |
| `docs/AUDIT.md` | the project audit: every red flag found, its evidence, and what changed |
| `paperfeedback.md` | the reviewer-style critique of the paper, and what was done about each point |
| `docs/E6_RESULTS.md` | the overhead study's results, generated from the archive |
| `paper/DRAFT.md` · `paper/tex/` | the paper, and its IEEEtran version (`python -m paper.build_tex`) |
| `paper/OUTLINE.md` | the paper's plan, cut menu and evidence map |
| `todo.md` | the review findings register — every defect found, its evidence, its status |
| `PLAN.md` | the phases to the paper deadline, and what each one unblocks |
| `docs/phases/` | plain-language walkthrough of each completed phase, with diagrams |
