# yProv4Agents — Provenance-as-a-Service Demonstrator

**A containerised multi-agent provenance system in which every agent tracks its own
behaviour as W3C PROV — including *why* it decided what it decided.**

Producers stream real provenance records onto a message bus; operational agents consume
them and do real work; meta agents govern those agents by reading the provenance the
agents emit about *themselves*. The system tracks its own trackers — one level deep, for a
reason given below.

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
only the transport differs. CI runs both on every commit and holds each to the same
lineage comparison against `experiments/audit-reference` and the same SHACL shapes; set
`OUT_HOST_DIR=../out-nats` to keep a container run's records apart from a local one's. The
broker is published on `127.0.0.1` only: it has no authentication. No API key is required: CAx falls back to a deterministic
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
| **CA1** | `StorageAgent` | — | ACCEPT / ESCALATE ×3 | persists after consumption; sha-256 + `yp:pid`; round-trip integrity check |
| **CAx** | `NL_Summarizer` | ✅ | ACCEPT / REVISE | natural-language summary of a provenance document |
| **CA2** | `Metrics_Visualizer` | — | ACCEPT / REVISE | renders charts from the graph |
| **CA3** | `Analytics_Agent` | optional | OK / ANOMALY | **5 mining algorithms** over any PROV graph |
| **MC1** | `ComplianceChecker` | — | PASS / FAIL | checks a record against `vocab/shapes.ttl` |
| **MC3** | `AuditabilityAgent` | — | COMPLETE / PARTIAL | reconstructs the who-did-what-when trail |

**Every verdict in that table has been observed in an archived run** — twelve of twelve,
six from the ordinary pipeline and six from nine injected scenarios under
`experiments/audit-reference/faults`, which also observe CA1's ESCALATE by each of its three
reasons and `INCOMPLETE`. A verdict that has never fired is a claim rather than a
result, which is why the distinction is tracked: this table used to credit CAx with an
`ESCALATE` it cannot emit and MC3 with no verdict at all when it emits two (review finding
A3).

CA1's `ESCALATE` arrives down three independent paths, and `yp:escalation_reason`
separates them: `integrity` means the stored bytes are wrong, `incomplete-input` means part
of the record was lost in transport and what was stored is partial, and
`consumption-unconfirmed` means the data is fine and a consumer is down. They demand
different responses, which is why the record has to say which.

**Every agent can also record `INCOMPLETE`.** The bus is at-most-once, so an envelope can
be lost. Each envelope is numbered, and an agent whose input arrives with a gap records the
missing sequence numbers as its decision instead of processing the part that did arrive —
which would produce a well-formed record about an input it never fully saw. The check lives
in the base class all six agents share, so one fault scenario (`stream-gap`) exercises it
for all of them. That scenario and CA1's `incomplete-input` path are observed in
`experiments/audit-reference` and re-checked by the fault suite in CI. Reproduce any of it with
`python -m eval.fault_injection --all`.

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

**And it stops there, deliberately.** The meta agents publish to `governance.*`, which
nothing subscribes to, so reflexivity in a run is **one level deep**: the operational
agents are governed and the governance layer is not. That bound is a design decision rather
than an omission — an agent that governs its own governance output produces another record
to govern, and the recursion does not terminate on its own.

What *is* established is that the meta layer could be governed: MC1 satisfies its own
standard, which `tests/test_reflexive_governance.py` checks by running MC1's checks over
MC1's own emitted record. Before Phase 1 it did not — the compliance agent demanded a
justified decision and emitted none, which is the kind of thing only self-application
finds. A bounded second level (depth 2, with the depth carried in the subject) is a
plausible extension and is not built; see `todo.md` A4.

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
eval/baseline.py      the Strategy A callback baseline the comparison is measured against
docs/                 design, architecture, and the instrumentation position
paper/                paper outline and results
```

## Documentation

| Document | What it covers |
|---|---|
| `docs/DESIGN.md` | why the demonstrator is shaped this way; scope; build plan; risks |
| `docs/INSTRUMENTATION.md` | **how to instrument any agent** — library vs API vs OpenTelemetry |
| `paper/OUTLINE.md` | the paper this demonstrator is the evaluation for |
| `todo.md` | the review findings register — every defect found, its evidence, its status |
| `PLAN.md` | the six phases to the paper deadline, and what each one unblocks |
| `docs/phases/` | plain-language walkthrough of each completed phase, with diagrams |
