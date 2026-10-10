# Capture, Not Retrieve: Behavioural Provenance for Agentic AI

> **6,275 words, 3 figures, 5 tables** — measured with `wc -w` over this file, not estimated;
> 6,004 from §1 onwards. Four earlier versions of this line stated a target I was aiming at
> instead of the file's actual length; the number above is the only kind worth printing. The
> post-audit rewrite added ~610 words, mostly Table 3 and the fact-level argument it carries. Cut from the 7,900-word full draft at
> commit `53c5b61`, which remains the reservoir for anything a reviewer asks to see expanded.
>
> **Whether this fits 6 IEEE pages is not yet known.** Word-count estimates for a two-column
> page range from ~900 to ~1,200, which spans "fits" and "a page over", so the question can
> only be settled by typesetting it. No LaTeX toolchain is installed here; see
> `paper/OUTLINE.md` for the ranked cuts that close a gap of up to ~1,000 words if one exists.

> Every figure quoted here is read from a frozen archive under `experiments/`, named at the
> point of use. Where a number could not be measured, the text says so rather than estimating.
> The reference archives are `experiments/audit-reference` and `experiments/audit-llm`, frozen
> from clean commits after the audit in `docs/AUDIT.md`, which narrowed the rationale result
> (§6.2) and the overhead headline (§6.5).

**Figures** (`python -m paper.figures`): Figure 1 = `f1_two_layers` ·
Figure 2 = `f4_two_escalations` · Figure 3 = `f9_where_the_overhead_is`. `f5_a_versus_b` is
rendered and held in reserve: it plots the same 30 values as Table 2, which states them
precisely in comparable space.

---

## 1. Introduction

Scientific computing infrastructure is acquiring autonomous agents. They summarise runs, select
hyperparameters, decide what to archive, and increasingly act on those decisions without a human
in the loop. The provenance community has a mature answer to *what* such a system did: W3C PROV
describes activities, entities and the lineage between them, and tooling to capture that lineage
automatically is now routine.

It has almost no answer to *why*.

When a storage agent declines to archive a record, when a summariser rejects its own output, when
a compliance agent fails a colleague's record — an operator, an auditor, and increasingly a
regulator need the reason, not the outcome. The EU AI Act's transparency obligations are written
in the language of explanation rather than of logging. A record that says a decision was taken,
and when, and by which component, and still cannot say why, does not meet that bar.

Our claim is that this gap is **structural rather than a matter of insufficient tooling**:

> The rationale for a decision is a local variable at the moment of decision. A callback fires
> after the function returns; a span wraps the call; a query reads the store afterwards. Each may
> see the facts the decision used. None of them can retrieve the rule that combined those facts
> into a verdict, because that rule was never a value anything passed.

We call this **capture, not retrieve**. The corollary is uncomfortable for the
observability-first approach that dominates agent tooling: no amount of additional collection
closes the gap, because the information is not in the span. Behavioural provenance therefore has
an irreducible explicit component — instrumentation a developer must write, at the point of
decision, that no framework can supply.

**C1 — Capture, not retrieve.** A structural argument supported by an A-versus-B comparison over
the *same agents in the same run*. We build the callback-level baseline ourselves and make it as
strong as we can: it reconstructs 96 of 96 activities, all 324 relations and 24 of 24 verdicts,
and its output is accepted by the reference W3C PROV parser. It recovers no justification
verbatim, and we do not rest on that. Searched fact by fact, it saw 61% of what the eight
operational justifications cite — and none of the values the agents computed in order to decide
(§6.2).

**C2 — A reflexive multi-agent provenance architecture.** Four operational agents emit typed PROV
about themselves; two meta agents consume *that* and emit PROV in turn. Containerised, run over a
real message bus in CI, and governing its own governance layer to a depth we state exactly (§5.1).

**C3 — The `yp:` vocabulary and a six-step lifecycle**, published as an OWL ontology with SHACL
shapes, instantiated by four heterogeneous agents — deterministic, LLM-backed and hybrid — and
governed uniformly against the declared shapes (§3).

