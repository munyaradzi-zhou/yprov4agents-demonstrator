# Capture, Not Retrieve: Behavioural Provenance for Agentic AI

**Draft, §1–§9 — 5,800 words, still about 2,500 over a 6-page budget.** Cut from the 7,900-word
full draft at commit `53c5b61`, which remains the reservoir for anything a reviewer asks to see
expanded. Prose compression is now exhausted: closing the remaining gap means **dropping
results**, which is an authorial decision, and `paper/OUTLINE.md` sets out the ranked options.
The arithmetic, so the next editor need not redo it: 6 IEEE pages hold roughly 5,700 words of
pure text, less ~0.4 page of title block, ~0.5 page of references, ~1 page for four figures and
~0.7 page for five tables — leaving about **3,300 words**.

> Every figure quoted here is read from a frozen archive under `experiments/`, named at the
> point of use. Where a number could not be measured, the text says so rather than
> estimating.

**Figures** (`python -m paper.figures` → `paper/figures/`): Figure 1 = `f1_two_layers` ·
Figure 2 = `f4_two_escalations` · Figure 3 = `f5_a_versus_b` ·
Figure 4 = `f9_where_the_overhead_is`.

---

## 1. Introduction

Scientific computing infrastructure is acquiring autonomous agents. They summarise runs,
select hyperparameters, decide what to archive, and increasingly act on those decisions
without a human in the loop. The provenance community has a mature answer to *what* such a
system did: W3C PROV describes activities, entities and the lineage between them, and tooling
to capture that lineage automatically is now routine.

It has almost no answer to *why*.

When a storage agent declines to archive a record, when a summariser rejects its own output,
when a compliance agent fails a colleague's record — an operator, an auditor, and increasingly
a regulator need the reason, not the outcome. The EU AI Act's transparency obligations are
written in the language of explanation rather than of logging. A record that says a decision
was taken, and when, and by which component, and still cannot say why, does not meet that bar.

Our claim is that this gap is **structural rather than a matter of insufficient tooling**:

> The rationale for a decision is a local variable at the moment of decision. A callback fires
> after the function returns; a span wraps the call; a query reads the store afterwards. None
> of them can retrieve what was never captured.

We call this **capture, not retrieve**. The corollary is uncomfortable for the
observability-first approach that dominates agent tooling: no amount of additional collection
closes the gap, because the information is not in the span. Behavioural provenance therefore
has an irreducible explicit component — instrumentation a developer must write, at the point of
decision, that no framework can supply on their behalf.

**C1 — Capture, not retrieve.** A structural argument supported by an A-versus-B comparison
over the *same agents in the same run*. We build the callback-level baseline ourselves and make
it as strong as we can: it reconstructs 96 of 96 activities, all 324 relations and 24 of 24
verdicts, and its output is accepted by the reference W3C PROV parser. It recovers **0 of 24
justifications** — measured by searching every string it observed, not asserted (§7.4).

**C2 — A reflexive multi-agent provenance architecture.** Four operational agents emit typed
PROV about themselves; two meta agents consume *that* and emit PROV in turn. Running,
containerised, and governing its own governance layer to a depth we state exactly (§6.1).

**C3 — The `yp:` vocabulary and a six-step lifecycle**, published as an OWL ontology with SHACL
shapes, instantiated by four heterogeneous agents — deterministic, LLM-backed and hybrid — and
governed uniformly against the declared shapes (§4).

We also measure the cost, because an argument that explicit instrumentation is *necessary* is
worth little if it is expensive. It is not: the field we argue for costs nothing our instruments
can resolve in time and 278 bytes in storage, and every agent that does substantial work carries
instrumentation overhead smaller than its own run-to-run variance (§7.7).

---

## 2. Background

**W3C PROV** models provenance as three classes — `prov:Entity`, `prov:Activity`, `prov:Agent`
— and a small set of relations between them. Its deliberate domain-neutrality is why it says
nothing about rationale: PROV describes *what was derived from what, by whom*, and a
justification is none of those things. It is a property of an activity that no relation can
express, which is why an extension vocabulary is needed rather than a modelling convention.

**The yProv ecosystem** instruments machine-learning runs (yProv4ML) and scientific workflows
(yProv4WFs), with content-addressed storage for large payloads (yProvStore). Our demonstrator
consumes real yProv4ML records, so the agents are exercised against provenance not written for
them — and, as §7.3 reports, they find real completeness gaps in it.

