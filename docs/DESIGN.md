# Demonstrator — design and rationale

> **Deliverable:** contract-end demonstrator, 8 November 2026
> **Headline claim:** reflexive instrumentation — agents track their own behaviour as W3C PROV
> **Scope rule:** lead with what runs; everything speculative is labelled roadmap

---

## 1. What Sandro asked for

From the 1:1 of 2026-10-05:

> *"a demonstrator, very simple, with a bus, even a synthetic generator that reads the
> records from a provenance file and sends them to the message queue, and one or two agents
> subscribing and doing something … but that one should be instrumented with the library,
> with something that will keep track of the provenance records. Because that's the other
> point that is missing — **how are we going to instrument those agents?**"*

Plus, from the plenary the same day: generalise the agents into reusable building blocks,
compose use cases on top of them, and fix two architectural errors (below).

This demonstrator delivers all of it, and exceeds the stated minimum: **four operational
agents, two meta agents, a bus, a producer, and the instrumentation** — which is the piece
Sandro named as missing.

---

## 2. The two decisions that shaped it

**Scope — lead with what runs.** Against a hard contract deadline we harden and extend the
pipeline that already executed end-to-end rather than gambling on new architecture. Every
speculative idea is in §6 as roadmap, not in the build.

**Headline — reflexive instrumentation.** This is simultaneously our locked paper novelty
(`yp:justification`, captured not retrieved), the gap Sandro named, and the differentiator
against the observability-based teams he is worried about. Every design choice serves it.

---

## 3. Corrections applied from the 2026-10-05 review

| Sandro's correction | How it is implemented |
|---|---|
| *"the store shouldn't really be on the bus"* — it belongs behind the persist agent | `CA1Storage` writes through `LightweightStore`'s own API from its backend. The bus carries the **storage receipt**, never the payload. |
| Persist must happen **after** a consumer has used the record | CA1 holds each record, counts consumer acknowledgements off `agent.lifecycle.*`, and persists only at quorum — with a timeout guard so a crashed consumer cannot lose a record. The verdict records which path was taken. |
| *"rather than going on the metrics … something on the structure … anomaly detection is just one analytics task"* | `CA3Analytics` is an **analytics agent running five algorithms** (structure, timing, sustainability, resource, anomaly) over any PROV graph. Anomaly detection is one of them, not the agent's identity. |
| Agents should be general building blocks, reusable across use cases | All six agents are domain-agnostic: every computation derives from PROV itself (types, roles, timestamps, relations), never from an ML-specific schema. |

---

## 4. Architecture

```
  data/yprov4ml/*.json ──▶ PRODUCER ──producer.yprov4ml.<session>──▶ NATS BUS
                                                                      │
                     ┌────────────────────────────────────────────────┤
                     ▼                                                ▼
         OPERATIONAL AGENTS                                   CA1 StorageAgent
         CAx · CA2 · CA3                                      holds ─▶ awaits acks
         consume, do work,                                    ─▶ quorum ─▶ persists
         emit their OWN PROV                                         │
                     │                                               ▼ store API
                     │ agent.lifecycle.<role>.<session>        yProvStore / KG
                     ▼                                         (BEHIND CA1, not on bus)
              META AGENTS  MC1 · MC3
              observe, govern, emit governance.<role>.*
              (and are themselves tracked the same way)
```

**Topics.** `producer.yprov4ml.<session>` · `agent.lifecycle.<role>.<session>` ·
`governance.<role>.<session>` · `system.ready.<role>` (readiness handshake — NATS core is
at-most-once, so subscribers must exist before the producer publishes).

**Why meta output uses a different prefix.** `governance.*` is terminal: meta agents
subscribe to `agent.lifecycle.>` but publish elsewhere, so the governance layer can never
re-trigger itself. CA1 additionally ignores its own acknowledgements.

**What that costs the reflexivity claim, stated plainly.** Reflexivity in a run is one
level deep. The operational agents are governed; the governance layer is not. This is a
termination property rather than an oversight — governing a governance record produces
another record to govern — but it does mean "the system tracks its own trackers" needs the
qualifier, and the paper's contribution C2 should carry it. Two things do hold: the meta
agents are themselves fully instrumented, and MC1 satisfies its own standard, which is
checked by running MC1's checks over MC1's own emitted record. Before Phase 1 it did not,
and only self-application found that. A bounded second level, with the depth carried in the
subject so recursion is impossible by construction, is a plausible extension and is not
built (`todo.md` A4).

**One image, many roles.** A single container image; the `ROLE` env var selects the agent.
Rootless-friendly, so the same image converts cleanly to Apptainer for HPC later.

---

## 5. What is built

| | Agent | Status | Notes |
|---|---|:--:|---|
| CA1 | StorageAgent | ✅ **new** | persist-after-consume, sha-256 + `yp:pid`, round-trip integrity |
| CAx | NL_Summarizer | ✅ | LLM with deterministic fallback |
| CA2 | Metrics_Visualizer | ✅ | headless matplotlib |
| CA3 | Analytics_Agent | ✅ **new** | 5 algorithms, generalised per Sandro |
| MC1 | ComplianceChecker | ✅ | typing + justification checks |
| MC3 | AuditabilityAgent | ✅ | who-did-what-when trail |
| — | NATS bus + producer | ✅ | real yProv4ML records replayed |
| — | Instrumentation core | ✅ | `yprov4agenticai` — the headline |
| — | Local no-Docker runner | ✅ **new** | same agents, in-process bus |
| — | Evaluation harness | ✅ **new** | emits the paper's tables from artifacts |
| — | Capture API | ✅ | `step` / `activity` / `decide` / `emit`; all six agents use it |
| — | Strategy A baseline | ✅ | one callback logger, shared by E3 and E6's `A` condition |
| — | `yp:` vocabulary + SHACL | ✅ | `vocab/`; the compliance agent's standard, declared |
| — | Overhead study | ✅ | `eval/bench_*.py`; results in `E6_RESULTS.md` |