We also measure the cost, because an argument that explicit instrumentation is *necessary* is
worth little if it is expensive. Building the record, justification included, moves no operational
agent's step by more than its own run-to-run variance, on either of two occasions; the
justifications are 6.5% of the records' bytes. What costs is writing the record to disk, and that
is a deployment decision the library exposes (§6.5).

---

## 2. Background and related work

**W3C PROV** models provenance as three classes — `prov:Entity`, `prov:Activity`, `prov:Agent` —
and a small set of relations between them. Its deliberate domain-neutrality is why it says nothing
about rationale: PROV describes *what was derived from what, by whom*, and a justification is none
of those things. It is a property of an activity that no relation can express, which is why an
extension vocabulary is needed rather than a modelling convention. This work sits in the **yProv**
family, which instruments machine-learning runs (yProv4ML) and workflows (yProv4WFs) with
content-addressed storage for large payloads; our demonstrator consumes real yProv4ML records, so
the agents are exercised against provenance not written for them.

Agent frameworks differ in their abstractions but converge on a recurring shape, which we take as
the unit of instrumentation, in three phases: *ingestion and scoping* (S1 receive, S2 plan),
*execution* (S3 act — the model call, the tool call, the write), and *governance and reflexion*
(S4 observe, **S5 decide**, S6 respond). S5 is the step this paper is about, and the only one
whose content an external observer cannot reconstruct.

**PROV-AGENT** (Souza et al.) is the closest prior work: it extends PROV for agentic systems and
treats agents as first-class provenance subjects, but models data as untyped `DomainData` and
carries no decision rationale — it records a richer *what*, and the *why* remains outside the
model. Work on post-hoc reasoning reconstruction (AER; Vispute et al.) formalises a result we rely
on: **a reasoning trace cannot in general be identified from inputs and outputs alone**, because
many distinct reasoning processes are consistent with the same observable behaviour. Where that
literature treats this as a limit on explanation, we treat it as a design requirement. Meanwhile
OpenTelemetry's GenAI conventions, OpenLLMetry, Langfuse and LangSmith have effectively solved
automatic collection of the *what*; none models W3C PROV and none captures rationale, because they
are built for debugging and cost attribution. **Our position is to ride that layer rather than
compete with it.** The planning/tool-use/reflection vocabulary of the applied agent literature
supplies our lifecycle; we add nothing to it, we instrument it.

---

## 3. The `yp:` vocabulary

Published as an OWL ontology (`vocab/yprov4agents.ttl`, 86 terms) with SHACL shapes
(`vocab/shapes.ttl`, 16 named constraints). Two rules governed its contents. **Only terms the
system emits**: the inventory was taken from frozen archives of actual runs rather than from design
documents, and a test enforces it in both directions, with terms the library offers but the demonstrator
does not exercise enumerated explicitly rather than left for a reader to discover. **Standard terms wherever
one exists**: all six lineage relations are PROV's, and `yp:` adds exactly two things PROV has no
term for — the typed steps of a decision lifecycle, and the reasoning behind a decision. Eleven
lifecycle activity classes specialise `prov:Activity`, including **`Decision`** and two governance
subclasses; five entity classes specialise `prov:Entity`, including `QualityEntity`, the outcome of
a check made an entity rather than a flag so a decision can declare it was *derived from* the
finding it rests on.

**`yp:justification`** is one `owl:DatatypeProperty` with `rdfs:domain yp:DecisionActivity`.
Alongside it sit `yp:verdict`, `yp:confidence_score`, and `yp:escalation_reason`. The asymmetry
between verdict and justification is worth stating, because our own measurements surfaced it. A
verdict is a short token from a fixed vocabulary, and a determined observer can sometimes recover
one: our baseline recovers 24 of 24 by scraping the labels developers wrote them into. A
justification combines evidence by a rule the step never returned to anyone, and no labelling
convention exposes the rule. **The conclusion is sometimes recoverable, and often the facts are;
the rule that joined them is not.**

