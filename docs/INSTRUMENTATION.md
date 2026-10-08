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

A decorator, a context manager, and **one** explicit call. That last call is the only place
a developer writes something a machine could not have inferred:

```python
from yprov4agenticai import ProvTracker

tracker = ProvTracker(sid=session, agent_label="NL_Summarizer")

@tracker.step("PlanningActivity")                 # S2 — auto: timing, typing, lineage
def plan(task): ...

with tracker.activity("ToolCallActivity") as act: # S3 — auto: inputs, outputs, energy
    result = tool.run(task)

tracker.decide(                                   # S5 — THE hand-written line
    verdict="REVISE",
    confidence=0.41,
    justification="Self-consistency 0.41 < 0.70 threshold; the drafted efficiency claim "
                  "contradicts recorded execution times for activity Training. Regenerating.",
)

tracker.emit()                                    # → PROV-JSON envelope → bus
```

Everything except `decide(...)` can be generated. `decide(...)` cannot, and that is the point.

### "For any agent" — how framework-agnosticism actually works

The vocabulary (PROV-O + `yp:`) is framework-independent; only a **thin adapter** binds the
6-step lifecycle to a given framework's events:

```
        ┌────────────────────────────────────────────┐
        │   yprov4agenticai  —  core (framework-free) │
        │   ProvTracker · vocabulary · store · energy │
        └───────────────┬────────────────────────────┘
      ┌─────────────────┼─────────────────┬───────────────────┐
 LangGraphAdapter  CrewAIAdapter   AutoGenAdapter      (bare daemon)
```

The demonstrator in this folder is the bare-daemon case: six agents, no agent framework at
all, same instrumentation, same output. That is the strongest possible evidence that the
library is not welded to one runtime.

---

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
documents with **78 typed activities, 44 typed entities, 250 lineage relations, and
`yp:justification` on 24/24 decisions (100%)** — against a structural 0 for any
callback-level approach.

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
> justification=…)` call; it is framework-agnostic through thin adapters over a shared
> PROV-O + `yp:` vocabulary, and it emits W3C PROV-JSON onto the bus. Strategically: ride
> OTel for collection, own the PROV and justification layer — that is the part the
> observability tools structurally cannot provide, and the part the EU AI Act asks for.
