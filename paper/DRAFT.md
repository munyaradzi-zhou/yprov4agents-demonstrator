# Capture, Not Retrieve: Behavioural Provenance for Agentic AI

**Full draft, §1–§9.** Roughly 7,900 words against an IEEE 6-page budget of about 4,000
once the four figures and the tables are placed, so this is a long draft to cut from rather
than a final text; `paper/OUTLINE.md` records what the cut should drop first.

> Every figure quoted here is read from a frozen archive under `experiments/`, named at
> the point of use. Where a number could not be measured, the text says so rather than
> estimating.

**Figures** (rendered by `python -m paper.figures` into `paper/figures/`):
Figure 1 = `f1_two_layers` · Figure 2 = `f4_two_escalations` ·
Figure 3 = `f5_a_versus_b` · Figure 4 = `f9_where_the_overhead_is`.

---

## 1. Introduction

Scientific computing infrastructure is acquiring autonomous agents. They summarise runs,
select hyperparameters, decide what to archive, and increasingly act on those decisions
without a human in the loop. The provenance community has a mature answer to *what* such a
system did: W3C PROV describes activities, entities and the lineage between them, and
tooling to capture that lineage automatically is now routine.

It has almost no answer to *why*.

This distinction is not academic. When a storage agent declines to archive a record, when a
summarisation agent rejects its own output and regenerates, when a compliance agent fails a
colleague's record — an operator, an auditor, and increasingly a regulator all need to know
the reason, not merely the outcome. The EU AI Act's transparency obligations are written in
the language of explanation rather than of logging. A record that says a decision was taken,
and when, and by which component, and still cannot say why, does not meet that bar.

Our claim is that this gap is **structural rather than a matter of insufficient tooling**:

> The rationale for a decision is a local variable at the moment of decision. A callback
> fires after the function returns; a span wraps the call; a query reads the store
> afterwards. None of them can retrieve what was never captured.

We call this **capture, not retrieve**. It has an uncomfortable corollary for the
observability-first approach that dominates agent tooling today: no amount of additional
collection closes the gap, because the information is not in the span. It follows that
behavioural provenance has an irreducible explicit component — a small amount of
instrumentation that a developer must write, at the point where a decision is made, and
that no framework can supply on their behalf.

The contribution of this paper is to make that argument precisely, build the smallest
instrumentation layer that discharges it, and measure both what it captures and what it
costs.

**C1 — Capture, not retrieve.** A structural argument that decision rationale is
unreachable by external observation, supported by an A-versus-B comparison over the *same
agents in the same run*. We build the callback-level baseline ourselves and make it as
strong as we can: it reconstructs 96 of 96 activities, all 324 relations, and 24 of 24
verdicts, and its output is accepted by the reference W3C PROV parser. It recovers **0 of
24 justifications** — a figure we obtain by searching every string the baseline observed,
not by assertion (§7, E3).

**C2 — A reflexive multi-agent provenance architecture.** Four operational agents emit
typed PROV about themselves; two meta agents consume *that* provenance and emit PROV in
turn. The system is running, containerised, and governs its own governance layer to a
depth we state exactly (§6, §8).

**C3 — The `yp:` vocabulary and a six-step agent lifecycle**, published as an OWL ontology
with SHACL shapes, instantiated by four heterogeneous agents — deterministic, LLM-backed,
and hybrid — and governed uniformly against the declared shapes (§4).

We also report what the instrumentation costs, because an argument that explicit
instrumentation is *necessary* is worth little if it is also expensive. It is not: the
field we argue for costs nothing our instruments can resolve in time and 278 bytes in
storage, and for every agent in the system that performs substantial work, the entire
instrumentation overhead is smaller than that agent's own run-to-run variance (§7, E6).

---

## 2. Background

### 2.1 W3C PROV

PROV models provenance as three classes — `prov:Entity`, `prov:Activity`, `prov:Agent` —
and a small set of relations between them: `used`, `wasGeneratedBy`, `wasDerivedFrom`,
`wasInformedBy`, `wasAssociatedWith`, `actedOnBehalfOf`. The model is deliberately
domain-neutral, with several serialisations (PROV-JSON, PROV-O in RDF, PROV-N) and an
ecosystem of validators.

That neutrality is why PROV says nothing about rationale. It is not an oversight: PROV
describes *what was derived from what, by whom*, and a justification is none of those
things. It is a property of an activity that no relation can express, which is precisely
why an extension vocabulary is needed rather than a modelling convention.

### 2.2 The yProv ecosystem

This work sits in a family of PROV-based tools: **yProv4ML** instruments machine-learning
training runs, **yProv4WFs** instruments scientific workflows, and **yProvStore** provides
content-addressed storage for payloads too large to inline into a graph. Our demonstrator
consumes real yProv4ML records as its input, which matters for two reasons. The agents are
exercised against provenance that was not written for them, and — as §7 reports — they
find real completeness gaps in it.

### 2.3 Agent lifecycles

Agent frameworks differ in their abstractions but converge on a recurring shape: a unit of
work arrives, the agent plans, acts (often by calling a model or a tool), observes the
result, decides what to do about it, and responds. We take that shape as the unit of
instrumentation, grouped into three phases:

| Phase | Steps | |
|---|---|---|
| Ingestion & scoping | S1 receive · S2 plan | what arrived, and the approach chosen |
| Execution | S3 act | the model call, the tool call, the write |
| Governance & reflexion | S4 observe · **S5 decide** · S6 respond | the check, **the verdict and its reasoning**, the handoff |

