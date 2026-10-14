# Capture, Not Retrieve: Behavioural Provenance for Agentic AI

> **Six-page version.** Condensed from `paper/DRAFT_full.md` (the long draft, kept as the
> reservoir for anything a reviewer asks to see expanded) and given a state-of-the-art section.
> Every figure is read from a frozen archive under `experiments/` — `audit-reference`,
> `audit-llm`, `energy-session` — named at the point of use. Citations are `[@key]` markers,
> resolved against the reference list in `paper/build_tex.py`.

---

## 1. Introduction

Scientific computing infrastructure is acquiring autonomous agents: they summarise runs, judge
results and decide what to archive, increasingly without a human in the loop. The provenance
community has a mature answer to *what* such a system did. W3C PROV [@provdm] describes
activities, entities and the lineage between them, and capturing that lineage automatically is
routine. It has almost no answer to *why*. When a storage agent declines to archive a record, an
operator, an auditor and — under the EU AI Act [@euaiact] — a regulator need the reason, not the
outcome.

We argue that this gap is **structural, not a matter of tooling**:

> The rationale for a decision is a local computation at the moment of decision. A callback
> fires after the function returns; a span wraps the call; a query reads the store afterwards.
> Each may see the facts the decision used. None can retrieve the rule that combined them into a
> verdict, because that rule was never a value anything passed.

We call this **capture, not retrieve**, and make three contributions.
**C1 — The argument, measured.** Against a deliberately generous callback-level baseline over
the same run, which recovers every activity, relation and verdict, we show that the facts an
agent *computed* in order to decide, and the rule that combined them, are unrecoverable.
**C2 — A reflexive multi-agent architecture.** Four operational agents emit typed PROV about
themselves; two meta agents govern that output, to a depth bounded by design.
**C3 — The `yp:` vocabulary**, published as an OWL ontology with SHACL shapes, and an API whose
one hand-written call records the decision. We also measure the cost, in time and in energy.

## 2. Background and State of the Art

### 2.1 Provenance models and capture systems

W3C PROV [@provdm; @provo] models provenance as entities, activities and agents linked by a small
set of relations; it is deliberately domain-neutral, and says nothing about rationale. Capture
systems differ in where they observe. *Script and workflow* tools — noWorkflow [@noworkflow] for
Python scripts, YesWorkflow [@yesworkflow] for annotated workflows — recover lineage from code and
execution. *System-level* collectors such as SPADE [@spade] and CamFlow [@camflow] capture
whole-system provenance from the operating system, CamFlow emitting W3C PROV-JSON directly.
*Machine-learning* trackers record runs, parameters and artifacts: MLflow [@mlflow] with its own
schema, and the yProv family — yProv4ML [@yprov4ml] for training runs, yProv4WFs for workflows —
in W3C PROV. Surveys of the field [@freire2008; @herschel2017] classify provenance by *what* it
records and *how* it is captured; across all of them, what is recorded is an observable event.
Our demonstrator consumes real yProv4ML records, and every system above could, in principle,
observe the agents we build. None could observe their reasoning.

### 2.2 Agents, and the observability built for them

Agent frameworks interleave reasoning and tool use [@react] and compose agents into multi-agent
systems [@autogen; @langgraph]. Their runs are observed by a fast-maturing layer:
OpenTelemetry's semantic conventions for generative AI [@otelgenai], and tracing platforms such as
OpenLLMetry [@openllmetry], Langfuse [@langfuse] and LangSmith [@langsmith], which record prompts,
completions, tool calls, latencies and token counts. This layer is built for debugging and cost
attribution. It does not model W3C PROV, and the only rationale it can hold is text a model
happened to emit. PROV-AGENT [@provagent] is the closest prior work: it extends PROV to agentic
workflows and makes agents and their model calls first-class provenance subjects — a richer
*what*, with no field for the reason behind a decision.

### 2.3 Rationale, explanation and accountability

