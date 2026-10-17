# How do we instrument agents? — the behavioural-provenance question

> Sandro, Slack, 2026-10-05: *"another point is how we instrument agents. Library approach?
> Any idea about the API? or a different approach? … this is for any agent … and it is
> about tracking the behavioural provenance."*

**Short answer: it is not library-vs-API. It is two layers, and only the second one is ours.**

---

## 1. The decisive constraint

Behavioural provenance has an **irreducible explicit component**, and this is a structural
fact rather than a design preference:

> **The rationale for a decision is a local variable at the moment of decision.**
> Nothing that observes the agent from the outside — a callback that fires after the
> function returns, a span that wraps the call, a query that reads the store afterwards —
> can retrieve what was never captured.

We call this **capture, not retrieve**. It has three consequences:

1. *Data* lineage (what was read, what was written, when) **can** be captured automatically.
2. *Behavioural* rationale (**why** this verdict, why REVISE rather than ESCALATE) **cannot**.
3. Therefore any instrumentation approach that is purely automatic is, for the behavioural
   half of the problem, **provably incomplete** — and this is precisely the half that
   governance, auditing, and the EU AI Act's transparency requirements depend on.

This is also why the pure-observability teams cannot close this gap by adding more
collectors: the information is not in the span.

---

## 2. The two layers

