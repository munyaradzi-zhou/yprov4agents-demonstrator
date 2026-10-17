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
operator, an auditor and, under the EU AI Act [@euaiact], a regulator need the reason, not the
outcome.

We argue that this gap is **structural, not a matter of tooling**, and state it precisely. A
decision step receives inputs $x$, computes intermediate values $z = g(x)$, and returns a verdict
$v = r(x, z)$. We call $z$ the *computed facts* and $r$ the *decision rule*. A *boundary
observer* (a callback that fires after the step returns, a span that wraps it, a query that
reads the store afterwards) sees only what crosses the step's interface: $x$ where it is passed,
$v$, and whatever the step chooses to emit.

**Proposition 1.** From a boundary observer's record, $z$ is recoverable only by re-running $g$,
which the record does not contain, and $r$ is not identifiable: any rule $r'$ with
$r'(x, g(x)) = v$ explains the observation equally well, and a two-valued verdict tells the observer one bit
about $r$ per decision.

Retrieving the reason is therefore an identification problem without a unique answer, and only
the step itself holds both $z$ and the branch of $r$ it took. An observer *inside* the step, such
as a line tracer, sees both, but that is capture by other means, not retrieval; Section 5.2
measures what it recovers and costs. We call this **capture, not retrieve**, and make three
contributions.
**C1: the argument, measured.** Against a deliberately generous callback-level baseline over
the same run, which recovers every activity, relation and verdict, we show that the facts an
agent *computed* in order to decide, and the rule that combined them, are unrecoverable from
outside the step.
**C2: a reflexive multi-agent architecture.** Four operational agents emit typed PROV about
themselves; two meta agents govern that output, to a depth bounded by design.
**C3: the `yp:` vocabulary**, published as an OWL ontology with SHACL shapes, and an API whose
one hand-written call records the decision. We also measure the cost, in time and in energy.

## 2. Background and State of the Art

### 2.1 Provenance models and capture systems

W3C PROV [@provdm; @provo] models provenance as entities, activities and agents linked by a small
set of relations; it is deliberately domain-neutral, and says nothing about rationale. Capture
systems differ in where they observe. *Script and workflow* tools (noWorkflow [@noworkflow] for
Python scripts, YesWorkflow [@yesworkflow] for annotated workflows) recover lineage from code and
execution. *System-level* collectors such as SPADE [@spade] and CamFlow [@camflow] capture
whole-system provenance from the operating system, CamFlow emitting W3C PROV-JSON directly.
*Machine-learning* trackers record runs, parameters and artifacts: MLflow [@mlflow] with its own
schema, and the yProv family (yProv4ML [@yprov4ml] for training runs, yProv4WFs for workflows)
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
workflows and makes agents and their model calls first-class provenance subjects: a richer
*what*, with no field for the reason behind a decision.

### 2.3 Rationale, explanation and accountability

Interpretability research asks how a model reaches outputs [@doshivelez2017], and documentation
practice, such as model cards [@modelcards] and datasheets [@datasheets], records how artifacts were made.
Two results bear directly on agents. A model's stated chain of thought is not necessarily
faithful to the process that produced its answer [@turpin2023; @lanham2023], so text a model emits
is weak evidence of the rule an agent applied. And proposals for the oversight of deployed agents
call for exactly the per-action visibility (identifiers, activity logs, the basis of
decisions) that a provenance record could carry [@chan2024]. Energy is the third concern of the
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

**The `yp:` vocabulary** is published as an OWL ontology (88 terms) with SHACL [@shacl] shapes
(18 named constraints). It emits only terms the system uses, checked in both directions by a test,
and uses PROV's own term wherever one exists: all lineage relations are PROV's. It adds two
things PROV lacks: a typed decision lifecycle (Receive, Plan, Act, Observe, **Decide**, Respond,
as subclasses of `prov:Activity`) and `yp:justification`, with `rdfs:domain yp:DecisionActivity`,
beside `yp:verdict`, `yp:escalation_reason`, and the `yp:rule` and `yp:rule_inputs` that make a
decision replayable. The shapes declare the standard: every decision is
justified and concluded; every step is typed, dated, ended and attributed; every agent decides
something; every `yp:pid` carries a `sha256:` hash. An agent's own runtime check is tested against
the shapes rather than trusted to match them, which has found four disagreements, each fixed.