Interpretability research asks how a model reaches outputs [@doshivelez2017], and documentation
practice — model cards [@modelcards], datasheets [@datasheets] — records how artifacts were made.
Two results bear directly on agents. A model's stated chain of thought is not necessarily
faithful to the process that produced its answer [@turpin2023; @lanham2023], so text a model emits
is weak evidence of the rule an agent applied. And proposals for the oversight of deployed agents
call for exactly the per-action visibility — identifiers, activity logs, the basis of
decisions — that a provenance record could carry [@chan2024]. Energy is the third concern of the
field: reporting it for ML workloads is now expected [@strubell2019; @henderson2020], usually
through sampling tools such as CodeCarbon [@codecarbon], whose limits for short steps we revisit
in Section 5.3.

| Approach | W3C PROV | Automatic | Agent-aware | Decision rule |
|---|:-:|:-:|:-:|:-:|
| Script, workflow and system capture | yes | yes | no | no |
| ML lifecycle trackers | partly | yes | no | no |
| LLM and agent observability | no | yes | yes | no |
| PROV-AGENT | yes | yes | yes | no |
| This work | yes | partly | yes | **yes** |

**Table 1.** The state of the technology. "Decision rule" means the reason an agent's code reached
its verdict, as opposed to model text that may describe one. Our capture is automatic except for
that one field.

**Our position is to ride the automatic layer rather than compete with it**: consume what it
collects, and add the one thing it structurally cannot.

## 3. Vocabulary and Instrumentation

**The `yp:` vocabulary** is published as an OWL ontology (86 terms) with SHACL [@shacl] shapes
(16 named constraints). It emits only terms the system uses, checked in both directions by a test,
and uses PROV's own term wherever one exists — all lineage relations are PROV's. It adds two
things PROV lacks: a typed decision lifecycle — Receive, Plan, Act, Observe, **Decide**, Respond,
as subclasses of `prov:Activity` — and `yp:justification`, with `rdfs:domain yp:DecisionActivity`,
beside `yp:verdict` and `yp:escalation_reason`. The shapes declare the standard: every decision is
justified and concluded; every step is typed, dated, ended and attributed; every agent decides
something; every `yp:pid` carries a `sha256:` hash. An agent's own runtime check is tested against
the shapes rather than trusted to match them, which has found four disagreements, each fixed.

**Two layers.** *Layer 1, what happened* — activities, timings, model calls, dataflow — follows
mechanically from what a framework callback receives, and the ecosystem of Section 2.2 already
collects it. *Layer 2, why* — the verdict and its justification — cannot be automated. The
library automates the first and leaves one call hand-written:

```python
with tracker.activity("ToolCallActivity", "write the summary") as prov:
    prov.consumes(task)
    summary = llm.complete(ACT_SYSTEM, facts)
    prov.produces("ResponseEntity", summary.text, pid=pointer, derived_from=task)
    prov.note(**summary.provenance())        # model, prompt hash, tokens

tracker.decide(verdict="REVISE",             # the hand-written line
    justification="Figures 4096 and 512 appear in the summary but not in the graph.")
```

`decide()` raises on a blank justification. Lineage is declared, never inferred — an early
tracker that inferred derivations from registration order produced 74 confident, wrong edges of
250 — except `wasInformedBy`, which PROV defines as derivable. Durations come from a monotonic
counter: on our host the wall clock advances in 15.6 ms steps, and before this fix 96 of 96
activities recorded zero duration.

## 4. The Demonstrator

A producer replays real yProv4ML records over a NATS bus to four operational agents, which do
work and emit typed PROV about themselves; two meta agents consume *that* and emit PROV in turn.
There is no agent framework: the agents are bare asyncio daemons, the strongest evidence that the
library is not tied to one runtime.