S5 is the step this paper is about. It is also the only step whose content an external
observer cannot reconstruct.

---

## 3. Related work

**PROV-AGENT** (Souza et al.) is the closest prior work: it extends PROV for agentic
systems and, like us, treats agents as first-class provenance subjects. It models data as
untyped `DomainData` and carries no decision rationale. The difference is the one this
paper is about — PROV-AGENT records a richer *what*, and the *why* remains outside the
model.

**Non-identifiability of reasoning.** Work on post-hoc reasoning reconstruction (AER;
Vispute et al.) formalises a result we rely on: a reasoning trace cannot in general be
identified from inputs and outputs alone, because many distinct reasoning processes are
consistent with the same observable behaviour. That is the theoretical form of our
structural argument. Where that literature treats it as a limit on explanation, we treat
it as a design requirement: if the rationale cannot be recovered, it must be recorded at
the moment it exists.

**Observability stacks.** OpenTelemetry's GenAI semantic conventions, OpenLLMetry,
OpenInference, Langfuse and LangSmith have effectively solved automatic collection of the
*what* for agent systems: spans, timings, token counts, run trees. None models W3C PROV,
and none captures decision rationale — they are built for debugging and cost attribution,
which are different problems. **Our position is to ride that layer rather than compete with
it** (§5): it supplies the skeleton for free, and the contribution is the layer above.

**Agentic design patterns.** The planning/tool-use/reflection vocabulary of the applied
agent literature supplies our six-step lifecycle. We add nothing to it; we instrument it.

---

## 4. The `yp:` vocabulary and the six-step lifecycle

The vocabulary is published as an OWL ontology (`vocab/yprov4agents.ttl`, 81 terms) with
accompanying SHACL shapes (`vocab/shapes.ttl`, 14 named constraints). Two rules governed
its contents.

**Only terms the system emits.** The term inventory was taken from frozen archives of
actual runs, not from the design documents. A vocabulary defining terms nothing produces
has the same defect as an API nothing implements, and a test enforces the rule: every `yp:`
term appearing in an emitted record must be defined, and terms the library offers but the
demonstrator does not exercise are enumerated explicitly rather than left for a reader to
discover.

**Standard terms wherever one exists.** All six lineage relations are PROV's. The `yp:`
namespace adds exactly two things PROV has no term for: the typed steps of an agent's
decision lifecycle, and the reasoning behind a decision.

### 4.1 Classes

Ten activity classes specialise `prov:Activity` under a common superclass
`yp:AgentLifecycleActivity`: `ReceiveTask`, `Planning`, `ToolCall`, `Analysis`, `Persist`,
`Observation`, **`Decision`**, `Response`, and two governance subclasses,
`ComplianceCheck` and `Audit`. Four entity classes specialise `prov:Entity`: `TaskEntity`,
`ResponseEntity`, `ToolResultEntity`, and `QualityEntity` — the last being the outcome of a
check, made an entity rather than a flag so that a decision can declare it was *derived
from* the finding it rests on.

Typing matters more than it may appear. It is what lets a consumer ask "show me every
decision in this run" without knowing how any particular agent was written, and it is the
precondition for the shapes in §4.3.

### 4.2 `yp:justification`

```
yp:justification  a owl:DatatypeProperty ;
    rdfs:domain yp:DecisionActivity ;
    rdfs:range  xsd:string .
```

One property, on one class. Alongside it sit `yp:verdict` (the conclusion),
`yp:confidence_score`, and `yp:escalation_reason` (which of several independent conditions
caused an escalation — §7 shows why that separation earns its place).

The asymmetry between the verdict and the justification is worth stating, because our own
measurements surfaced it. A verdict is a short token from a fixed vocabulary, and a
sufficiently determined observer can sometimes recover one: our baseline recovers 24 of 24
by scraping the labels developers wrote them into. A justification is prose composed at the
moment of decision out of evidence the step never returned to anyone. No labelling
convention exposes it. **The conclusion is sometimes recoverable; the reasoning is not.**

### 4.3 Shapes: declaring the standard

A compliance agent that enforces a structural standard in hand-written predicates has made
that standard a property of one implementation. `vocab/shapes.ttl` declares it instead:
every `yp:DecisionActivity` carries a non-empty `yp:justification` and a `yp:verdict`;
every lifecycle activity is dated, ended and attributed to an agent; every entity carrying
a `yp:pid` carries a `sha256:` content hash; every record declares its own granularity and
persistence.

Three consequences follow, and the third is the one that paid. A consumer can validate a
record with any SHACL engine without running our agent. The checks acquire names a reader
can cite. And the agent becomes testable *against* the declaration — which immediately
found them disagreeing. The compliance agent tested `if not activity.get("yp:justification")`,
so a justification of pure whitespace passed; the shape's `sh:pattern "\S"` rejects it; the
evaluation harness stripped before counting. Three implementations of one rule, and one
disagreed. **A rule written once in one place cannot disagree with itself.**

The shapes are themselves tested for falsifiability: eight mutations of a conforming record
— removing a justification, blanking it, removing a start time, corrupting a content hash —
each produce the objection the corresponding shape exists to produce. A shape suite that
passes everything reports perfect conformance over an empty check.

---

## 5. Instrumentation: two layers

### 5.1 The decisive constraint

> **Figure 1.** The two layers: what an external observer can collect automatically, and
> what it structurally cannot.

Behavioural provenance splits cleanly in two, and the split is not a matter of effort
budget — it follows from what an observer can see.