**Two layers.** *Layer 1, what happened* (activities, timings, model calls, dataflow) follows
mechanically from what a framework callback receives, and the ecosystem of Section 2.2 already
collects it. *Layer 2, why* (the verdict and its justification) cannot be automated. The
library automates the first and leaves one call hand-written:

```python
with tracker.activity("ToolCallActivity", "write the summary") as prov:
    prov.consumes(task)
    summary = llm.complete(ACT_SYSTEM, facts)
    prov.produces("ResponseEntity", summary.text, pid=pointer, derived_from=task)
    prov.note(**summary.provenance())        # model, prompt hash, tokens

tracker.decide(verdict="REVISE",             # the hand-written line
    justification="Figures 4096 and 512 appear in the summary but not in the graph.",
    rule=SUMMARY_RULE,                       # cax.summary/v1: a pure function
    evidence={"ungrounded_figures": 2, "truncated": False})
```

`decide()` raises on a blank justification, and on a verdict its own rule does not give. A rule
is a pure function from named values to the decision's outputs, versioned in its name, so a
record naming `cax.summary/v1` names one function. Applied at the call and recorded with its
inputs, it makes the decision replayable: anyone holding the code can re-derive the verdict
from the record. The justification stays prose for a reader; the rule is what makes the record
checkable against the code, which a sentence, written by a developer or a model [@turpin2023],
is not. Lineage is declared, never inferred, except
`wasInformedBy`, which PROV defines as derivable; an early tracker that inferred derivations from
registration order produced 74 confident, wrong edges of 250. Durations come from a monotonic
counter: on our host the wall clock advances in 15.6 ms steps, and before this fix 96 of 96
activities recorded zero duration.

## 4. The Demonstrator

A producer replays real yProv4ML records over a provenance bus (NATS) to four operational agents, which do
work and emit typed PROV about themselves; two meta agents consume *that* and emit PROV in turn.
There is no agent framework: the agents are bare asyncio daemons, the strongest evidence that the
library is not tied to one runtime. Figure 1 shows the architecture.

> **Figure 1.** The demonstrator. A producer replays yProv4ML records onto the provenance bus;
> four operational agents do the work and emit their own PROV, which two meta agents govern.
> CA1 writes to yProvStore through the store's API, never over the bus.

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
SHA-256). It persists *after* consumption, at a quorum of consumer acknowledgements, with a
timeout guard so a crashed consumer cannot lose a record; its `yp:escalation_reason`
separates three conditions demanding different responses: *integrity*, *incomplete-input* and
*consumption-unconfirmed*. Envelopes on the bus are numbered, and an agent that finds a gap
records an `INCOMPLETE` decision rather than a well-formed record of a truncated input.

**Reflexivity is one level deep, by construction.** Meta agents publish to a subject prefix
nothing subscribes to, so governance cannot re-trigger itself. Within that bound the meta agents
are fully instrumented, and MC1 satisfies the standard it enforces. One container image serves
all roles; CI runs both the containerised NATS path and an in-process path and holds each to the
same lineage comparison.

## 5. Evaluation

The reference run is the two records bundled with yProv4ML through six agents: **24 PROV documents**, 96 activities,
324 relations (`experiments/decision-replay`; the timing study is in `experiments/audit-reference`). Every figure is regenerated by code from archives
frozen from clean commits, with per-file hashes; 263 tests run on Linux and Windows.

### 5.1 Conformance

All 24 documents are accepted by the reference `prov` parser, and all 24 conform to the published
shapes under `pyshacl` [@pyshacl], an external judge of an external rule. The relation total is
the wrong number to quote: 126 relations are observed, 72 derived, 30 declared and 96 are
one-per-activity attributions, so the structural claim is 228. Applied to its input, CA3 found
real gaps in yProv4ML's records: 8 of 9 activities declare a start and no end.

### 5.2 Capture, not retrieve

Strategy A is a callback-level baseline we built ourselves from the *same run*, fed every step
event a framework handler receives. It is an upper bound: its events carry the structural facts
the explicit instrumentation declared. Figure 2 compares the two over the 24 records.