**Agent lifecycles.** Frameworks differ in their abstractions but converge on a recurring
shape, which we take as the unit of instrumentation:

| Phase | Steps | |
|---|---|---|
| Ingestion & scoping | S1 receive · S2 plan | what arrived, and the approach chosen |
| Execution | S3 act | the model call, the tool call, the write |
| Governance & reflexion | S4 observe · **S5 decide** · S6 respond | the check, **the verdict and its reasoning**, the handoff |

S5 is the step this paper is about, and the only one whose content an external observer cannot
reconstruct.

---

## 3. Related work

**PROV-AGENT** (Souza et al.) is the closest prior work: it extends PROV for agentic systems
and, like us, treats agents as first-class provenance subjects. It models data as untyped
`DomainData` and carries no decision rationale. The difference is the one this paper is about —
PROV-AGENT records a richer *what*, and the *why* remains outside the model.

**Non-identifiability of reasoning.** Work on post-hoc reasoning reconstruction (AER; Vispute
et al.) formalises a result we rely on: a reasoning trace cannot in general be identified from
inputs and outputs alone, because many distinct reasoning processes are consistent with the same
observable behaviour. That is the theoretical form of our structural argument. Where that
literature treats it as a limit on explanation, we treat it as a design requirement: if the
rationale cannot be recovered, it must be recorded at the moment it exists.

**Observability stacks.** OpenTelemetry's GenAI conventions, OpenLLMetry, OpenInference,
Langfuse and LangSmith have effectively solved automatic collection of the *what*. None models
W3C PROV, and none captures decision rationale — they are built for debugging and cost
attribution, which are different problems. **Our position is to ride that layer rather than
compete with it** (§5).

**Agentic design patterns.** The planning/tool-use/reflection vocabulary of the applied agent
literature supplies our six-step lifecycle. We add nothing to it; we instrument it.

---

## 4. The `yp:` vocabulary

Published as an OWL ontology (`vocab/yprov4agents.ttl`, 81 terms) with SHACL shapes
(`vocab/shapes.ttl`, 14 named constraints). Two rules governed its contents. **Only terms the
system emits**: the inventory was taken from frozen archives of actual runs rather than from
design documents, and a test enforces it — every `yp:` term in an emitted record must be
defined, and terms the library offers but the demonstrator does not exercise are enumerated
explicitly rather than left for a reader to discover. **Standard terms wherever one exists**:
all six lineage relations are PROV's, and `yp:` adds exactly two things PROV has no term for —
the typed steps of a decision lifecycle, and the reasoning behind a decision.

Ten activity classes specialise `prov:Activity` under `yp:AgentLifecycleActivity`, including
`ToolCall`, **`Decision`**, and two governance subclasses. Four entity classes specialise
`prov:Entity`, including `QualityEntity` — the outcome of a check, made an entity rather than a
flag so a decision can declare it was *derived from* the finding it rests on. Typing is what
lets a consumer ask "show me every decision in this run" without knowing how any agent was
written.

### 4.1 `yp:justification`

One `owl:DatatypeProperty`, with `rdfs:domain yp:DecisionActivity`. Alongside it sit
`yp:verdict`, `yp:confidence_score`, and `yp:escalation_reason` — which of several independent
conditions caused an escalation, a separation §7.5 shows earning its place.

The asymmetry between verdict and justification is worth stating, because our own measurements
surfaced it. A verdict is a short token from a fixed vocabulary, and a determined observer can
sometimes recover one: our baseline recovers 24 of 24 by scraping the labels developers wrote
them into. A justification is prose composed at the moment of decision out of evidence the step
never returned to anyone, and no labelling convention exposes it. **The conclusion is sometimes
recoverable; the reasoning is not.**

### 4.2 Shapes: declaring the standard

A compliance agent that enforces a structural standard in hand-written predicates has made that
standard a property of one implementation. `vocab/shapes.ttl` declares it instead: every
`yp:DecisionActivity` carries a non-empty `yp:justification` and a `yp:verdict`; every lifecycle
activity is dated, ended and attributed; every `yp:pid` is accompanied by a `sha256:` hash;
every record declares its own granularity and persistence.