**Shapes: declaring the standard.** A compliance agent that enforces a structural standard in
hand-written predicates has made that standard a property of one implementation. `vocab/shapes.ttl`
declares it instead: every `yp:DecisionActivity` carries a non-empty justification and a verdict;
every lifecycle activity is dated, ended and attributed; every `yp:pid` is accompanied by a
`sha256:` hash; every record declares its granularity and persistence; and every agent decides
something — a constraint we lacked until deleting a record's decision left it conforming, since a
shape targeting a class is silent when no node of the class exists. A consumer can validate with any
SHACL engine without running our agent, and the agent's runtime check, a second implementation of
the standard, is tested *against* the declaration. That test has found them disagreeing on
whitespace justifications (plain, and as PROV-JSON typed literals), untyped or mistyped steps, and
the deleted decision. **The standard is declared once, and each implementation is tested against
it rather than trusted to match it.** One difference is deliberate and pinned: the agent requires
a decision to state its type, which the shapes infer from `rdfs:domain`. The shapes are themselves tested for falsifiability: twelve
mutations of a conforming record each produce the objection the corresponding shape exists to
produce.

---

## 4. Instrumentation: two layers

> **Figure 1.** The two layers: what an external observer can collect automatically, and what it
> structurally cannot.

Behavioural provenance splits cleanly in two, and the split follows from what an observer can see
rather than from an effort budget. **Layer 1, what happened and when**: activities, timings, tool
and model calls, token counts, dataflow — a framework callback receives run trees with inputs,
outputs and parents, from which all of it follows mechanically, so *this layer can be automated and
the ecosystem has already automated it.* **Layer 2, why it happened**: the verdict, the confidence,
the justification. *This layer cannot be automated*, for the reason in §1. Our position follows:
**consume Layer 1 where it exists, own Layer 2.** The vocabulary is framework-independent and only a
thin adapter binds the lifecycle to a framework's events; the demonstrator is the bare-daemon case,
with no agent framework at all, which is the strongest available evidence that the library is not
welded to one runtime.

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
attribution, sequence position, and the failure case — a step that raises is recorded as having run
and failed, because a gap in a record is indistinguishable from a step that never ran. What must be
declared is what is not observable. A wrapper sees that a function ran; it does not see which of the
values in scope that function treated as its input, so lineage is stated through the scope and never
inferred. We learned this the hard way: an earlier tracker inferred derivations from registration
order, producing 74 confident and wrong edges out of 250. `wasInformedBy` is the one relation that
*is* automatic, because PROV defines it as derivable.

**`decide()` raises on a blank justification.** Every other field can be filled in by machinery;
this one cannot, so the API refuses to let a caller skip it — at the call site, where the reasoning
still exists as a value. This has a consequence we did not anticipate (§6.6).

**Timing is measured, not merely stamped.** PROV records an activity as two instants, which invites
reading both from the wall clock. That loses short steps: our host's clock advances in steps of
roughly 15.6 ms, so two readings either side of a sub-millisecond operation return the *same* value.
Before we corrected this, **96 of 96 activities recorded a zero duration** — a timeline from which
nothing about where a run spent its time can be recovered. The start instant now comes from the wall
clock, where it must be to compare across processes; the duration from a monotonic counter.

---

## 5. The demonstrator

A message bus, a producer replaying real yProv4ML records, four operational agents that do work and
emit typed PROV about themselves, and two meta agents that consume *that* and emit PROV in turn.
There is **no agent framework**; the agents are bare asyncio daemons.

| | Agent | What it does | Kind |
|---|---|---|---|
| CA1 | StorageAgent | hashes, stores, mints a `yp:pid`, verifies the round trip | deterministic |
| CAx | NL_Summarizer | summarises a graph in natural language | LLM, computed fallback |
| CA2 | Metrics_Visualizer | renders a chart from the graph's measurements | deterministic |
| CA3 | Analytics_Agent | five algorithms over any PROV graph | heuristic |
| MC1 | ComplianceChecker | enforces the declared shapes over other agents' records | deterministic |
| MC3 | AuditabilityAgent | reconstructs a who-did-what-when trail | deterministic |