> **Figure 2.** Callback baseline (A, with a ten-rule type map) against explicit capture (B),
> signal by signal over the 24 records. A recovers every activity, relation and verdict; it
> types no entity, carries no pointer and recovers no justification.

Structure is recoverable, and so are the verdicts, scraped from node labels, which a graph
naming its decision node `decide` would defeat. No justification appears verbatim in the 12,425
characters A observed, but a run-time sentence rarely survives verbatim, so we ask instead about
the values each decision's rule read, which the record names (Table 3).

| Agent | Decisions | Values read | Checks | Seen by A | Stated in prose |
|---|--:|--:|--:|--:|--:|
| CA1 Storage | 2 | 4 | 4 | 0 | 2 |
| CA2 Metrics | 2 | 4 | 0 | 0 | 0 |
| CA3 Analytics | 2 | 10 | 0 | 2 | 8 |
| CAx Summariser | 2 | 2 | 2 | 0 | 0 |
| MC1 and MC3 | 16 | 16 | 0 | 0 | 0 |
| All | 24 | 36 | 6 | 2 | 10 |

**Table 3.** The values each decision's rule read (`yp:rule_inputs`), searched for in everything
A saw and in the decision's own justification. Checks are booleans, the outcome of a test inside
the step, which a text search cannot attribute, so they are counted apart. A number counts as
seen at any rounding within 5%.

A saw 2 of the 36: the wall clock span of each input run, a field of the yProv4ML record that
crossed the boundary as part of $x$. Every computed value (gap statistics, byte counts, failed
checks, ungrounded figures) was invisible, as Proposition 1 predicts. A looser test, extracting
facts from the justification prose, finds 30 of the operational agents' 49 facts (61%) visible,
and the gap between the measures is the point: every fact in CA2's prose is visible to A, and
neither value its rule read is. **The inputs to a decision may be observable; the values it
computed, and the rule that combined them, are not.**

**At scale.** Two records cannot carry a claim about records in general, so we trained 24 small
models for real and let yProv4ML record each run (`corpus/`, `experiments/corpus-24`): 288
decisions, all conforming to the shapes. A saw 24 of the 432 values the rules read (5.6%), each
again an input run's wall clock, while the prose-fact measure rose to 75%: the more the input
carries, the more of the prose an observer can match, and the less that says about the
decision. The corpus also found what two records could not: CAx read the digits inside run
names as asserted figures and said REVISE on half the runs, and CA3 found a 1.0 s stall before
the first epoch of every run, 74 to 87% of its wall clock. The first was a defect, now fixed;
the second is a finding about the producer.

**A real framework.** A handler we wrote might see less than a real one, so we ran the four
operational agents unmodified as nodes of a LangGraph graph [@langgraph] over the same corpus,
observed by a subclass of LangChain's own callback handler (`experiments/framework-baseline`).
The framework hands its handler the whole graph state at every node: 45 times the bytes our
handler saw. Of the 240 values the rules read, 110 match somewhere in it, and 106 of those
match just as well in an unrelated run's observations: zeros, small counts, and two labels the
agents print in their one-line results (CA1's trigger, CA2's chart subject). Four match only
in their own run. A real framework shows its observer more of the input, and no more of the
decision.

**Replay.** All 24 decisions replay, as do all 288 on the corpus and the nine of the fault scenarios, which between
them reach every verdict and each of CA1's escalation reasons: the named rule, applied to the
recorded inputs, gives the recorded outputs. Editing a stored verdict or input makes replay
fail. The prose is the weaker record: it states 10 of the 36 values its rules read, and
paraphrases the rest ("all supported" for zero ungrounded figures) or omits them (a chart's byte
count).

**Inside the step.** A line tracer is the observer Proposition 1 leaves out, so we built one:
`sys.settrace` over the agents' own frames, keeping each change to a scalar local, on the same
agents and producer record as the overhead study (`experiments/line-tracer`). It holds 7 of the
12 values the six rules read: CA3's five gap statistics, CA2's byte count and CA1's trigger.
The other five (counts of failed checks, ungrounded figures and missing envelopes) were computed
inside an expression and never assigned, so no tracer of locals sees them. The seven are among
15 to 116 distinct values per step, with nothing to mark them as the decision's inputs or to say
what was done with them, and tracing multiplies the step's time by 2 to 13 where building our
record is inside the noise. Seeing inside the step yields values, not the rule, and costs more
than recording the rule.