Three consequences follow, and the third paid. A consumer can validate a record with any SHACL
engine without running our agent. The checks acquire names a reader can cite. And the agent
becomes testable *against* the declaration — which immediately found them disagreeing: the
compliance agent tested `if not activity.get("yp:justification")`, so pure whitespace passed,
while the shape's `sh:pattern "\S"` rejects it. Three implementations of one rule, and one
disagreed. **A rule written once in one place cannot disagree with itself.**

The shapes are themselves tested for falsifiability: eight mutations of a conforming record each
produce the objection the corresponding shape exists to produce. A shape suite that passes
everything reports perfect conformance over an empty check.

---

## 5. Instrumentation: two layers

> **Figure 1.** The two layers: what an external observer can collect automatically, and what it
> structurally cannot.

Behavioural provenance splits cleanly in two, and the split follows from what an observer can
see rather than from an effort budget. **Layer 1, what happened and when**: activities, timings,
tool and model calls, token counts, dataflow. A framework callback receives run trees with
inputs, outputs and parents, from which all of it follows mechanically — *this layer can be
automated, and the ecosystem has already automated it.* **Layer 2, why it happened**: the
verdict, the confidence, the justification. *This layer cannot be automated*, for the reason in
§1.

Our position follows: **consume Layer 1 where it exists, own Layer 2.** The vocabulary is
framework-independent and only a thin adapter binds the lifecycle to a framework's events; the
demonstrator is the bare-daemon case, with no agent framework at all, which is the strongest
available evidence that the library is not welded to one runtime.

### 5.1 The API

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

The wrappers supply exactly what is observable: type, label, start instant, measured duration,
attribution, sequence position, and the failure case — a step that raises is recorded as having
run and failed, because a gap in a record is indistinguishable from a step that never ran.

What must be declared is what is not observable. A wrapper sees that a function ran; it does not
see which of the values in scope that function treated as its input. Lineage is therefore stated
through the scope and never inferred. We learned this the hard way: an earlier tracker inferred
derivations from registration order, producing 74 confident and wrong edges out of 250.
`wasInformedBy` is the one relation that *is* automatic, because PROV defines it as derivable.

**`decide()` raises on a blank justification.** Every other field can be filled in by machinery;
this one cannot, so the API refuses to let a caller skip it — at the call site, where the
reasoning still exists as a value. This has a consequence we did not anticipate (§7.8).

**Timing is measured, not merely stamped.** PROV records an activity as two instants, which
invites reading both from the wall clock. That loses short steps: our development host's clock
advances in steps of roughly 15.6 ms, so two readings either side of a sub-millisecond operation
return the *same* value. Before we corrected this, **96 of 96 activities recorded a zero
duration** — a timeline from which nothing about where a run spent its time can be recovered.
The start instant now comes from the wall clock, where it must be to compare across processes;
the duration comes from a monotonic counter.

---

## 6. The demonstrator

A message bus, a producer replaying real yProv4ML records, four operational agents that do work
and emit typed PROV about themselves, and two meta agents that consume *that* and emit PROV in
turn. There is **no agent framework**; the agents are bare asyncio daemons.

| | Agent | What it does | Kind |
|---|---|---|---|
| CA1 | StorageAgent | hashes, stores, mints a `yp:pid`, verifies the round trip | deterministic |
| CAx | NL_Summarizer | summarises a graph in natural language | LLM, computed fallback |
| CA2 | Metrics_Visualizer | renders a chart from the graph's measurements | deterministic |
| CA3 | Analytics_Agent | five algorithms over any PROV graph | heuristic |
| MC1 | ComplianceChecker | enforces the declared shapes over other agents' records | deterministic |
| MC3 | AuditabilityAgent | reconstructs a who-did-what-when trail | deterministic |

All six are domain-agnostic: every computation derives from PROV itself — types, roles,
timestamps, relations — never from an ML-specific schema. The operational agents instantiate all
six lifecycle steps; the meta agents instantiate a three-step subset (receive, act, decide),
because they neither plan an approach nor answer a caller. We therefore phrase C3 as **a template
with declared subsets**, not as uniform instantiation.

Two design decisions are visible in the provenance, and worth stating because the record is what
makes them checkable. The store sits *behind* the persist agent rather than on the bus: CA1 writes
through the store's own API, and the bus carries a storage receipt — a `yp:pid` and a content hash
— never a payload. And persistence happens *after* consumption: CA1 holds each record, counts
acknowledgements from three named consumers, and persists at quorum, with a timeout guard so a
crashed subscriber cannot lose a record. The agent must say which path it took — `ACCEPT` at
quorum, `ESCALATE` on timeout — with `yp:escalation_reason` separating a *consumption-unconfirmed*
escalation from an *integrity* one. Those demand opposite operator responses: one says a consumer
is down, the other that the stored bytes are wrong.

