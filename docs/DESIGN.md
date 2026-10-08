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

### Verified results (two records, six agents, 24 PROV documents)

| Signal | Strategy A | Strategy B |
|---|--:|--:|
| Typed activities | 0 | 78 |
| Typed entities | 0 | 44 |
| Lineage relations | 0 | 250 |
| Decisions with `yp:justification` | 0 (impossible) | 24 (100%) |

CA3 additionally found **real completeness gaps in yProv4ML's own output**: 8 of 9
activities declare `prov:startedAtTime` with no end time, and entity typing coverage is
82.1%. A useful, honest result — the analytics agent earns its place on the first run.

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