The cost is visible in one agent. CA1 says ESCALATE for three reasons; A recovers the verdict,
never the reason, so it cannot tell *"storage is corrupting records"* from *"a consumer is down"*.

### 5.3 Energy

On our host the CPU is measured (the Windows Energy Meter Interface reads the package counter,
tracking load from 5 W idle to 31 W saturated), while memory is a flat 10 W model. A record must
say so per component: a sum of the two is labelled `partly-measured`, never `measured`. We first
concluded the counter was broken; it reads zero until its first sample.

| CPU energy per session | OFF | B-TRANS | B |
|---|--:|--:|--:|
| joules | 1.43 | 1.50 | 1.72 |
| vs OFF (margin) | baseline | +5% (0.9x) | +20% (2.3x) |

**Table 4.** Measured CPU energy per six-agent session, 12 interleaved rounds with idle windows
(`experiments/energy-session`). No difference reaches 3x its spread.

Per step, energy cannot be attributed at all: at a 15 s sampling period a step that did nothing
was charged ten times the step that rendered a chart. Each figure therefore carries
`yp:energy_sampling_period_s` and `yp:energy_attributable`, and **energy belongs at session
granularity**, where Table 4 measures it.

### 5.4 Model provenance

Every model-bearing step records the model, sampling parameters, prompt identity, version and
hash, token counts, latency and a response hash (`experiments/audit-llm`: four calls, 2,071
prompt and 480 completion tokens). The model a caller names is not the one that answers: the
record carries the alias `gpt-4o`, the snapshot `gpt-4o-2024-08-06` and the provider's
fingerprint, and the four calls of one run returned three fingerprints.

### 5.5 Overhead

Each agent's step was timed 30 times per condition, interleaved: provenance off, built but not
written (B-TRANS), and built and written (B); a difference counts only at 3x its spread. On two
occasions, **building the record was inside every operational agent's run-to-run variance**
(margins 0.0 to 0.5x). Writing it resolves for some agents, and there it dominates (Figure 3): for the worst
case, MC1, a 109 µs step gains 38 µs to build the record and 1,485 µs to write it (+1,394%, 98%
of it the write). Transient mode removes 89% of the time cost; metadata mode 38% of the bytes,
keeping every justification. Recording a justification costs 54 ns against a ±726 ns spread, and
justifications are 6.4% of the records' bytes, and a decision's rule and inputs 1.7% (added after the timing
study, which predates them). Cost is linear (fitted exponents 1.09 and 1.13 to
10,000 activities). Across occasions, two agents' verdicts flipped between resolved and not, so
we quote the verdicts that held, the bytes and the complexity class, not the timings.

> **Figure 3.** Where the overhead is: each agent's own work, building the PROV record, and
> writing it to disk, as shares of one record's end-to-end time, with the measured total and
> the overhead against no provenance (CA2, inside the noise, is omitted).

### 5.6 Verdict reachability

The ordinary run reaches six of twelve two-valued verdicts; nine injected scenarios reach the
rest, each of CA1's three escalations, and `INCOMPLETE`: all thirteen are observed. Three
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
whole is silent. The evaluation uses one producer (24 real records and two bundled ones) and one machine. We
built the main baseline ourselves, granted it every declared structural fact, and checked it
against a real framework's handler, which saw more bytes and no more of the decisions.

## 7. Conclusion

Provenance can describe what an agentic system did; with automatic capture alone it cannot
describe why, because the rule behind a decision never leaves the agent. We made that measurable
against a generous baseline, discharged it with a vocabulary and an API whose one hand-written
call records the decision, and found the cost to be storage rather than speed: *storage for
explainability, not speed for explainability*. Next: an OpenTelemetry bridge for the automatic
half, bounded second-level reflexivity, meta agents that act on their verdicts, and a study of
whether justifications improve operators' decisions.