| Agent | Work | Verdicts |
|---|---|---|
| CAx Summariser | LLM summary, figures checked against the graph | ACCEPT/REVISE |
| CA2 Metrics | renders a chart of the run | ACCEPT/REVISE |
| CA3 Analytics | five mining algorithms over any PROV graph | OK/ANOMALY |
| CA1 Storage | persists only after consumers used the record | ACCEPT/ESCALATE |
| MC1 Compliance | checks records against the shapes | PASS/FAIL |
| MC3 Audit | rebuilds the who-did-what-when trail | COMPLETE/PARTIAL |

**Table 2.** The six agents. All are domain-agnostic: they compute from PROV itself.

CA1 writes through the store's API, never the bus, which carries only a receipt (a `yp:pid` and a
SHA-256). It persists *after* consumption — at a quorum of consumer acknowledgements, with a
timeout guard so a crashed consumer cannot lose a record — and its `yp:escalation_reason`
separates three conditions demanding different responses: *integrity*, *incomplete-input* and
*consumption-unconfirmed*. Envelopes on the bus are numbered, and an agent that finds a gap
records an `INCOMPLETE` decision rather than a well-formed record of a truncated input.

**Reflexivity is one level deep, by construction.** Meta agents publish to a subject prefix
nothing subscribes to, so governance cannot re-trigger itself. Within that bound the meta agents
are fully instrumented, and MC1 satisfies the standard it enforces. One container image serves
all roles; CI runs both the containerised NATS path and an in-process path and holds each to the
same lineage comparison.

## 5. Evaluation

The reference run is two input records through six agents: **24 PROV documents**, 96 activities,
324 relations (`experiments/audit-reference`). Every figure is regenerated by code from archives
frozen from clean commits, with per-file hashes; 246 tests run on Linux and Windows.

### 5.1 Conformance

All 24 documents are accepted by the reference `prov` parser, and all 24 conform to the published
shapes under `pyshacl` [@pyshacl] — an external judge of an external rule. The relation total is
the wrong number to quote: 126 relations are observed, 72 derived, 30 declared and 96 are
one-per-activity attributions, so the structural claim is 228. Applied to its input, CA3 found
real gaps in yProv4ML's records: 8 of 9 activities declare a start and no end.

### 5.2 Capture, not retrieve

Strategy A is a callback-level baseline we built ourselves from the *same run*, fed every step
event a framework handler receives. It is an upper bound: its events carry the structural facts
the explicit instrumentation declared.

| Signal | A | B |
|---|--:|--:|
| Activities, typed | 96 | 96 |
| Entities, typed | 0 of 54 | 78 of 78 |
| Relations | 324 | 324 |
| Verdicts | 24 | 24 |
| Justifications | 0 | 24 |
| Accepted by the PROV parser | 24 | 24 |

**Table 3.** Callback baseline (A, with a ten-rule type map) against explicit capture (B).

Structure is recoverable, and the verdicts are too — scraped from node labels, which a graph
naming its decision node `decide` would defeat. No justification appears verbatim in the 12,425
characters A observed, but a run-time sentence rarely survives verbatim, so we test facts:

| Decisions | Count | Facts | Seen by A | All seen |
|---|--:|--:|--:|--:|
| Operational | 8 | 49 | 30 (61%) | 2 |
| Meta | 16 | 16 | 16 (100%) | 16 |

**Table 4.** Each justification's facts (figures, hashes, names, roles) searched for in
everything A saw. Seeing a fact over-counts knowing it.

The 19 facts A never saw are those the agents *computed in order to decide*: a record's byte
count, a timing gap and its share of the run, a summary's length, a threshold. Where A saw every
fact, it still lacks the rule: CA2's facts were all visible, but not that finding no numeric
values sent it to a fallback chart, nor that the fallback is why it said REVISE. Meta-agent
justifications restate structure A already has, and are not counted. **The inputs to a decision
may be observable; the rule that combined them is not.**

> **Figure 1.** One verdict, two causes: CA1's integrity and timeout escalations, with both
> justifications quoted as the record carries them.

Figure 1 shows the cost. CA1 says ESCALATE for three reasons; A recovers the verdict, never the
reason — so it cannot tell *"storage is corrupting records"* from *"a consumer is down"*.