**Table 1.** The six agents. All are domain-agnostic: every computation derives from PROV itself —
types, roles, timestamps, relations — never from an ML-specific schema.

The operational agents instantiate all six lifecycle steps; the meta agents instantiate a three-step
subset, because they neither plan an approach nor answer a caller. We therefore phrase C3 as **a
template with declared subsets**, not as uniform instantiation.

Two design decisions are visible in the provenance, and worth stating because the record is what
makes them checkable. The store sits *behind* the persist agent rather than on the bus: CA1 writes
through the store's own API, and the bus carries a storage receipt — a `yp:pid` and a content hash —
never a payload. And persistence happens *after* consumption: CA1 holds each record, counts
acknowledgements from three named consumers, and persists at quorum, with a timeout guard so a
crashed subscriber cannot lose a record. The agent must say which path it took, and
`yp:escalation_reason` separates three: *integrity*, *incomplete-input* and
*consumption-unconfirmed* — conditions that demand different operator responses. Every envelope
on the bus is numbered, and an agent that finds a gap records an `INCOMPLETE` decision naming the
missing envelopes rather than a well-formed record of a truncated input.

### 5.1 Reflexivity, and the depth we claim

Meta agents subscribe to `agent.lifecycle.>` and publish to `governance.<role>.<session>`. Nothing
subscribes to `governance.*`, so the governance layer cannot re-trigger itself and **reflexivity in a
run is one level deep**: the operational agents are governed; the governance layer is not.

That bound is a termination property rather than an oversight — an agent that governs governance
output emits a record which is itself governance output, and nothing in the subject structure stops
the next pass. Publishing to a prefix nobody subscribes to makes the recursion impossible by
construction, which is worth more than a depth counter someone can forget to check, and it is
enforced as a test rather than documented as a caveat. Two things hold at that depth, both stronger
than a governance table: the meta agents are fully instrumented, and **MC1 satisfies its own
standard** — applying MC1 to MC1's own output **failed** when first run, because the checker could
not satisfy its own rule. It now passes, as does MC1 applied to MC3.

One container image serves all six roles and is rootless-friendly, so it converts cleanly to
Apptainer for HPC. A second path runs the same agents over an in-process bus; CI runs both on every
commit and holds each to the same structural comparison against the reference archive. They are not the same evidence — the
in-process bus awaits each handler inline in subscription order, exercising none of the distributed
path's concurrency — so we use it for isolation in measurement, never as evidence about ordering or
throughput.

---

## 6. Evaluation

Every figure is regenerated from frozen archives under `experiments/` by
`uv run python run_local.py && uv run python -m eval.evaluate`; none is transcribed by hand. Each
archive carries per-file hashes and the commit it was frozen from, and refuses to freeze from a
working tree with uncommitted changes. A CI matrix (Linux and Windows, Python 3.11 and 3.12) re-runs
the pipeline and fails if any lineage edge of the emitted provenance changes without the change
being declared; 239 tests pass. The reference run (`experiments/audit-reference`) is two input
records through six agents, producing **24 PROV documents**.

### 6.1 Conformance, coverage, and a declared standard

Interoperability with W3C PROV is judged by an independent implementation. A single non-standard key
at the document root made the reference `prov` parser reject **every one of 24 documents** whole —
the interoperability claim had been *inverted*, not merely weak — and after the fix all 24 are
accepted and all 8 `yp:pid` pointers dereference from a fresh process. The run produces 96 activities
and 324 relations, and the total is the wrong number to quote: **126 observed**, **72 derived** from
PROV's definition of `wasInformedBy`, **30 declared**, and **96 attributed**, one per activity. That
last category counts how many steps ran rather than how they were connected, so **the structural
claim is 228, not 324.**