### 6.1 Reflexivity, and the depth we claim

Meta agents subscribe to `agent.lifecycle.>` and publish to `governance.<role>.<session>`. Nothing
subscribes to `governance.*`, so the governance layer cannot re-trigger itself and **reflexivity
in a run is one level deep**: the operational agents are governed; the governance layer is not.

That bound is a termination property rather than an oversight. An agent that governs governance
output emits a record which is itself governance output, and nothing in the subject structure
stops the next pass; publishing to a prefix nobody subscribes to makes the recursion impossible by
construction, which is worth more than a depth counter someone can forget to check. It is enforced
as a test rather than documented as a caveat. What holds at that depth is stronger than a
governance table: the meta agents are fully instrumented, and **MC1 satisfies its own standard**
(§7.6).

One container image serves all six roles and is rootless-friendly, so it converts cleanly to
Apptainer for HPC. A second path runs the same agents over an in-process bus; both emit the same
provenance, and a CI gate checks that on every commit. They are not the same evidence — the
in-process bus awaits each handler inline in subscription order, so none of the distributed path's
concurrency is exercised. We use it for isolation in measurement, never as evidence about ordering
or throughput.

---

## 7. Evaluation

Every figure is regenerated from frozen archives under `experiments/`, each with per-file hashes,
by `uv run python run_local.py && uv run python -m eval.evaluate`; none is transcribed by hand. A
CI matrix (Linux and Windows, Python 3.11 and 3.12) re-runs the pipeline and fails if the emitted
provenance changes without the change being declared; 180 tests pass. The reference run is two
input records through six agents, producing **24 PROV documents**.

### 7.1 Conformance, the precondition for every other claim

Interoperability with W3C PROV is an explicit contribution, so it is judged by an independent
implementation — the reference `prov` library — not by our own code.

| | before | after |
|---|--:|--:|
| Documents accepted by the reference PROV-JSON parser | **0 of 24** | **24 of 24** |
| `yp:pid` pointers dereferenceable by a fresh process | **0 of 8** | **8 of 8** |

A single non-standard key at the document root made a conforming parser reject every file whole:
the interoperability claim had been *inverted*, not merely weak. We report the figure and name the
judge, because that is the difference between a standards claim and an assertion.

### 7.2 Structural coverage

The run produces 96 activities and 324 relations — and the total is the wrong number to quote.
By what established them: **126 observed** (a step said it read or wrote this), **72 derived**
(following from PROV's own definition of `wasInformedBy`), **30 declared** (a step asserted this
derivation), and **96 attributed**, one per activity. That last category counts how many steps ran
rather than how they were connected, so **the structural claim is 228, not 324.** Quoting the
total invites precisely that objection; the split forecloses it.

### 7.3 Conformance to a declared standard

Two measurements are easy to confuse, and only the second is a result. **Coverage: 24 of 24
decisions carry a justification** — a conformance check on our own instrumentation. The code that
counts decision activities is the code that justifies them, so no run of this system could report
another number, and the figure says nothing about whether a justification is *informative*.
**Conformance: 24 of 24 records satisfy the published shapes** — and this has an external judge,
`pyshacl` over RDF converted by the reference `prov` library. Neither the rule nor the engine is
ours. Declaring the standard is also what found the whitespace defect in the agent enforcing it
(§4.2).

CA3, applied to its own input rather than to our output, found **real completeness gaps in
yProv4ML's published records**: 8 of 9 activities declare a start time with no end time, and
entity typing coverage is 82.1%. These are *completeness* findings rather than anomalies — they
describe what the producer recorded, not how the run behaved.

### 7.4 Capture, not retrieve

> **Figure 3.** Strategy A against Strategy B, signal by signal: structure recovered in full, and
> the one row where the two diverge.

Strategy A is a callback-level baseline we built ourselves, fed the 96 step events a framework
callback handler receives — node name, timings, input and output payloads, errors — from the *same
run* as Strategy B.

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

***A is an upper bound, deliberately.*** Its event stream is built from the declarations the
explicit instrumentation made. A real handler receives a node's whole input payload and must guess
which part mattered, so it would over-report `used`. Conceding A every structural fact B declared
turns the last row into a statement about the **ceiling**: even granted everything explicit
instrumentation declares, the rationale is unreachable.