**Layer 1 — what happened, and when.** Activities, timings, tool and model calls, token
counts, and the dataflow between steps. An external observer sees all of this: a framework
callback receives run trees with inputs, outputs and parents, from which activities,
entities and lineage follow mechanically. *This layer can be automated, and the ecosystem
has already automated it.*

**Layer 2 — why it happened.** The verdict, the confidence, and the justification. *This
layer cannot be automated*, for the reason in §1.

Our strategic position follows directly: **consume Layer 1 where it exists, own Layer 2.**
Competing with OpenTelemetry on collection would be wasted effort; the defensible
contribution is the semantic layer above it, which no observability tool models.

### 5.2 The API

Three constructs — a decorator, a context manager, and one explicit call:

```python
tracker = ProvTracker(sid=session, agent_label="NL_Summarizer", sink=publish)

with tracker.activity("ToolCallActivity", "write the summary") as prov:
    prov.consumes(task)
    summary = llm.complete(ACT_SYSTEM, facts)
    prov.produces("ResponseEntity", summary.text, pid=pointer, derived_from=task)
    prov.note(**summary.provenance())          # model, prompt hash, tokens, latency

tracker.decide(                                 # the hand-written line
    verdict="REVISE",
    justification="Figures 4096 and 512 appear in the summary but not in the graph's "
                  "counts, so the text asserts something the record does not support.",
)
tracker.emit()
```

What the wrappers supply automatically is exactly what is observable: the activity's type,
label, start instant, measured duration, attribution, position in the sequence, and the
failure case — a step that raises is recorded as having run and failed, because a gap in a
record is indistinguishable from a step that never ran.

What must be declared is what is not observable. A wrapper sees that a function ran; it
does not see which of the values in scope that function treated as its input. Lineage is
therefore stated through the scope and never inferred. We learned this the hard way: an
earlier version of our tracker inferred derivations from the order in which entities were
registered, producing 74 confident and wrong edges out of 250. `wasInformedBy` is the one
relation that *is* automatic, because PROV defines it as derivable — *a₂* was informed by
*a₁* when *a₁* generated an entity *a₂* used.

**`decide()` raises on a blank justification.** Every other field can be filled in by
machinery; this one cannot, so the API refuses to let a caller skip it — at the call site,
where the reasoning still exists as a value. This has a consequence we did not anticipate
and report in §7: the compliance agent's FAIL verdict is *unreachable* for records this
library produces, because an agent instrumented with it cannot emit an unjustified
decision.

### 5.3 Two properties the layer commits to

**It adds no record kind of its own.** Anything expressible through the ergonomic API is
expressible through the underlying primitives. We test this directly — the same lifecycle
built both ways reduces to an identical document — and it is why moving all six agents of
the demonstrator onto the API produced a structural difference of exactly zero across 24
emitted records.

**Timing is measured, not merely stamped.** PROV records an activity as two instants, which
invites reading both from the wall clock. That loses short steps: on our development host
the system clock advances in steps of roughly 15.6 ms, so two readings either side of a
sub-millisecond operation return the *same* value. Before we corrected this, **96 of 96
activities recorded a zero duration** — a timeline of zero-length events, from which
nothing about where a run spent its time can be recovered. The start instant now comes from
the wall clock, where it must be to compare across processes, and the duration from a
monotonic counter. This is a precondition for anything downstream that reads the timeline,
including our own analytics agent and the overhead study in §7.

### 5.4 Framework-agnosticism

The vocabulary is framework-independent; only a thin adapter binds the six-step lifecycle
to a given framework's events. The demonstrator in §6 is the bare-daemon case — six agents,
no agent framework at all, same instrumentation, same output — which is the strongest
available evidence that the library is not welded to one runtime.

---

## 6. The demonstrator

The system described here runs. It consists of a message bus, a producer that replays real
yProv4ML provenance records, four operational agents that do work and emit typed PROV about
themselves, and two meta agents that consume *that* provenance and emit PROV in turn. It
uses **no agent framework at all** — the agents are bare asyncio daemons — which is
deliberate: it is the strongest available evidence that the instrumentation is not welded to
one runtime's callback model.

```
 data/yprov4ml/* --> PRODUCER --producer.yprov4ml.<session>--> BUS (NATS)
                                                                |
                 +----------------------------------------------+
                 v                                              v
     OPERATIONAL AGENTS                                CA1 StorageAgent
     CAx . CA2 . CA3                                   holds --> awaits acks
     consume, do work,                                 --> quorum --> persists
     emit their OWN PROV                                         |
                 |                                               v  store API
                 | agent.lifecycle.<role>.<session>        yProvStore / KG
                 v                                        (BEHIND CA1, not on the bus)
          META AGENTS  MC1 . MC3
          observe, govern, emit governance.<role>.*
          (and are themselves tracked the same way)
```

*(Not one of the paper's four figures; this becomes prose or an appendix figure.)*

### 6.1 The agents

| | Agent | What it does | Kind |
|---|---|---|---|
| CA1 | StorageAgent | hashes, stores, mints a `yp:pid`, verifies the round trip | deterministic |
| CAx | NL_Summarizer | summarises a graph in natural language | LLM, with a computed fallback |
| CA2 | Metrics_Visualizer | renders a chart from the graph's measurements | deterministic (matplotlib) |
| CA3 | Analytics_Agent | five algorithms over any PROV graph | heuristic |
| MC1 | ComplianceChecker | enforces the declared shapes over other agents' records | deterministic |
| MC3 | AuditabilityAgent | reconstructs a who-did-what-when trail | deterministic |

Three kinds of agent the instrumentation has to serve are present — deterministic,
model-backed, and hybrid — which is what contribution C3 rests on. All six are
domain-agnostic: every computation derives from PROV itself (types, roles, timestamps,
relations), never from an ML-specific schema, so the same agents apply to a workflow record
as to a training record.

CA3 deserves a note, because it began as an anomaly detector. Anomaly detection is one
analytics task over a provenance graph, not an agent's identity; CA3 runs five (structure,
timing, sustainability, resource, anomaly) and reports which fired. The generalisation is
what let it produce a result on the first run that we had not anticipated (§7.3).