Two further measurements are easy to confuse, and only the second is a result. **Coverage: 24 of 24
decisions carry a justification** — a conformance check on our own instrumentation, since the code
that counts decision activities is the code that justifies them, so no run could report another
number and the figure says nothing about whether a justification is *informative*. **Conformance: 24
of 24 records satisfy the published shapes**, including the constraint that each agent decided
something — and this has an external judge, `pyshacl` over RDF converted by the reference `prov`
library. Neither the rule nor the engine is ours.

Applied to its own input rather than to our output, CA3 found **real completeness gaps in yProv4ML's
published records**: 8 of 9 activities declare a start time with no end time, and entity typing
coverage is 82.1%.

### 6.2 Capture, not retrieve

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

**Table 2.** Strategy A against Strategy B, signal by signal.

***A is an upper bound, deliberately.*** Its event stream is built from the declarations the explicit
instrumentation made. A real handler receives a node's whole input payload and must guess which part
mattered, so it would over-report `used`. Conceding A every structural fact B declared turns the last
row into a statement about the **ceiling**: even granted everything explicit instrumentation
declares, the rationale is unreachable.

***Structure is recoverable, and the baseline recovers all of it*** — 96 activities and all 324
relations, identical to B. An earlier version of this table claimed four zeros down the A column and
three were false, including the claim that a callback reconstruction produces no conformant
PROV-JSON. Syntax was never the differentiator.

***Types and identity are where the structural gap is.*** A types nothing with no configuration and
everything with ten hand-written label patterns, so the types exist as a per-system artifact someone
maintains, and only because these labels happen to be descriptive. A is handed payloads rather than
references, so it mints identity from content, types none of the 54 entities, and carries no pointer.

***The verdict is often recoverable.*** A recovers 24 of 24 verdicts, which we report as fragile
rather than impressive: it scrapes a developer-chosen node label, and a graph whose decision node is
named `decide` yields none.

***The facts are often recoverable; the rule is not.*** Searching everything the handler could
read, 222 strings and 12,425 characters, finds no justification verbatim. We report that and do not
rest on it, because a sentence assembled at run time almost never survives intact anywhere. The
harder test splits each justification into the facts it cites (figures, hashes, names, files,
agent roles) and searches for each as a whole token:

| Decisions | Count | Verbatim | Facts cited | Seen by A | Every fact seen |
|---|--:|--:|--:|--:|--:|
| Operational (CA1, CA2, CA3, CAx) | 8 | 0 | 49 | 30 (61%) | 2 |
| Meta (MC1, MC3) | 16 | 0 | 16 | 16 (100%) | 16 |

**Table 3.** The rationale, fact by fact (`python -m eval.baseline` over the reference archive).
Seeing a fact over-counts knowing it: a role name in the stream is not knowledge that the role
acknowledged.

The 19 facts A never saw are those the agents *computed in order to decide*: a record's byte count,
a timing gap and its share of the run, a summary's length, the threshold its figures were checked
against. Where A saw every fact, it still lacks the rule: CA2 cites only figures and a file name A
observed, but not that finding no numeric `prov:value` sent it to a fallback chart, nor that the
fallback is why it said REVISE. The meta-agent justifications restate structure A reconstructs in
full, so they do not count toward the claim. **The inputs to a decision may be observable; the rule
that combined them is not.**

> **Figure 2.** One verdict, two causes: the integrity path and the timeout path through the same
> code, with both justifications quoted as the record carries them.

What the gap costs is visible in one agent (Figure 2). CA1 says ESCALATE for three reasons, and its
justification differs by which one held. On an integrity failure it cites the two content hashes
that did not match; on a timeout, *"Persisted WITHOUT confirmed consumption … still waiting on [ca2,
ca3, cax] when the record was archived anyway to avoid losing it"*; on a lost envelope, *"Envelope(s)
3 of the record never arrived, so what was stored is partial."* A recovers one fact about each
decision, the verdict. B records the trigger, the quorum, the reason, the integrity check that
*passed* on the timeout path, and the choice to archive anyway rather than lose the record. So A
cannot tell *"storage is corrupting records"* from *"ca3 is not running"*, and the two call for
opposite responses.