***Structure is recoverable, and the baseline recovers all of it*** — 96 activities and all 324
relations, identical to B. An earlier version of this table claimed four zeros down the A column
and three were false, including the claim that a callback reconstruction produces no conformant
PROV-JSON. Syntax was never the differentiator.

***Types and identity are where the structural gap is.*** A types nothing with no configuration
and everything with ten hand-written label patterns — so the types exist, but as a per-system
artifact someone maintains, and only because these labels happen to be descriptive. A is handed
payloads rather than references, so it mints identity from content, types none of the 54 entities,
and carries no pointer at all.

***The verdict is often recoverable; the reasoning never is.*** A recovers 24 of 24 verdicts,
which we report as fragile rather than impressive: it scrapes a developer-chosen node label, and a
graph whose decision node is named `decide` yields none. The justifications are recoverable under
no labelling convention at all — searching everything the handler could read, **222 strings and
12,425 characters**, finds **0 of 24**. **This is the single earned zero**, and it is worth more
than the four unearned ones it replaced.

### 7.5 The same decision, under both strategies

> **Figure 2.** One verdict, two causes: the quorum path and the timeout path through the same
> code, with both justifications quoted as the record carries them.

CA1's justification differs by what happened, in the same code path:

> `example_2` — **ACCEPT**, trigger `quorum`, 3 of 3: *"Persisted at quorum … acknowledged by
> [ca2, ca3, cax], the full set required."*
>
> `example_0` — **ESCALATE**, trigger `timeout`, 2 of 3: *"Persisted WITHOUT confirmed consumption
> … the persist-after-consume ordering was not satisfied."*

Strategy A recovers one fact about either decision: the verdict. Strategy B records the trigger,
the quorum reached, the escalation reason, the integrity check that *passed*, and the choice to
archive anyway rather than lose the record. Because CA1 escalates for two independent reasons
calling for opposite responses, A cannot distinguish *"storage is corrupting records"* from *"ca3
is not running"*. The structural comparison says the rationale is absent; this says what being
absent costs.

### 7.6 Reflexivity, and self-application

All four operational agents are governed by both meta agents, which emit their own typed PROV. The
sharper result is **self-application**: applying MC1 to MC1's own output **failed** when first run
— the checker could not satisfy its own rule. It now passes, as does MC1 applied to MC3, and both
are tests in the suite rather than claims in prose.

### 7.7 What the instrumentation costs

Absolute timings are machine-specific — a four-core laptop, CPython 3.11, with other processes on
it. The transferable results are the ratios, the byte counts and the complexity class. A difference
is reported as a result only if it reaches **3× the dispersion of its own samples**.

**The headline is better than a percentage: the instrumentation is cheaper than the noise floor of
the work it instruments.** At 30 interleaved repetitions, four of six agents show no resolvable
overhead — the difference between running them with and without provenance is smaller than the
difference between two runs of either.

**The field this paper argues for costs nothing we can resolve in time, and 278 bytes in storage.**
Over 40 interleaved rounds of 1,000 calls, the paired difference between recording an activity with
and without a justification is **26 ns against a round-to-round spread of ±352 ns**. The same field
is 278 bytes, **21.7% of a record**. The trade is **storage for explainability, not speed for
explainability** — more defensible than "it is free", and what the data supports. It survived being
made harder: the justifications were rewritten mid-project to state the results of real checks
rather than restating their own instruction, so this is not a measurement of string concatenation.

> **Figure 4.** Where the overhead actually is: each agent's own work, building the record, and
> writing it, as shares of one end-to-end record.

**Overhead is resolvable only for the two agents whose own work is trivial, and there it is large.**
We lead with the worst case:

| | MC1 ComplianceChecker, the worst case in the system |
|---|--:|
| the agent's own work | 96 µs |
| …plus building a complete typed PROV record | **+42 µs** |
| …plus writing it to disk | **+1,532 µs** |
| total overhead | **+1,645%** (margin 8.2×) |

Read the decomposition before believing the ratio: **97% of that overhead is one file write.**
Explicit instrumentation is not expensive to *compute*; persisting its output is, and persistence
is a deployment decision. Both knobs are exposed and measured: transient mode is **−91% of the
time cost** for a byte-identical record, and metadata granularity **−37.6% of the bytes** while
retaining every justification. A mode that shed justifications would be a different product, not a
cheaper one.