### Verified results (two records, six agents, 24 PROV documents)

| Signal | Strategy A (callbacks) | Strategy B (this system) |
|---|--:|--:|
| Documents accepted by the reference `prov` parser | 24 | 24 |
| Typed activities | 96 | 96 |
| Typed entities | 0 | 78 |
| Entities carrying a resolvable pointer | 0 | 8 |
| Relations | 324 | 324 |
| Decisions carrying `yp:verdict` | 24 | 24 |
| Decisions carrying **`yp:justification`** | **0** | **24** |
| Records conforming to `vocab/shapes.ttl` | — | 24 |

**This table used to read 0 / 78 / 44 / 250 down the Strategy A column, and four of those
figures were wrong.** A callback handler sees every node, times it, reconstructs the whole
relation set, and serialises to conformant PROV-JSON. It even recovers the verdicts, by
scraping the labels developers wrote them into. What it does not recover, under any
configuration, is the reasoning — measured by searching every string the handler was handed
for the justifications in the explicit record, and finding none of them. One earned zero is
worth more than four unearned ones. See `paper/OUTLINE.md` E3 and `eval/baseline.py`.

Read the relation total as a split, not a number: 126 observed, 72 derived, 30 declared,
96 agent attributions. The attributions are one per activity, so they count steps rather
than structure (`eval.evaluate` reports E1b for this reason).

CA3 additionally found **real completeness gaps in yProv4ML's own output**: 8 of 9
activities declare `prov:startedAtTime` with no end time, and entity typing coverage is
82.1%. A useful, honest result — the analytics agent earns its place on the first run.

---

## 5b. Limitations a reader should be told

Each of these is a real property of what is built. Stating them costs nothing; having one
found by a reviewer costs a great deal.

**The bus can drop records.** NATS core is at-most-once and JetStream is not configured, so
a subscriber that is not running when a record is published never sees it. The readiness
handshake (`system.ready.<role>`) mitigates start-up ordering and does nothing about a
mid-run disconnection. A provenance bus that can silently lose provenance deserves to say
so in a provenance paper.

**The local and containerised paths are not equivalent evidence.** `run_local.py`'s bus
awaits each handler inline in subscription order, so there is no concurrency in it: a
consumer's acknowledgement is delivered from inside the producer's own `publish` call. The
in-process path exercises none of the NATS path's races. Both emit the same provenance —
the pipeline gate checks that on every commit — but a claim about ordering or throughput
needs the NATS path, and the overhead study's Level 3 is labelled sequential for this
reason.

**CA1 accumulates state without bound.** `_acks` and `_done` are pruned per session, but
`_done` grows for the life of the process: one session identifier per record ever handled.
At two records this is nothing; a long-lived deployment needs an eviction policy. The
timeout guards *are* cancelled on early quorum, which they were not before Phase 1 — before
that fix the guard raced the consumers it was meant to back up.

**A tracker holds its whole record in memory until it emits.** Measured in the scaling
sweep: roughly 1.3 KiB per activity, linear, so a 10,000-step record costs about 13 MiB of
live memory before a byte is written. For an agent with a bounded turn this is irrelevant;
for a long-running one it is a real constraint and the reason `granularity="METADATA"`
exists.

**Per-step energy figures are not attributable.** When a sampling backend's period exceeds
a step's duration — codecarbon's default is 15 seconds against millisecond steps — a delta
lands on whichever step read after a tick. The record discloses this per figure
(`yp:energy_attributable`), and energy is off by default. Energy belongs at session
granularity.

**Library surface exceeds demonstrated surface.** `register_memory` and the
`prov:actedOnBehalfOf` delegation are offered by the library and not exercised by the
demonstrator; the vocabulary defines them and `tests/test_shapes.py` enumerates them as
known exceptions so the gap cannot drift into being undocumented.

---

## 6. Explicitly out of scope (roadmap, not 8 November)

Keeping these out is what protects the deadline; each is a genuine next increment.

- **Meta → operational actuation.** Today the meta layer observes, judges, and gates. Making
  it *reconfigure* or *activate* an operational agent needs a control-channel design — this
  is Sandro's #1 open question and the natural Paper-2 contribution.
- **Per-experiment agent selection.** Which agents apply to which experiment: backend config
  or a class tag on the incoming records? Open. Today: all agents see all records.
- **OTel Layer-1 bridge** (see `INSTRUMENTATION.md`) — the scouting target.
- **In-flight continuous-query analytics** — CA3 is post-mortem over a completed document.
- **Kubernetes** — compose today; Sana's track.
- **Topic + `yp:` namespace convergence** with Sana's branch.

---

## 7. Risks

| Risk | Mitigation |
|---|---|
| Other teams publish first | Lead with capture-not-retrieve + reflexivity — hard to replicate without typed explicit instrumentation. |
| Demo fragility in front of an audience | Two independent run paths (Docker and in-process); no API key required; every LLM call has a deterministic fallback, exercised in testing. |
| Reviewer asks "is this just LangGraph?" | The demonstrator uses **no agent framework at all** — same instrumentation, bare daemons. |
| Scope creep from new use cases | §6 is the contract: new agents go to roadmap until the paper is drafted. |