### 6.3 Energy: a negative result, and what a record must disclose

Energy is a first-class concern for the infrastructure this work targets, so we wired a sampling
backend into the capture API. The wiring produced a result worth reporting on its own terms.

**A sampling backend cannot attribute energy to a step shorter than its sampling period.**
codecarbon's default period is 15 seconds and these steps last milliseconds, so a delta lands on
whichever step happens to read after a tick:

| Step | Energy charged | Step duration |
|---|--:|--:|
| choose what to plot | 46.9 µWh | 0.000 s |
| render chart (matplotlib) | 4.6 µWh | 1.328 s |

**Table 4.** Two steps of one record under a 15-second sampling period. The step that did the work
was charged a tenth of what the step that did nothing was charged.

A second artifact was worse: the *first* step of each record was charged 28.3 seconds of
inter-message idle time, because the energy tracker outlives any one record. Both are now handled —
the baseline resets when a record begins, so no step is charged for the gap before it — but the
substantive contribution is what the record says about its own figure. Each measurement is published
alongside `yp:energy_sampling_period_s` and `yp:energy_attributable`, so the record states that the
number is real consumption in a real window and **not** an attribution to the step it is attached to.
**Energy belongs at session granularity**, and any per-step energy claim in this literature needs a
backend sampling faster than the steps and should state its period. *A provenance record that reports
an energy figure without its sampling period is not reporting a measurement; it is reporting an
artifact of when the sampler last ticked.*

### 6.4 Model provenance

Before this work the graph named no model, no sampling parameters, no prompt and no token counts, so
the one step in the pipeline that was not deterministic was the one step the record could not account
for. Every model-bearing step now carries the model, temperature, token limit, prompt identity,
version and content hash, token counts, latency, finish reason, and a hash of the response
(`experiments/audit-llm`: four calls, 2,071 prompt and 480 completion tokens). Three details
generalise. *The model a caller names is not the model that answers*: the record carries the
requested alias `gpt-4o`, the snapshot that answered, `gpt-4o-2024-08-06`, and the provider's
`system_fingerprint` — and the four calls of one run returned three distinct fingerprints. *A prompt is apparatus and had to be routed like one*: a prompt registry with versions
and hashes already existed and nothing imported it, because both LLM agents built prompts as inline
f-strings; the fix was making the call site take a prompt object rather than a string, so a prompt
cannot reach a model without its identity reaching the record. And *an absence is recorded as an
absence*: the computed fallback path stamps no model attributes at all, rather than zeros, because a
zero token count is a measurement and on that path none was made.

### 6.5 What the instrumentation costs

Absolute timings are machine-specific — a four-core laptop, CPython 3.11, with other processes on it.
The transferable results are the ratios, the byte counts and the complexity class. A difference is
reported as a result only if it reaches **3× the dispersion of its own samples**.

Figures are from `experiments/audit-reference/bench`, with a second occasion in
`bench-previous`.

**Building the record is inside the noise; writing it is not always.** Each agent's step was timed
30 times per condition, interleaved, with provenance off, built but not written, and built and
written. For all four operational agents, *building* a complete typed record moved the step by
less than its own run-to-run variance on both occasions (margins 0.0–0.5×). Whether *writing* it
did depends on the agent and, for two of them, on the day.

**The field this paper argues for costs nothing we can resolve in time.** Over 40 interleaved
rounds of 1,000 calls, the paired difference between recording an activity with and without a
justification is **54 ns against a round-to-round spread of ±726 ns**. In storage it costs bytes:
240–738 per justification in the reference records, **6.5% of their serialised size**. The trade is
**storage for explainability, not speed for explainability**.

> **Figure 3.** Where the overhead actually is: each agent's own work, building the record, and
> writing it, as shares of one end-to-end record.

**Where overhead resolves, it is the write** (Figure 3). We lead with the worst case:

| | MC1 ComplianceChecker, the worst case in the system |
|---|--:|
| the agent's own work | 109 µs |
| …plus building a complete typed PROV record | **+38 µs** |
| …plus writing it to disk | **+1,485 µs** |
| total overhead | **+1,394%** (margin 11.5×) |

