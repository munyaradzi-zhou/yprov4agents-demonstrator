# Capture, Not Retrieve: Behavioural Provenance for Agentic AI

**Draft — §1–§5.** §6–§9 follow; the material for them is in `docs/DESIGN.md`,
`paper/OUTLINE.md` E0–E6, and `docs/E6_RESULTS.md`.

> Every figure quoted here is read from a frozen archive under `experiments/`, named at
> the point of use. Where a number could not be measured, the text says so rather than
> estimating.

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

*(§6 The demonstrator, §7 Evaluation, §8 Discussion & limitations, §9 Conclusion: to
follow. Material is in `docs/DESIGN.md`, `paper/OUTLINE.md` E0–E6 and `docs/E6_RESULTS.md`;
Figures 2–4 are rendered by `python -m paper.figures`.)*