Two further results transfer. **The dominant per-step cost is rendering an instant as text** — a
tracked step is 4.49 µs, of which 2.88 µs is the timestamp, and 3× more of that is formatting the
value than reading the clock; a property of what PROV stores rather than of how it is built. And
**the cost is linear, but was not when first measured**: building a record fits an exponent of
1.066, where the first sweep fitted 1.468 because a duplicate check on declared derivations scanned
a list, and a 10,000-activity record went from 1.72 s to 88 ms once that became an O(1) set. The
defect was invisible at the six activities every test and demo uses, and would have reached this
paper as an unqualified linear claim; a second, an uncached metadata lookup making serialisation
306× slower than needed, was found the same way. Both are the argument for measuring before
publishing a performance claim.

**Which figures transfer.** Run twice on separate occasions, absolute timings moved by up to 26%,
byte counts were identical to the byte, exponents agreed within 3%, and the resolvability verdicts
were unchanged for every agent; a third run under roughly 3× the load changed **no verdict**.
**Quote the bytes, the complexity class and the verdicts; quote the timings as orders of
magnitude.** The criterion is also stricter than the one we began with, and the correction changed
two published rows. It started as *larger than the noise*, which treats any consistent bias as a
finding, and a loaded machine supplies plenty. The project's own guard test caught it: on a shared
CI runner, timing the **same function twice** produced a 147.5 ns difference against 61.0 ns of
dispersion and called it real. Counterbalancing the pair order cancels a fixed-order bias instead
of accumulating it, and a 3× margin is now required. **Write the guard test that fails when your
harness reports noise as a result.**

Three confounds. The pipeline figure is inside the noise, dominated by a fixed persist window and
process start-up, and is sequential, so it is not a throughput measurement. CA2's own work is
matplotlib rendering, three orders of magnitude larger than its instrumentation, so its overhead is
unresolvable — reported as such rather than as the 5% *speed-up* an earlier harness produced. And
condition A is indistinguishable from OFF: the callback layer is cheap, and what it cannot do is
not a matter of cost.

### 7.8 Verdict reachability

A verdict that only ever takes one value carries no information, so the tally is the less
interesting half; what has to be established is that the alternatives are **reachable**. The
ordinary run reaches six of twelve. The other six come from seven injected faults
(`experiments/verdict-reachability`): a store that writes bytes it did not hash, and a timeout with
no acknowledgement, for CA1's two escalation paths; a third-party record whose decision carries no
justification, and one with an activity started and never closed, for MC1's FAIL and MC3's PARTIAL;
and three inputs that are not faults at all. **Twelve of twelve verdicts observed.**

Those last three matter. CA2's ACCEPT, CAx's REVISE and CA3's OK are unreachable from the bundled
sample data, because the real yProv4ML records carry no numeric values to plot and declare start
times without end times. So *"CA2 always says REVISE"* was a finding about the **input**, not about
CA2, and separating those two is what these scenarios are for. It also sharpens CA3: ANOMALY is
informative precisely because OK is reachable.

One row is a property rather than a result. **`mc1:FAIL` is unreachable from records this library
produces**, because `decide()` refuses a blank justification at the call site. FAIL is reachable for
records the library did not write — exactly the case a compliance checker exists for, since it is
pointed at agents you do not control. *The instrumentation makes the violation unrepresentable, and
the governance layer catches it in input that was not instrumented.*

Every fault is injected into the input or the environment, **never into the check being tested**,
and the suite enforces all seven scenarios plus the matrix itself — so a verdict advertised and
never observed fails the build.

---

## 8. Discussion and limitations

**We measure that justifications exist and are externally unrecoverable; we do not measure whether
they are *good*.** This is the most significant limitation of the evaluation. §7.3's coverage figure
is a self-report by construction, and §7.4 establishes unrecoverability, not quality. Whether an
operator handed one of these justifications acts correctly more often is an empirical question
requiring a human study, and we have not run one. §7.5 is an argument, not evidence.

**Reflexivity is one level deep.** The governance layer observes and judges the operational layer
and is not itself consumed; self-application is demonstrated as a property rather than as a running
loop, and C2 carries that qualifier.