**Table 5.** The worst ratio in the system, decomposed. 98% of it is one file write; for every
agent whose overhead resolves, the write is at least 90%.

Read the decomposition before believing the ratio. Explicit instrumentation is not expensive to
*compute*; persisting its output is, and persistence is a deployment decision. Both knobs are exposed
and measured: transient mode is **−89% of the time cost** for a byte-identical record, and metadata
granularity **−37.6% of the bytes** while retaining every justification. A mode that shed
justifications would be a different product, not a cheaper one.

Two further results transfer. **The dominant per-step cost is rendering an instant as text** — of a
4.97 µs tracked step, 2.59 µs is the timestamp, and formatting the value costs 1.8× reading the
clock. And **the cost is linear, but was not when first measured**: the first sweep fitted an
exponent of 1.468 because a duplicate check on declared derivations scanned a list, and a
10,000-activity record went from 1.72 s to 88 ms once that became an O(1) set; the exponents are now
1.09 for building a record and 1.13 for serialising it. That defect was
invisible at the six activities every test uses and would have reached this paper as an unqualified
linear claim.

**Which figures transfer.** Across two occasions the headline timings moved by 6–11%, byte counts
were identical, and exponents agreed within 2.1%. Not every verdict held: for CA3 and CAx, building
and writing went from 2.9× the dispersion to 5.0× and 4.6×, resolved on one occasion and not the
other, which is a result in neither direction. **Quote the bytes, the complexity class, the
verdicts that held and the decomposition; quote the timings as orders of magnitude.** The 3×
criterion is stricter than the one we began with, *larger than the noise*, which treats any
consistent bias as a finding, and a loaded machine supplies plenty. The project's own guard test
caught it — on a shared CI runner, timing the **same function twice** produced a 147.5 ns difference
against 61.0 ns of dispersion and called it real. **Write the guard test that fails when your harness
reports noise as a result.**

### 6.6 Verdict reachability

A verdict that only ever takes one value carries no information, so what has to be established is that
the alternatives are **reachable**. The six agents have two verdicts each; the ordinary run reaches six
of those twelve. Nine injected scenarios (`experiments/audit-reference/faults`) reach the other six,
CA1's ESCALATE by each of its three reasons, and `INCOMPLETE`, which any stream-consuming agent
records when an envelope goes missing. **All thirteen verdicts are observed.** Three of the
scenarios are not faults at all, and that matters: CA2's ACCEPT, CAx's REVISE and CA3's OK are unreachable from the bundled sample
data because the real records carry no numeric values to plot and declare start times without end
times. So *"CA2 always says REVISE"* was a finding about the **input**, not about CA2.

One row is a property rather than a result. **`mc1:FAIL` is unreachable from records this library
produces**, because `decide()` refuses a blank justification at the call site. FAIL is reachable for
records the library did not write — exactly the case a compliance checker exists for, since it is
pointed at agents you do not control. *The instrumentation makes the violation unrepresentable, and
the governance layer catches it in input that was not instrumented.* Every fault is injected into the
input or the environment, **never into the check being tested**, so a verdict advertised and never
observed fails the build.

---

## 7. Discussion and limitations

**We measure that justifications exist and that their decision rule is externally unrecoverable;
we do not measure whether they are *good*.** This is the most significant limitation of the
evaluation. §6.1's coverage figure is a self-report by construction, and §6.2 establishes
unrecoverability, not quality. §6.2's fact-level measure is also coarse in both directions: it
counts a fact as seen wherever the same token appears, and it cannot see a rule that cites no
checkable fact at all. Whether an
operator handed one of these justifications acts correctly more often is an empirical question
requiring a human study, and we have not run one. Figure 2 is an argument, not evidence.

**Reflexivity is one level deep**, and self-application is demonstrated as a property rather than as a
running loop, so C2 carries that qualifier.