### 6.2 Ordering as an architectural property

Two design decisions are visible in the provenance, and worth stating because the record is
what makes them checkable.

**The store is behind the persist agent, not on the bus.** CA1 writes through the store's own
API from its backend; the bus carries a *storage receipt* — a `yp:pid` and a content hash —
and never a payload. A provenance bus that carries payloads becomes a data plane, and the
pointers-not-payloads choice is what keeps records small enough to emit per step.

**Persistence happens after consumption, and the record says which way it went.** CA1 holds
each incoming record, counts acknowledgements off `agent.lifecycle.*` from three named
consumers, and persists at quorum. A timeout guard persists anyway if a consumer never
reports, so a crashed subscriber cannot lose a record. The two paths are not equivalent and
the agent is required to say which it took: `ACCEPT` at quorum, `ESCALATE` on timeout, with
`yp:escalation_reason` separating a *consumption-unconfirmed* escalation from an *integrity*
one. Those two demand opposite operator responses — one says a consumer is down, the other
says the stored bytes are wrong — and §7.4 shows that an external observer can see the
`ESCALATE` and cannot tell which fired.

### 6.3 Lifecycle subsets

The operational agents instantiate all six lifecycle steps. The meta agents instantiate a
three-step subset — S1 receive, S3 act, S5 decide — because they neither plan an approach
nor answer a caller. We therefore phrase C3 as **a template with declared subsets**, not as
uniform instantiation: the measured coverage in §7.2 supports the former and contradicts the
latter.

### 6.4 Reflexivity, and the depth we claim

Meta agents subscribe to `agent.lifecycle.>` and publish to `governance.<role>.<session>`.
Nothing subscribes to `governance.*`, so the governance layer cannot re-trigger itself and
**reflexivity in a run is one level deep**: the operational agents are governed; the
governance layer is not.

That bound is a termination property rather than an oversight. An agent that governs
governance output emits a record which is itself governance output, and nothing in the
subject structure stops the next pass; publishing meta output to a prefix nobody subscribes
to makes the recursion impossible by construction, which is worth more than a depth counter
someone can forget to check. It is enforced as a test rather than documented as a caveat —
`test_governance_output_is_terminal_by_design` fails if any agent starts consuming
`governance.*` without a bounded-depth scheme arriving alongside it.

What does hold at that depth is stronger than a governance table: the meta agents are
themselves fully instrumented, and **MC1 satisfies its own standard** (§7.6).

### 6.5 Deployment, and two run paths that are not equivalent evidence

A single container image serves all six roles, selected by a `ROLE` environment variable,
and is rootless-friendly so it converts cleanly to Apptainer for an HPC deployment later. A
second path runs the same agents over an in-process bus, with no broker and no container.

Both emit the same provenance, and a CI gate checks that on every commit. They are not the
same evidence. The in-process bus awaits each handler inline in subscription order, so a
consumer's acknowledgement is delivered from inside the producer's own publish call and none
of the distributed path's concurrency is exercised. We use it for isolation in measurement,
never as evidence about ordering or throughput, and the overhead study's pipeline level is
labelled accordingly (§7.9).

---

## 7. Evaluation

Every figure below is regenerated from frozen archives under `experiments/`, each with
per-file hashes, by `uv run python run_local.py && uv run python -m eval.evaluate`. None is
transcribed by hand. A CI matrix (Linux and Windows, Python 3.11 and 3.12) re-runs the
pipeline and fails if the emitted provenance changes without the change being declared; 180
tests pass, with no expected failures.

The reference run is two input records through six agents, producing **24 PROV documents**.

### 7.1 Conformance, the precondition for every other claim

Interoperability with W3C PROV is an explicit contribution, so it is judged by an independent
implementation — the reference `prov` library — rather than by our own code.

| | before | after |
|---|--:|--:|
| Documents accepted by the reference PROV-JSON parser | **0 of 24** | **24 of 24** |
| `yp:pid` pointers dereferenceable by a fresh process | **0 of 8** | **8 of 8** |

A single non-standard key at the document root made a conforming parser reject every file
whole. The interoperability claim had therefore been *inverted*, not merely weak, and no
amount of care in the rest of the model would have revealed it. We report the figure and name
the judge, because that is the difference between a standards claim and an assertion.

### 7.2 Structural coverage

| Agent | Records | Activities | Entities | Relations |
|---|--:|--:|--:|--:|
| CA1 StorageAgent | 2 | 6 | 4 | 21 |
| CAx NL_Summarizer | 2 | 6 | 4 | 21 |
| CA2 Metrics_Visualizer | 2 | 6 | 3 | 19 |
| CA3 Analytics_Agent | 2 | 6 | 4 | 21 |
| MC1 ComplianceChecker | 8 | 3 | 3 | 10 |
| MC3 AuditabilityAgent | 8 | 3 | 3 | 10 |