### 5.3 Energy

On our host the CPU is measured — the Windows Energy Meter Interface reads the package counter,
tracking load from 5 W idle to 31 W saturated — while memory is a flat 10 W model. A record must
say so per component: a sum of the two is labelled `partly-measured`, never `measured`. We first
concluded the counter was broken; it reads zero until its first sample.

| CPU energy per session | OFF | B-TRANS | B |
|---|--:|--:|--:|
| joules | 1.43 | 1.50 | 1.72 |
| vs OFF (margin) | — | +5% (0.9x) | +20% (2.3x) |

**Table 5.** Measured CPU energy per six-agent session, 12 interleaved rounds with idle windows
(`experiments/energy-session`). No difference reaches 3x its spread.

Per step, energy cannot be attributed at all: at a 15 s sampling period a step that did nothing
was charged ten times the step that rendered a chart. Each figure therefore carries
`yp:energy_sampling_period_s` and `yp:energy_attributable`, and **energy belongs at session
granularity**, where Table 5 measures it.

### 5.4 Model provenance

Every model-bearing step records the model, sampling parameters, prompt identity, version and
hash, token counts, latency and a response hash (`experiments/audit-llm`: four calls, 2,071
prompt and 480 completion tokens). The model a caller names is not the one that answers: the
record carries the alias `gpt-4o`, the snapshot `gpt-4o-2024-08-06` and the provider's
fingerprint — and the four calls of one run returned three fingerprints.

### 5.5 Overhead

Each agent's step was timed 30 times per condition, interleaved: provenance off, built but not
written (B-TRANS), and built and written (B); a difference counts only at 3x its spread. On two
occasions, **building the record was inside every operational agent's run-to-run variance**
(margins 0.0–0.5x). Writing it resolves for some agents, and there it dominates: for the worst
case, MC1, a 109 µs step gains 38 µs to build the record and 1,485 µs to write it (+1,394%, 98%
of it the write). Transient mode removes 89% of the time cost; metadata mode 38% of the bytes,
keeping every justification. Recording a justification costs 54 ns against a ±726 ns spread, and
justifications are 6.5% of the records' bytes. Cost is linear (fitted exponents 1.09 and 1.13 to
10,000 activities). Across occasions, two agents' verdicts flipped between resolved and not, so
we quote the verdicts that held, the bytes and the complexity class, not the timings.

### 5.6 Verdict reachability

The ordinary run reaches six of twelve two-valued verdicts; nine injected scenarios reach the
rest, each of CA1's three escalations, and `INCOMPLETE` — all thirteen are observed. Three
scenarios are not faults: CA2 says REVISE on the bundled data because yProv4ML records carry no
numeric values, a finding about the input. `mc1:FAIL` is unreachable from this library's own
records, because `decide()` refuses a blank justification; it is reached by a third-party record,
which is what a compliance checker is for.

## 6. Limitations

**We show that justifications exist and that the decision rule is unrecoverable, not that they
are good**; whether they improve an operator's decisions needs a human study. The fact-level
measure is coarse in both directions. A model-bearing step is repeatable from its record, not
reproducible: three fingerprints in one run is a backend changing under a caller that changed
nothing. The bus is at-most-once: numbered envelopes expose a partial loss, but a stream lost
whole is silent. The evaluation uses two input records, one producer and one machine, and we
built the baseline ourselves — mitigated by granting it every declared structural fact.

## 7. Conclusion

Provenance can describe what an agentic system did; with automatic capture alone it cannot
describe why, because the rule behind a decision never leaves the agent. We made that measurable
against a generous baseline, discharged it with a vocabulary and an API whose one hand-written
call records the decision, and found the cost to be storage rather than speed — *storage for
explainability, not speed for explainability*. Next: an OpenTelemetry bridge for the automatic
half, bounded second-level reflexivity, meta agents that act on their verdicts, and a study of
whether justifications improve operators' decisions.