**A provenance system can over-report as easily as under-report.** An earlier version of our tracker
asserted derivation from registration order and informed-by from recording order, producing exactly
*n*−1 edges for *n* items regardless of what the agent did. The relations were corrected to
declared-or-derived, and the correction changed the graph's *topology* rather than only its size:
the old chain asserted a single dependency spine where the real dataflow is a tree. We disclose this
because it is what makes the counts in §7.2 trustworthy, and because **relation counts are the
easiest place in a provenance system for over-reporting to go unnoticed.**

**A model-bearing step is repeatable from its record, not bitwise reproducible.** Every model-bearing
step carries the model, sampling parameters, prompt hash, token counts and finish reason — what an
auditor needs to re-run the call, and what catches a forgotten version bump. It cannot make a hosted
model deterministic: a provider may change the weights behind a model name. The record makes the
apparatus explicit; it does not make the experiment closed.

**Energy is recorded at session granularity, not per step.** A sampling backend cannot attribute
energy to a step shorter than its period: codecarbon's default is 15 seconds against millisecond
steps, so a delta lands on whichever step reads after a tick — in one record a 0.000 s step was
charged 46.9 µWh while a 1.328 s rendering step was charged 4.6 µWh. Each figure is published
alongside `yp:energy_sampling_period_s` and `yp:energy_attributable`, so the record states that the
number is real consumption in a real window but **not** an attribution.

**Identifiers, transport, and resource bounds.** The `21.T11999` prefix is not registered and
nothing resolves it globally, and identical payloads collapse to one identifier, so a pointer
identifies *content* rather than a storage event. NATS core is at-most-once with JetStream
unconfigured, so a subscriber not running when a record is published never sees it — a provenance
bus that can silently lose provenance deserves a sentence in a provenance paper. And a tracker holds
its whole record in memory until it emits (~1.3 KiB per activity, so 10,000 steps cost ~13 MiB
before a byte is written), which is why a metadata-only granularity exists.

**Scale, and a single producer family.** Two input records, 24 emitted documents, one producer.
Nothing in the agents is ML-specific, but that generality is argued from the code rather than
demonstrated against a second producer, and yProv4WFs is untested here.

**The baseline is our own construction.** We built Strategy A, so we could in principle have built it
badly. The mitigation is to grant it every structural fact the explicit instrumentation declares and
report it as an upper bound; the resulting table concedes A everything except the one row the paper
is about.

---

## 9. Conclusion and future work

The provenance community can describe what an agentic system did. With the automatic-capture tooling
that dominates agent observability it cannot describe why — and this is a structural property rather
than a tooling deficiency. The rationale for a decision is a local variable at the moment of
decision, and no callback, span, or post-hoc query can retrieve what was never captured.

We made that argument measurable by building the strongest callback-level baseline we could and
granting it every structural advantage: it reconstructs 96 of 96 activities, all 324 relations and 24
of 24 verdicts, serialises to conformant W3C PROV-JSON, and recovers **0 of 24 justifications** from
12,425 characters of everything it could see. We discharged the argument with a small explicit
instrumentation layer — a published `yp:` vocabulary with SHACL shapes, and an API whose decision
call refuses a blank justification — and demonstrated it across six heterogeneous agents in a running
reflexive system, including the agents that govern the others. And we measured the cost: nothing
resolvable in time, 278 bytes and 21.7% of a record in storage, with every agent that does
substantial work showing instrumentation overhead smaller than its own run-to-run variance.

The honest summary of the trade is one sentence: **storage for explainability, not speed for
explainability.**

Four extensions follow directly. **Actuation** is the largest: the meta layer today observes, judges
and gates, and making it *reconfigure* or *activate* an operational agent requires a control-channel
design with its own provenance — an action taken on the basis of a justification should be derivable
from it. **Bounded second-level reflexivity**, with the depth carried in the subject so recursion
remains impossible by construction, would lift the one-level qualifier on C2 without reintroducing
the termination problem. **A Layer-1 OpenTelemetry bridge** would let the automatic half of the
record come from instrumentation a deployment already runs, leaving only the irreducible explicit
part to the developer. And **a justification-quality study** is the missing evaluation: we have shown
that reasoning can only be captured and never retrieved, and not yet that capturing it makes an
operator's decisions better.

**Artifact availability.** *(To be completed: the library, the vocabulary and SHACL shapes, the frozen
experiment archives and the benchmark harness are all in the project repository; a citable archive is
pending a decision on public release.)*