96 activities and 324 relations in total — and the total is the wrong number to quote.
Relations have to be read by what established them:

| How the edge was established | Count | Share | Which |
|---|--:|--:|---|
| **Observed** — a step said it read or wrote this | 126 | 39% | `used` 72, `wasGeneratedBy` 54 |
| **Derived** — follows from PROV's own definition | 72 | 22% | `wasInformedBy` 72 |
| **Declared** — a step asserted this derivation | 30 | 9% | `wasDerivedFrom` 30 |
| **Attributed** — one per activity, to its agent | 96 | 30% | `wasAssociatedWith` 96 |
| **Total** | **324** | 100% | |

The last category is exactly one edge per activity, so it counts how many steps ran rather
than how they were connected. **The structural claim is 228, not 324.** Quoting the total
invites precisely that objection, and the split forecloses it.

### 7.3 Conformance to a declared standard, and what declaring it found

Two measurements are easy to confuse here, and only the second is a result.

**Coverage: 24 of 24 decisions carry a justification.** This is a conformance check on our
own instrumentation. The code that counts decision activities is the code that justifies
them, so no run of this system could report another number, and the figure says nothing
about whether a justification is *informative*. It is evidence that no decision point was
left uninstrumented, and nothing more.

**Conformance: 24 of 24 records satisfy the published SHACL shapes.** This one has an
external judge. The rule — every `yp:DecisionActivity` carries a non-empty
`yp:justification`, on an activity that is dated, ended and attributed — is declared in
SHACL in the vocabulary, not written into the agent that enforces it, and it is applied by
`pyshacl` over RDF converted by the reference `prov` library. Neither the rule nor the
engine is ours.

Declaring the standard immediately **found a defect in the agent that had been enforcing
it.** MC1 tested whether the justification field was empty, so a justification of pure
whitespace passed; the shape requires a non-whitespace character and rejects it. Three
implementations of one rule, and the agent was the one that disagreed — which is the argument
for declaring a standard rather than implementing it: *a rule written once in one place
cannot disagree with itself.*

The shapes are also tested for teeth. Each is checked by mutating a conforming record in the
specific way that shape exists to catch; a shape suite that accepts everything is decoration,
and this is how the whitespace gap surfaced.

CA3, applied to its own input rather than to our output, found **real completeness gaps in
yProv4ML's published records**: 8 of 9 activities declare a start time with no end time, and
entity typing coverage is 82.1%. We report these as *completeness* findings rather than
anomalies — they describe what the producer recorded, not how the run behaved.

### 7.4 Capture, not retrieve: the A-versus-B comparison

> **Figure 3.** Strategy A against Strategy B, signal by signal: structure recovered in
> full, and the one row where the two diverge.

This is the paper's central measurement (Figure 3). Strategy A is a callback-level baseline
we built ourselves. It is fed the 96 step events that a framework callback handler receives
— node name, timings, input and output payloads, errors — from the *same run* as Strategy B,
and reconstructs as much W3C PROV as that stream supports.

| Signal | A, zero config | A, with type map | B |
|---|--:|--:|--:|
| Documents emitted | 24 | 24 | 24 |
| Activities | 96 | 96 | 96 |
| …of them typed | 0 | 96 | 96 |
| Entities | 54 | 54 | 78 |
| …of them typed | 0 | 0 | 78 |
| …carrying a resolvable pointer | 0 | 0 | 8 |
| Relations, total | 324 | 324 | 324 |
| Decisions carrying `yp:verdict` | 0 | **24** | 24 |
| Decisions carrying **`yp:justification`** | **0** | **0** | **24** |
| Accepted by the reference `prov` parser | 24 | 24 | 24 |

Four readings, in the order a reviewer will test them.

***A is an upper bound, deliberately.*** The event stream is built from the declarations the
explicit instrumentation made: the entity each step said it read, the entity each step said
it wrote. A real handler receives a node's whole input payload and must guess which part
mattered, so it would over-report `used` and could not separate a load-bearing input from an
incidental one. Conceding A every structural fact B declared turns the last row from a
comparison between two implementations into a statement about the **ceiling**: even granted
everything explicit instrumentation declares, the rationale is unreachable.

***Structure is recoverable, and the baseline recovers all of it.*** 96 activities and all
324 relations, identical to B. An earlier version of this table claimed four zeros down the
A column and three of them were false — including the claim that a callback reconstruction
produces no conformant PROV-JSON, which it plainly does. Syntax was never the differentiator,
and a baseline dismissible on syntax would make this comparison about serialisation.

***Types and identity are where the structural gap actually is.*** A types nothing with no
configuration and everything with ten hand-written label patterns — so the types exist, but
as a per-system artifact someone writes and maintains, and only because these node labels
happen to be descriptive. Entities are worse: A is handed payloads rather than references,
mints identity from content, types none of the 54, and carries no pointer at all. The
pointers-not-payloads design has no analogue in a reconstruction that never saw where a
payload was stored.

***The verdict is often recoverable; the reasoning never is.*** A recovers 24 of 24 verdicts,
and we report that as fragile rather than impressive: it recovers them by scraping a
developer-chosen node label, and a graph whose decision node is named `decide` yields none.
The justifications are recoverable under no labelling convention at all. Searching everything
the handler could read — **222 strings, 12,425 characters** — finds **0 of 24**. Each
justification is prose composed at the moment of decision out of evidence the step never
returned.

**This is the single earned zero**, and it is worth more than the four unearned ones it
replaced.