| | Layer 1 — the skeleton | Layer 2 — the rationale |
|---|---|---|
| **Question** | *what* happened, *when* | ***why*** it happened |
| **Mechanism** | OpenTelemetry auto-instrumentation / framework hooks | explicit library API at decision points |
| **Captures** | activities, timings, tool calls, LLM calls, token counts | typed activities & entities, lineage, **`yp:justification`**, verdict, confidence |
| **Developer effort** | ~zero | a few lines, and only at the decisions |
| **Can it be automated?** | yes | **no — by construction** |
| **Who owns it** | the ecosystem (don't reinvent) | **us** |

**Emit:** a PROV bridge maps both layers into W3C PROV + the `yp:` vocabulary and publishes
PROV-JSON onto the bus. That bridge is the whole integration surface.

---

## 3. The API

A decorator, a context manager, and **one** explicit call. The decorator and the context
manager observe what a machine can observe; the explicit call states what only the code
making the decision knows.

```python
from yprov4agenticai import ProvTracker

tracker = ProvTracker(sid=session, agent_label="NL_Summarizer", sink=publish_to_bus)

# S2 — a decorated function. Automatic: type, label, start instant, measured duration,
# agent attribution, sequence position, and the failure case.
@tracker.step("PlanningActivity")
def plan(task: str) -> str:
    ...

# S3 — a block. The scope is where the step declares what it touched.
with tracker.activity("ToolCallActivity", "write the summary") as prov:
    prov.consumes(task)
    summary = llm(prompt)
    prov.produces("ResponseEntity", summary, pid=pointer, derived_from=task)
    prov.note(**{"yp:model": model_id})

# S5 — THE hand-written line. `justification` is mandatory; a blank one raises.
# The rule and the values it read make the decision replayable from the record.
from yprov4agenticai.rules import Rule

CONSISTENCY_RULE = Rule(
    "summariser.consistency/v1",
    lambda e: {"yp:verdict": "ACCEPT" if e["self_consistency"] >= 0.70 else "REVISE"},
)
evidence = {"self_consistency": 0.41}
tracker.decide(
    verdict=CONSISTENCY_RULE.apply(evidence)["yp:verdict"],
    confidence=0.41,
    justification="Self-consistency 0.41 < 0.70 threshold; the drafted efficiency claim "
                  "contradicts recorded execution times for activity Training. Regenerating.",
    rule=CONSISTENCY_RULE,
    evidence=evidence,
)

tracker.emit()                                    # → PROV-JSON → the sink → the bus
```

Everything except `decide(...)` can be generated. `decide(...)` cannot, and that is the point.

**Rules and replay.** `rule` and `evidence` are optional, and come together or not at all.
Given them, `decide()` applies the rule to the evidence and refuses a verdict (or
`yp:escalation_reason`) the rule does not give; the record then carries `yp:rule` and
`yp:rule_inputs`, and `python -m eval.replay --prov-dir <dir>` re-derives every verdict from
the record. A justification is prose, and prose can say anything; a verdict that replays
cannot. A rule's name carries its version, and a rule whose logic changes takes a new name.

**When a model decides.** The rule is the code that reads the model's answer: ask for a
fixed JSON form, parse it into the evidence, and let the rule turn it into the verdict,
failing closed on an answer it cannot read. Replay then shows the verdict follows from what
the model said; it cannot re-run the model. CAx works this way under `CAX_VERDICT=model`, and
records the deterministic check's verdict beside the model's as `yp:code_verdict`. Note that
the model's answer is a step output, so a callback handler sees it too: for a model's stated
reason, capture adds accountability rather than visibility.

### What is automatic, and what has to be declared

The split is not a matter of effort budget. It follows from what a wrapper can see.

| | Observed, so automatic | Declared, because unobservable |
|---|---|---|
| **Activity** | type, label, start instant, duration, ordering, agent, exception on failure | — |
| **Entity** | — | that it exists, its type, its pointer |
| **Lineage** | `wasInformedBy` (*derived* from declared dataflow) | `used`, `wasGeneratedBy`, `wasDerivedFrom` |
| **Decision** | — | verdict, confidence, **`yp:justification`**, `yp:rule`, `yp:rule_inputs` |

A wrapper sees that a function ran. It does not see which of the values in scope that
function treated as its input — so lineage is stated through the scope, never guessed. An
earlier version of the tracker did guess, by linking each record to the one before it, and
produced 74 confident and wrong edges out of 250. `wasInformedBy` is the one relation that
*is* automatic, because PROV defines it exactly: *a₂* was informed by *a₁* when *a₁*
generated an entity *a₂* used. That is derivable from declarations already made.

### Three properties the layer commits to

**It adds no record kind of its own.** Anything expressible through the API is expressible
through `register_entity`/`record_activity`. This is tested directly — the same lifecycle
built both ways reduces to an identical document — and it is why moving all six agents onto
the API produced a structural diff of exactly zero across 24 emitted documents.

**A step that raises is still recorded**, with `yp:outcome` and `yp:error_type`, before the
exception propagates. A failed step that leaves no trace is the worst case for an audit: the
record then shows a gap where it should show a failure.

**A decision without a justification raises `ValueError`.** Every other field can be filled
in by machinery. This one cannot, so it is the one field the API refuses to let a caller
skip — at the call site, where the reasoning still exists in a local variable.

### Measuring *when*, not just *that*

An activity is two instants in PROV, which invites reading both off the wall clock. That
loses short steps. On a Windows host the system clock advances in steps of about 15.6 ms, so
two `datetime.now()` readings either side of a sub-millisecond operation return the **same**
value — and the record says the step took no time at all, indistinguishable from a step
nobody measured.

So the start instant comes from the wall clock, where it must (it has to be comparable with
timestamps from other processes), and the duration comes from `time.perf_counter()`, which
is monotonic and nanosecond-resolution. The end instant is the start plus that duration.

The effect, on the same run, same agents, same graph:

| | activities with zero recorded duration | work attributed to the steps that did it |
|---|--:|--:|
| Before (both instants off the wall clock) | **96 of 96** | 0.000 ms |
| After (monotonic duration, wall-clock anchor) | **0 of 96** | 272.829 ms |

This is a precondition for anything else reading the timeline: CA3's timing algorithms
mine exactly these fields, and an overhead study cannot measure a step the record calls
instantaneous.

### Where the decorator fits, and where it does not

`tracker.step(...)` binds to a tracker instance, so it decorates functions defined where a
tracker is already in scope — a module-level pipeline, or a closure inside a session
handler. Every agent in this demonstrator creates its tracker per incoming message, so all
six use the context manager; the decorator is for the long-lived-tracker case. A
context-variable-based module-level decorator would cover both and is a deliberate
non-decision: it is worth adding when a framework adapter needs it, not before.

### "For any agent" — how framework-agnosticism actually works

The vocabulary (PROV-O + `yp:`) is framework-independent; only a **thin adapter** binds the
6-step lifecycle to a given framework's events:

```
        ┌─────────────────────────────────────────────┐
        │   yprov4agenticai  —  core (framework-free)  │
        │   ProvTracker · capture API · store · energy │
        └───────────────┬─────────────────────────────┘
      ┌─────────────────┼─────────────────┬───────────────────┐
 LangGraphAdapter  CrewAIAdapter   AutoGenAdapter      (bare daemon)
```

The demonstrator in this folder is the bare-daemon case: six agents, no agent framework at
all, same instrumentation, same output. That is the strongest possible evidence that the
library is not welded to one runtime.

## 4. The strategic position — and the tech-scouting answer

**Ride OpenTelemetry for collection; own the PROV + rationale layer on top.**

The ecosystem has already won Layer 1 — OTel's GenAI/agent semantic conventions,
OpenLLMetry (Traceloop), OpenInference (Arize Phoenix), Langfuse, LangSmith. Competing
there is wasted effort. None of them, however, model W3C PROV, and none capture decision
rationale: they are built for debugging and cost dashboards, not for provenance.

So the position is:

- **Consume** OTel spans where they exist → cheap, standard, zero-effort Layer 1.
- **Own** the PROV semantic layer and `yp:justification` → the defensible contribution.
- **Emit** W3C PROV-JSON → interoperable with the whole yProv ecosystem by construction.

This de-risks us (we do not fight the ecosystem) *and* sharpens the differentiator.

### Scouting targets

| Target | Why it matters | What to check |
|---|---|---|
| **OTel GenAI / agent semconv** | would give Layer 1 for free | is an agent-lifecycle span standardised yet, or still LLM-call-centric? |
| OpenLLMetry · OpenInference · Langfuse · LangSmith | the incumbents | their data model; confirm none emits PROV or captures rationale |
| **PROV-AGENT** (Souza/ORNL, arXiv:2508.02866) | the closest competitor; likely among Sandro's "other teams" | they use untyped `DomainData` and carry **no decision rationale** — verify it still holds |
| **AER / Vispute** (arXiv:2603.21692) | reasoning-provenance; their non-identifiability result formalises capture-vs-retrieve | adopt the formalism, note they use bespoke JSON, not PROV |

---

## 5. What this demonstrator already proves

Layer 2 is **built and running**. Across two records and six agents it emits 24 PROV
documents with **96 typed activities, 78 typed entities, 324 relations, and
`yp:justification` on 24/24 decisions** — and all 24 documents are accepted by the
reference `prov` parser, not merely by this project's own code.

Read those numbers with their denominators. The 324 relations are 126 observed dataflow
edges, 72 derived `wasInformedBy`, 30 declared derivations and 96 agent attributions (one
per activity); the first three are the structural claim, and quoting the total invites
"that counts steps, not structure". The 24/24 justification figure is instrumentation
coverage, not a quality result — the code that counts decisions is the code that justifies
them, so no run of this system could report another number. What a callback-level approach
structurally cannot reach is the decision rule: it may see the facts a decision used, but not
how they were combined into the verdict. Measured against the values the rules themselves
read, the callback baseline saw 2 of 36 (24 of 432 over 24 real training runs), all input
fields; a line tracer, LangChain's own handler on a LangGraph port, and gpt-4o reading the
trace did no better on reasons computed in code; and operators named a decision's cause from
the baseline's record in 53% of trials against 100% from the explicit one (`eval.replay`,
`eval.line_tracer`, `eval.framework_baseline`, `eval.reconstruct`, `eval.diagnose`).

Layer 1 (the OTel bridge) is **not built**; it is the scouting target and the natural next
increment. Nothing in the architecture has to change to accept it: the bridge feeds the
same tracker.

---

## 6. The one-paragraph version (for Slack or a slide)

> Instrumenting agents for *behavioural* provenance is two layers, not a choice between a
> library and an API. Layer 1 — *what/when* — should ride OpenTelemetry and framework hooks:
> automatic, standard, free. Layer 2 — *why* — needs a small explicit library API at the
> decision points, because the rationale behind a decision is a local variable at the moment
> of decision, and no callback, span, or post-hoc query can retrieve what was never
> captured. The API is a decorator, a context manager, and one `decide(verdict,
> justification=…, rule=…, evidence=…)` call, whose verdict replays from the record; it is framework-agnostic through thin adapters over a shared
> PROV-O + `yp:` vocabulary, and it emits W3C PROV-JSON onto the bus. Strategically: ride
> OTel for collection, own the PROV and justification layer — that is the part the
> observability tools structurally cannot provide, and the part the EU AI Act asks for.