**A provenance system can over-report as easily as under-report.** An earlier version of our tracker
asserted derivation from registration order and informed-by from recording order, producing exactly
*n*−1 edges for *n* items regardless of what the agent did. The relations were corrected to
declared-or-derived, and the correction changed the graph's *topology* rather than only its size: the
old chain asserted a single dependency spine where the real dataflow is a tree. We disclose this
because it is what makes the counts in §6.1 trustworthy, and because **relation counts are the easiest
place in a provenance system for over-reporting to go unnoticed.**

**A model-bearing step is repeatable from its record, not bitwise reproducible.** The record carries
what an auditor needs to re-run a call and what catches a forgotten version bump, but it cannot make a
hosted model deterministic. Recording the resolved snapshot and fingerprint narrows the gap without
closing it: three fingerprints in four calls of one run is a backend changing underneath a caller
that changed nothing.

**Identifiers, transport, and resource bounds.** The `21.T11999` prefix is not registered and nothing
resolves it globally, and identical payloads collapse to one identifier, so a pointer identifies
*content* rather than a storage event. NATS core is at-most-once with JetStream unconfigured, so a
subscriber not running when a record is published never sees it — a provenance bus that can lose
provenance deserves a sentence in a provenance paper. Numbered envelopes turn a *partial* loss into an
`INCOMPLETE` record; a stream lost whole is still silent. And a tracker holds its whole record in
memory until it emits (~1.3 KiB per activity), which is why a metadata-only granularity exists.

**Scale, a single producer, and our own baseline.** Two input records, 24 emitted documents, one
producer; nothing in the agents is ML-specific, but that generality is argued from the code rather than
demonstrated against a second producer. And we built Strategy A, so we could in principle have built
it badly — the mitigation is to grant it every structural fact the explicit instrumentation declares
and report it as an upper bound, which is why Table 2 concedes A everything except the one row the
paper is about.

---

## 8. Conclusion and future work

The provenance community can describe what an agentic system did. With the automatic-capture tooling
that dominates agent observability it cannot describe why — and this is a structural property rather
than a tooling deficiency. The facts a decision used may cross a step boundary; the rule that combined
them is a local computation at the moment of decision, and no callback, span, or post-hoc query can
retrieve what was never captured.

We made that argument measurable by building the strongest callback-level baseline we could and
granting it every structural advantage: it reconstructs 96 of 96 activities, all 324 relations and 24
of 24 verdicts, and serialises to conformant W3C PROV-JSON. It sees 61% of the facts the operational
justifications cite, none of the values the agents computed in order to decide, and not one decision
rule. We discharged the argument with a small explicit
instrumentation layer — a published `yp:` vocabulary with SHACL shapes, and an API whose decision call
refuses a blank justification — and demonstrated it across six heterogeneous agents in a running
reflexive system, including the agents that govern the others. We measured the cost: building the record is inside every operational
agent's run-to-run variance, the justifications are 6.5% of the records' bytes, and where overhead
resolves at all, at least 90% of it is the write to disk. And we report a negative
result on energy that constrains how this community should record it at all.

The honest summary of the trade is one sentence: **storage for explainability, not speed for
explainability.**

Four extensions follow directly. **Actuation** is the largest: the meta layer today observes, judges
and gates, and making it *reconfigure* or *activate* an operational agent requires a control-channel
design with its own provenance — an action taken on the basis of a justification should be derivable
from it. **Bounded second-level reflexivity**, with the depth carried in the subject so recursion
remains impossible by construction, would lift the one-level qualifier on C2. **A Layer-1
OpenTelemetry bridge** would let the automatic half of the record come from instrumentation a
deployment already runs, leaving only the irreducible explicit part to the developer. And **a
justification-quality study** is the missing evaluation: we have shown that a decision rule can only
be captured and never retrieved, and not yet that capturing it makes an operator's decisions better.

**Artifact availability.** *(To be completed: the library, the vocabulary and SHACL shapes, the frozen
experiment archives and the benchmark harness are all in the project repository; a citable archive is
pending a decision on public release.)*