### 7.5 The same decision, under both strategies

> **Figure 2.** One verdict, two causes: the quorum path and the timeout path through the
> same code, with both justifications quoted as the record carries them.

The qualitative companion to that table, and the paper's best evidence for C1 (Figure 2).
CA1's justification genuinely differs by what happened, in the same code path:

> `example_2` — **ACCEPT**, trigger `quorum`, 3 of 3: *"Persisted at quorum … acknowledged
> by [ca2, ca3, cax], the full set required."*
>
> `example_0` — **ESCALATE**, trigger `timeout`, 2 of 3: *"Persisted WITHOUT confirmed
> consumption … the persist-after-consume ordering was not satisfied."*

Strategy A recovers exactly one fact about either decision: the verdict, scraped out of the
node's label. Strategy B records the trigger, the quorum reached, the escalation reason, the
integrity check that *passed*, and the choice to archive anyway rather than lose the record.
Because CA1 escalates for two independent reasons that call for opposite responses, A cannot
distinguish *"storage is corrupting records"* from *"ca3 is not running"*. The structural
comparison says the rationale is absent; this says what being absent costs.

### 7.6 Reflexivity, and self-application

All four operational agents are governed by both meta agents, and the meta agents emit their
own typed PROV — visible in the artifact names, where a summariser's record is governed into
a compliance record over it.

The sharper result is **self-application**. MC1 enforces that every agent record a justified
decision activity; applying MC1 to MC1's own output is a one-line experiment, and it
**failed** when first run — the checker could not satisfy its own rule. It now passes, as
does MC1 applied to MC3, and both are tests in the suite rather than claims in prose. The
depth bound of §6.4 is likewise a tested invariant.

### 7.7 The two steps that are not deterministic

**Model provenance.** Before this work the emitted graph named no model, no sampling
parameters, no prompt and no token counts, so the one step in the pipeline that was not
deterministic was the one step the record could not account for. Every model-bearing step now
carries `yp:model`, `yp:temperature`, `yp:max_tokens`, `yp:prompt_id`, `yp:prompt_version`,
`yp:prompt_sha`, `yp:prompt_tokens`, `yp:completion_tokens`, `yp:llm_latency_s`,
`yp:finish_reason` and `yp:response_sha` (`experiments/phase4-llm`: four gpt-4o calls, 2,071
prompt and 473 completion tokens).

Three details carry a general lesson. *A prompt is apparatus, and had to be routed like one*:
a prompt registry with versions and content hashes already existed and nothing imported it,
because both LLM agents built their prompts as inline f-strings. The fix was not new
infrastructure but making the call site take a prompt object rather than a string, so a
prompt cannot reach a model without its identity reaching the record. *An absence is recorded
as an absence*: the computed fallback path stamps no model attributes at all, rather than
zeros or `"none"`, because a zero token count is a measurement and on that path no
measurement was made. *The response hash and the entity pointer agree here, and may
legitimately disagree* — one says what the call returned, the other what was stored — so an
agent that post-processes model output before storing it would make them differ, and that
divergence is informative.

**A negative result: energy cannot be attributed per step.** We wired an energy backend into
the capture API, and the wiring produced a result worth reporting on its own terms. A
sampling backend cannot attribute energy to a step shorter than its sampling period.
codecarbon's default period is 15 seconds; these steps last milliseconds, so a delta lands on
whichever step happens to read after a tick:

| step | energy charged | step duration |
|---|--:|--:|
| choose what to plot | 46.9 µWh | 0.000 s |
| render chart (matplotlib) | 4.6 µWh | 1.328 s |

The step that did the work was charged a tenth of what the step that did nothing was charged.
A second artifact was worse: the *first* step of each record was charged 28.3 seconds of
inter-message idle time, because the tracker outlives any one record. Both are now handled —
the baseline resets when a record begins — and, more importantly, each figure is published
alongside `yp:energy_sampling_period_s` and `yp:energy_attributable`, so the record states
that the number is real consumption in a real window but **not** an attribution. **Energy
belongs at session granularity.** Any per-step energy claim in this literature needs a
backend sampling faster than the steps, and should state its period.

### 7.8 What the instrumentation costs

Absolute timings are machine-specific — a four-core laptop, CPython 3.11, with other
processes on it. The transferable results are the ratios, the byte counts and the complexity
class, and §7.9 says which is which. A difference is reported as a result only if it reaches
**3× the dispersion of its own samples**; otherwise it is reported as unresolvable.

**The headline is better than a percentage: the instrumentation is cheaper than the noise
floor of the work it instruments.** At 30 interleaved repetitions, four of the six agents
show no resolvable overhead — the difference between running them with and without
provenance is smaller than the difference between two runs of either.

**The field this paper argues for costs nothing we can resolve in time, and 278 bytes in
storage.** Over 40 interleaved rounds of 1,000 calls, the paired difference between recording
an activity with and without a justification is **26 ns against a round-to-round spread of
±352 ns**. The same field is 278 bytes of output, **21.7% of a record**. The trade is
**storage for explainability, not speed for explainability** — a far more defensible claim
than "it is free", and the one the data supports. It survived being made harder: the
justifications were rewritten mid-project to state the results of real checks rather than
restating their own instruction, so this is not a measurement of string concatenation.

> **Figure 4.** Where the overhead actually is: each agent's own work, the cost of building
> the record, and the cost of writing it, as shares of one end-to-end record.

**Overhead is resolvable only for the two agents whose own work is trivial, and there it is
large** (Figure 4). We lead with the worst case:

| | MC1 ComplianceChecker, the worst case in the system |
|---|--:|
| the agent's own work | 96 µs |
| …plus building a complete typed PROV record | **+42 µs** |
| …plus writing it to disk | **+1,532 µs** |
| total overhead | **+1,645%** (margin 8.2×) |

Read the decomposition before believing the ratio: **97% of that overhead is one file
write**. Explicit instrumentation is not expensive to *compute*; persisting its output is,
and persistence is a deployment decision. Both knobs are exposed and both were measured:
transient mode is **−91% of the time cost** for a byte-identical record, and metadata
granularity is **−37.6% of the bytes** while retaining every justification. A mode that shed
justifications would be a different product, not a cheaper one.

**The dominant per-step cost of this library is rendering an instant as text.** A tracked
step is 4.49 µs, of which 2.88 µs is producing the timestamp — and 3× more of that is
formatting the value than reading the clock. That is a property of what PROV stores rather
than of how it is built: a faster hot path means changing the record format. For anyone else
implementing a provenance library it is the most transferable number in the study.

**The cost is linear in the size of the graph, and was not when first measured.** Building a
record fits an exponent of 1.066 and serialising it 1.124, with memory at roughly 1.3 KiB per
activity. The first run of the sweep fitted **1.468**, and a 10,000-activity record took
1.72 s; the cause was a duplicate check on declared derivations that scanned a list. With an
O(1) membership set the same record takes 88 ms, a 19.6× improvement. The defect was
invisible at every scale the
tests and the demonstrator use — six activities — and would have reached this paper as an
unqualified linear claim.

**Two library defects were found by this study and fixed before publication**: the quadratic
derivation check above, and an uncached metadata lookup making serialisation 306× slower than
it needed to be. Neither is a result about provenance. Both are the argument for measuring
before publishing a performance claim rather than after.

### 7.9 Which of these figures transfer

The whole study was run twice on separate occasions. Absolute timings moved by up to **26%**;
byte counts were **identical to the byte**; fitted exponents agreed to within 3%; and the
resolvability verdicts were unchanged for every agent. A third run, taken while the machine
was loaded by this study's own earlier levels, inflated every absolute figure by about 3× and
changed **no verdict** — which is the strongest evidence available that the margin criterion,
not the clock, is what makes these numbers safe to read.

**Quote the bytes, the complexity class and the verdicts; quote the timings as orders of
magnitude.**

That criterion is stricter than the one we began with, and the correction changed two
published rows. It started as *larger than the noise*, which treats any consistent bias as a
finding, and a loaded machine supplies plenty; two agents had been reported as carrying
resolved overhead on margins of 2.9× of their own dispersion. The project's own guard test
caught it — on a shared CI runner, timing
the **same function twice** produced a 147.5 ns difference against 61.0 ns of dispersion and
called it real. The fix was to counterbalance the pair order, so a fixed-order bias cancels
instead of accumulating, and to require a 3× margin. **Write the guard test that fails when
your harness reports noise as a result.**

Three confounds, stated plainly. The pipeline-level figure is inside the noise and is
dominated by a fixed persist window and by process start-up; it is also sequential and
single-process, so it is not a throughput measurement. CA2's own work is matplotlib
rendering, three orders of magnitude larger than its instrumentation, so its overhead is
unresolvable — reported as such, rather than as the 5% *speed-up* an earlier version of the
harness produced. And condition A is indistinguishable from OFF at agent level: the callback
layer is cheap, and what it cannot do is not a matter of cost.

### 7.10 Verdict reachability

A verdict that only ever takes one value carries no information, so the tally is the less
interesting half of this result; what has to be established is that the alternatives are
**reachable**. The ordinary run reaches six of twelve. The other six come from seven injected
faults, archived under `experiments/verdict-reachability`:

| Verdict | How it was provoked |
|---|---|
| `ca1:ESCALATE` *(integrity)* | a store that writes bytes it did not hash |
| `ca1:ESCALATE` *(consumption-unconfirmed)* | no consumer acknowledged before the timeout |
| `mc1:FAIL` | a third-party record whose decision carries no justification |
| `mc3:PARTIAL` | a third-party record with an activity started and never closed |
| `ca2:ACCEPT` | a producer record that actually carries measurements |
| `cax:REVISE` | a scripted model reply naming figures absent from the graph |
| `ca3:OK` | a fully instrumented graph with regular timing |

**Twelve of twelve verdicts observed.** Three of the seven are not failures at all: CA2's
ACCEPT, CAx's REVISE and CA3's OK are unreachable from the bundled sample data, because the
real yProv4ML records carry no numeric values to plot and declare start times without end
times. So *"CA2 always says REVISE"* was a finding about the **input**, not about CA2, and
separating those two is what these scenarios are for. It also sharpens CA3: ANOMALY is
informative precisely because OK is reachable.

One row is a property rather than a result. **`mc1:FAIL` is unreachable from records this
library produces**, because the decision call refuses a blank justification at the call site,
so an agent instrumented with this library cannot emit an unjustified decision. FAIL is
reachable for records the library did not write — which is exactly the case a compliance
checker exists for, since it is pointed at agents you do not control. *The instrumentation
makes the violation unrepresentable, and the governance layer catches it in input that was
not instrumented.*

Every fault is injected into the input or the environment, **never into the check being
tested**, and the suite enforces all seven scenarios plus the matrix itself — so a verdict
advertised and never observed fails the build.

---

## 8. Discussion and limitations

