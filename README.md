# yProv4Agents — Provenance-as-a-Service Demonstrator

**A containerised multi-agent provenance system in which every agent tracks its own
behaviour as W3C PROV — including *why* it decided what it decided.**

Producers stream real provenance records onto a message bus; operational agents consume
them and do real work; meta agents govern those agents by reading the provenance the
agents emit about *themselves*. The system tracks its own trackers.

> MC Zhou · HPCI Lab, University of Trento · supervisor Prof. Sandro Fiore
> Part of the yProv ecosystem (`yProv4ML`, `yProv4WFs`, `yProv4Agents`).

---

## Run it

**No Docker, five seconds** — everything in one process:

```bash
python run_local.py
python -m eval.evaluate --out-dir out --markdown out/RESULTS.md
```

**The real topology** — seven containers around a NATS broker:

```bash
cp .env.example .env            # optional: OPENAI_API_KEY enables LLM mode
docker compose -f deploy/docker-compose.yml up --build
python -m eval.evaluate --out-dir out
```

Both paths run the *same agents with the same instrumentation* and emit the same PROV —
only the transport differs. No API key is required: CAx falls back to a deterministic
summary and CA3 reports its computed findings without LLM narration.

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
[ca1] persisted 'example_0' -> 21.T11999/yprovstore-abfe4946dd38 [ACCEPT]
```

A timeout guard persists anyway if a consumer dies, and the decision records which case
applied — so a crashed agent can never silently lose a record.

---

## The agents

Every operational agent traverses the same canonical **6-step lifecycle** —
S1 Receive · S2 Plan · S3 Act · S4 Observe · **S5 Decide ★** · S6 Respond — grouped into
three phases: *Ingestion & Scoping* {S1,S2} · *Execution* {S3} · *Governance &
Reflexion* {S4,S5,S6}. ★ is the step that carries `yp:justification`.

| | Agent | LLM | S5 verdicts | What it does |
|---|---|:--:|---|---|
| **CA1** | `StorageAgent` | — | ACCEPT / ESCALATE | persists after consumption; sha-256 + `yp:pid`; round-trip integrity check |
| **CAx** | `NL_Summarizer` | ✅ | ACCEPT / REVISE / ESCALATE | natural-language summary of a provenance document |
| **CA2** | `Metrics_Visualizer` | — | ACCEPT / REVISE | renders charts from the graph |
| **CA3** | `Analytics_Agent` | optional | OK / ANOMALY | **5 mining algorithms** over any PROV graph |
| **MC1** | `ComplianceChecker` | — | PASS / FAIL | is the emitted PROV typed, and is every decision justified? |
| **MC3** | `AuditabilityAgent` | — | — | reconstructs the who-did-what-when trail |

**CA3 is an analytics agent, not an anomaly detector.** It applies a *set* of algorithms
to any W3C PROV document — structure, timing, sustainability, resource, anomaly — with
anomaly detection as one algorithm among them. Everything is derived from PROV itself
(types, roles, timestamps, relations), so it works unchanged on yProv4ML, yProv4WFs, or
an agent's own lifecycle record.

---

## What it proves

Running the two bundled records through six agents produces **24 PROV documents**:

| Signal | Strategy A (callbacks) | Strategy B (this demonstrator) |
|---|--:|--:|
| Typed activities | 0 | **78** |
| Typed entities | 0 | **44** |
| Lineage relations | 0 | **250** |
| Decisions with `yp:justification` | **0 — impossible** | **24 (100%)** |
| W3C PROV-JSON output | no | **yes** |

**Why Strategy A can never reach the rationale.** A framework callback fires *after* a
node returns; an observability span wraps the call from outside; a post-hoc query reads
the store later. The reasoning behind a decision is a **local variable at the moment of
decision** — none of them can retrieve what was never captured. This is *capture, not
retrieve*, and it is why behavioural provenance has an irreducible explicit component.

Here is what that buys, from a real run (`out/example_0__ca1_prov.json`):

> *"Persisted after consumption (all expected consumers acknowledged); acknowledged by
> [ca2, ca3, cax]. Record carries 9 activities / 39 entities / 55 relations, 21634 bytes.
> Integrity: round-trip read verified; sha-256 matched. Verdict ACCEPT."*

No callback could have written that sentence.

**Reflexivity is visible in the filenames.** `example_0__cax_prov.json` is CAx's own
lifecycle; `example_0__cax__mc1_prov.json` is MC1's provenance *about* CAx;
`example_0__cax__mc3_prov.json` is MC3's audit of the same. The trackers are tracked.

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
  agents/             the six agents
yprov4agenticai/      the instrumentation library (ProvTracker, store, energy)
data/yprov4ml/        real yProv4ML provenance records to replay
deploy/               Dockerfile + docker-compose.yml
eval/evaluate.py      turns a run into the paper's result tables
docs/                 design, architecture, and the instrumentation position
paper/                paper outline and results
```

## Documentation

| Document | What it covers |
|---|---|
| `docs/DESIGN.md` | why the demonstrator is shaped this way; scope; build plan; risks |
| `docs/INSTRUMENTATION.md` | **how to instrument any agent** — library vs API vs OpenTelemetry |
| `paper/OUTLINE.md` | the paper this demonstrator is the evaluation for |