Each of the following is a real property of what we built. We state them because a limitation
found by a reviewer costs far more than one declared by an author, and because several are
stronger stated than omitted.

**We measure that justifications exist and are externally unrecoverable; we do not measure
whether they are *good*.** This is the most significant limitation of the evaluation. §7.3's
coverage figure is a self-report by construction, and §7.4 establishes unrecoverability, not
quality. Whether an operator handed one of these justifications acts correctly more often is
an empirical question requiring a human study, and we have not run one. The worked example in
§7.5 is an argument, not evidence.

**Reflexivity is one level deep.** The governance layer observes and judges the operational
layer and is not itself consumed. Self-application is demonstrated as a property — MC1
satisfies its own standard — rather than as a running loop, and contribution C2 carries that
qualifier (§6.4).

**A provenance system can over-report as easily as under-report.** An earlier version of our
tracker asserted derivation from registration order and informed-by from recording order,
producing exactly *n*−1 edges for *n* items regardless of what the agent actually did. The
relations were corrected to declared-or-derived, and the correction changed the graph's
*topology* rather than only its size: the old chain asserted a single dependency spine where
the real dataflow is a tree. We disclose this because it is what makes the counts in §7.2
trustworthy, and because **relation counts are the easiest place in a provenance system for
over-reporting to go unnoticed.**

**A model-bearing step is repeatable from its record, not bitwise reproducible.** The record
now carries the model, the sampling parameters and the prompt hash (§7.7), which is what an
auditor needs in order to re-run the call and what catches a forgotten version bump. It
cannot make a hosted model deterministic: a provider may change the weights behind a model
name, and sampling is not reproducible across them. The record makes the apparatus explicit;
it does not make the experiment closed.

**Content-addressed identifiers are mock handles.** The `21.T11999` prefix is not registered
to this project and nothing resolves it globally. Identical payloads also collapse to a single
identifier, so the pointer identifies *content* rather than a storage event.

**The transport can drop provenance.** NATS core is at-most-once and JetStream is not
configured, so a subscriber that is not running when a record is published never sees it. The
readiness handshake mitigates start-up ordering and does nothing about a mid-run
disconnection. A provenance bus that can silently lose provenance deserves a sentence in a
provenance paper.

**Two long-running resource bounds.** A tracker holds its whole record in memory until it
emits — measured at roughly 1.3 KiB per activity, so a 10,000-step record costs about 13 MiB
of live memory before a byte is written — and the storage agent's record of handled sessions
grows for the life of the process. For an agent with a bounded turn both are irrelevant; for a
long-lived deployment they need an eviction policy, and they are the reason a metadata-only
granularity exists.

**Scale, and a single producer family.** Two input provenance records, 24 emitted documents,
one producer (yProv4ML). Nothing in the agents is ML-specific — every computation derives from
PROV itself — but that generality is argued from the code rather than demonstrated against a
second producer, and yProv4WFs is untested here.

**The baseline is our own construction.** We built Strategy A, so we could in principle have
built it badly. The mitigation is to grant it every structural fact the explicit
instrumentation declares and to report it as an upper bound (§7.4); the resulting table
concedes A everything except the one row the paper is about.

**Library surface exceeds demonstrated surface.** Memory registration and
`prov:actedOnBehalfOf` delegation are offered by the library and not exercised by the
demonstrator; the vocabulary defines them and the shape suite enumerates them as known
exceptions, so the gap cannot drift into being undocumented.

---

## 9. Conclusion and future work

The provenance community can describe what an agentic system did. With the automatic-capture
tooling that dominates agent observability it cannot describe why — and we have argued that
this is a structural property rather than a tooling deficiency. The rationale for a decision
is a local variable at the moment of decision, and no callback, span, or post-hoc query can
retrieve what was never captured.

We made that argument measurable by building the strongest callback-level baseline we could
and granting it every structural advantage: it reconstructs 96 of 96 activities, all 324
relations and 24 of 24 verdicts, serialises to conformant W3C PROV-JSON, and recovers **0 of
24 justifications** from 12,425 characters of everything it could see. We discharged the
argument with a small explicit instrumentation layer — a published `yp:` vocabulary with
SHACL shapes, and an API whose decision call refuses a blank justification — and demonstrated
it across six heterogeneous agents in a running reflexive system, including the agents that
govern the others. And we measured the cost: nothing resolvable in time, 278 bytes and 21.7%
of a record in storage, with every agent that does substantial work showing instrumentation
overhead smaller than its own run-to-run variance.

The honest summary of the trade is one sentence: **storage for explainability, not speed for
explainability.**

Four extensions follow directly. **Actuation** is the largest: the meta layer today observes,
judges and gates, and making it *reconfigure* or *activate* an operational agent requires a
control-channel design with its own provenance — an action taken on the basis of a
justification should be derivable from it. **Bounded second-level reflexivity**, with the
depth carried in the subject so that recursion remains impossible by construction, would lift
the one-level qualifier on C2 without reintroducing the termination problem. **A Layer-1
OpenTelemetry bridge** would let the automatic half of the record come from instrumentation a
deployment already runs, leaving only the irreducible explicit part to the developer — the
deployment story this paper's argument implies. And **a justification-quality study** is the
missing evaluation: we have shown that reasoning can only be captured and never retrieved,
and not yet that capturing it makes an operator's decisions better.

**Artifact availability.** *(To be completed: the library, the vocabulary and SHACL shapes,
the frozen experiment archives and the benchmark harness are all in the project repository; a
citable archive is pending a decision on public release.)*
