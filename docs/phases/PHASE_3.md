# Phase 3 — The shared baseline

> Follow-along notes. Plain language, with diagrams. The measured table lives in
> [`../../paper/OUTLINE.md`](../../paper/OUTLINE.md) E3; the code is
> [`../../eval/baseline.py`](../../eval/baseline.py).
>
> Previous: [`PHASE_2.md`](PHASE_2.md) · Plan: [`../../PLAN.md`](../../PLAN.md) Phase 3

---

## The problem in one sentence

The paper's central table compared this project against "what a callback-based approach can
do" — and that column was **filled with zeros nobody had measured**.

That was finding **B2**, and `PLAN.md` called it the most rejectable table in the paper.
Not because the conclusion was wrong, but because three of the four zeros were false, and
any reviewer who has written a LangChain callback handler would know it in ten seconds.

```
   THE OLD TABLE                          WHAT WAS WRONG WITH IT
   ─────────────                          ──────────────────────
   Typed activities      A: 0   B: 96     ✗ false — a handler sees every node
   Typed entities        A: 0   B: 78     ✗ false — it sees the payloads too
   Lineage relations     A: 0   B: 324    ✗ false — inputs and outputs give it these
   Justifications        A: 0   B: 24     ✓ true, and the only one that matters
   W3C PROV-JSON         A: no  B: yes    ✗ false — it serialises fine
```

One true zero surrounded by four bad ones. The bad ones were actively harmful: they made
the true one look like more of the same.

---

## What we built

A real callback baseline. One, shared between two experiments that were going to need two:

```
                    ┌──────────────────────────────┐
                    │  the six agents, UNCHANGED   │
                    │  (not modified for this)     │
                    └──────────┬───────────────────┘
                               │ every step, as it finishes
                               ▼
                    ┌──────────────────────────────┐
                    │  StepEvent                   │
                    │  ── what a callback handler  │
                    │     is handed, and no more   │
                    │   · node name                │
                    │   · start / end              │
                    │   · input payloads           │
                    │   · output payloads          │
                    │   · the error, if it raised  │
                    └──────────┬───────────────────┘
                               │
                 ┌─────────────┴─────────────┐
                 ▼                           ▼
        ┌──────────────────┐        ┌──────────────────┐
        │  E3              │        │  E6 condition A  │
        │  what A can do   │        │  what A costs    │
        └──────────────────┘        └──────────────────┘
```

Two design decisions carried the weight.

### 1. The restriction is enforced by the type, not by good behaviour

`StepEvent` has **no field** for a verdict, a justification, an activity type, or an entity
identifier. Not "we promised not to use them" — they are not there. A test asserts it.

This matters because the alternative is unfalsifiable. If the baseline *could* see the
justification and simply chose not to record it, the headline result would be a decision we
made, dressed up as a finding.

### 2. The baseline is deliberately generous — it is an **upper bound**

The temptation with a comparator is to write a weak one and win. So this one gets every
advantage that can be honestly given:

- the actual node boundaries, timings and payloads
- a hand-written label-to-type map, so it can produce *typed* activities
- permission to assert `wasDerivedFrom`
- permission to scrape a verdict out of a node's label

And one advantage that is more than honest: the event stream is built from **the
declarations the explicit instrumentation made** — the entity each step *said* it read, the
entity each step *said* it wrote. A real handler gets the node's whole input payload and has
to guess which part mattered.

That is stated prominently everywhere the table appears, because it changes what the result
means — for the better:

> The claim is not "callbacks recover less structure", which would depend on how good
> someone's handler is. The claim is that **even granted every structural fact explicit
> instrumentation declares, the rationale is still unreachable.** A bound beats a
> measurement of one implementation.

---

## What was measured

Same agents, same input, **the same single run** — the baseline observes while the real
instrumentation records.

| Signal | A, zero config | A, with type map | B |
|---|--:|--:|--:|
| Documents emitted | 24 | 24 | 24 |
| Activities | 96 | 96 | 96 |
| …of them typed | 0 | 96 | 96 |
| Entities | 54 | 54 | 78 |
| …of them typed | 0 | 0 | 78 |
| …carrying a resolvable pointer (`yp:pid`) | 0 | 0 | 8 |
| **Relations, total** | 324 | 324 | 324 |
| Decisions carrying `yp:verdict` | 0 | **24** | 24 |
| Decisions carrying **`yp:justification`** | **0** | **0** | **24** |
| Accepted by the reference `prov` parser | 24 | 24 | 24 |

**The baseline ties on structure.** Every activity, every relation, exactly. And it
serialises to valid W3C PROV-JSON, judged by the independent reference parser. Both of those
are concessions, and both make the comparison worth reading.

Three places a gap does appear:

**Entity identity.** A is handed *payloads*, not references to payloads. So it has to invent
identity by hashing content, which means two steps emitting equal values collapse into one
entity, and it types none of them. More pointedly: it carries **no `yp:pid` at all**. The
pointers-not-payloads design has no analogue in a reconstruction that never saw where
anything was stored.

```
   B says:  entity ToolResultEntity_example_0
            ├─ prov:value "example_0_metrics.png"
            ├─ yp:pid         21.T11999/yprovstore-89f4c3bfd486   ← fetchable
            └─ yp:contentHash sha256:4b0c...                      ← checkable

   A says:  entity cb:entity_a3f91c0e5b72        ← a hash of the string it saw
            └─ prov:value "example_0_metrics.png"
```

**Types cost something.** Zero configuration types nothing; ten hand-written label patterns
type everything. So A *can* have types — as a per-system artifact someone writes and
maintains, and only because these labels happen to be descriptive. A graph whose nodes are
`node_1`…`node_6` yields nothing. B declares the type at the call site.

**And the one that is the paper.**

---

## The verdict comes back. The reasoning does not.

This is the finding I did not expect, and it is better than what we set out to show.

A recovers **24 of 24 verdicts** — by scraping them out of node labels, because these agents
interpolate the verdict into the label (`"CA1: accept the stored record"`). That is a real
capability and it is reported as one, with its fragility stated: rename the node `decide`
and A gets nothing. A test covers exactly that case.

A recovers **0 of 24 justifications**, and this is *measured, not asserted*:

```
   Every justification in the explicit record  ──┐
                                                 │  searched for, verbatim, in
   Every string the handler was handed        ──┤  222 payloads · 12,425 characters
   (payloads AND node labels)                   │
                                                 ▼
                                            0 found
```

And a test guards the guard — it plants a justification in a payload and confirms the search
*does* find it. A check that can never fire proves nothing, which is a lesson this project
learned the hard way in Phase 1 (the 3σ rule that was mathematically unreachable).

**Why the asymmetry is the whole argument.** A verdict is a short token from a fixed
vocabulary; it survives in whatever strings happen to carry it. A justification is prose
composed *at the moment of decision*, out of evidence the step never returned to anyone.

### The worked example, both conditions, one run

To get an ESCALATE on the record we ran CA1 with **no consumers at all** and a 3-second
patience — a crashed-pipeline scenario. Both conditions observed it. Archived under
`experiments/fault-escalate`, because this is the only run in which the timeout path fires.

**What Strategy A recovered** (its decision node, in full):

```
yp:DecisionActivity   "CA1: escalate the stored record"
  prov:startTime      …
  prov:endTime        …
  yp:verdict          ESCALATE          ← scraped out of the label text
  cb:verdict_source   scraped-from-label
```

**What Strategy B recorded** at the same step:

```
yp:verdict                   ESCALATE
yp:persist_trigger           timeout
yp:consumers_required        3
yp:consumers_acknowledged    0
yp:escalation_reason         consumption-unconfirmed
yp:justification             ↓
```

> *"Persisted WITHOUT confirmed consumption: timeout after 3.0s; no acknowledgement from
> ['ca2', 'ca3', 'cax']. Acknowledged by [none]; still waiting on [ca2, ca3, cax] when the
> record was archived anyway to avoid losing it. The persist-after-consume ordering was not
> satisfied, so this record is archived but its consumption is unconfirmed. Record carries
> 9 activities / 39 entities / 55 relations, 21634 bytes. Integrity: round-trip read
> verified; sha-256 matched. Verdict ESCALATE."*

Read the gap operationally rather than aesthetically. **CA1 escalates for two independent
reasons, and they call for opposite responses:** a failed integrity check means the stored
bytes are wrong and the data is suspect; an unconfirmed consumption means the data is fine
and a consumer is down. A recovers that *something* escalated. It cannot recover which — so
it cannot tell "your storage is corrupting records" from "ca3 is not running", and an
on-call engineer handed A's record learns nothing actionable.

Everything that separates the two cases — the quorum set, the timeout that fired, the three
consumers that never answered, the integrity check that *passed*, and the decision to
archive anyway rather than lose the record — CA1 held in local variables, used to decide,
and returned to nobody. There is no callback, span, or post-hoc query that recovers that
sentence, because the sentence never existed outside the function that wrote it into the
record.

That is **capture, not retrieve**, demonstrated instead of argued.

---

## What changed, file by file

| File | What happened |
|---|---|
| `yprov4agenticai/observer.py` | **new** — `StepEvent` and the process-wide observation hook. Also the seam an OpenTelemetry bridge would attach to (Layer 1, not built) |
| `yprov4agenticai/api.py` | the scope notifies the observer on each step, passing values rather than identifiers |
| `yprov4agenticai/tracker.py` | `decide()` now routes through `ActivityScope` — one code path, and the observer sees decisions ran |
| `eval/baseline.py` | **new** — `CallbackLogger`, the comparison, and the rationale search |
| `eval/evaluate.py` | E3's hardcoded zeros replaced by the measurement; reports "not measured" rather than substituting an assumption when the stream is absent |
| `run_local.py` | `--baseline` attaches the reconstruction to the same run |
| `tests/test_baseline.py` | **new** — 16 tests, split between what A can do and what it structurally cannot |
| `docs/E6_OVERHEAD.md` | §4.1, §8 and §11 amended — §8 would have hard-coded the false assumption |
| `paper/OUTLINE.md` | E3 rewritten as measured; F5's drawing guidance changed |
| `.github/workflows/ci.yml` | the pipeline runs `--baseline`, and the rationale-unreachability test is a gate |

### One bug worth recording

`decide()` was not going through `ActivityScope`, so the observer never saw that the decision
steps ran — the baseline reported 72 activities instead of 96 and lost 24 `used` edges.

That is **unfairness in our own favour**, which is the one kind of error a baseline must not
have. A real callback handler absolutely sees a decision node execute; what it does not see
is what the node concluded or why. Fixed by routing `decide()` through the same scope as
every other step, which also means there is now one code path instead of two.

It is worth being clear that this was caught by looking at a number that seemed too good,
not by a test. Writing down what you expect before you measure is what makes that possible.

---

## How to check any of this yourself

```bash
# produce both conditions over one run
uv run python run_local.py --out-dir out --baseline

# the fault-injection case: no consumers, so CA1 times out and escalates
uv run python run_local.py --out-dir out.fault --agents ca1 --persist-timeout 3 --baseline

# the E3 table, measured
uv run python -m eval.baseline --prov-dir out --baseline-dir out/baseline

# the claim as a test, including the guard-the-guard case
uv run pytest tests/test_baseline.py -v

# and confirm attaching the baseline did not perturb what B emitted
uv run python -m eval.compare --before experiments/phase3-reference --after out --strict
```

`out/baseline/_events.json` is the raw observation stream — the actual artifact of
condition A. Every reconstruction in the table is a function of that file, which is why the
same recorded run can be reconstructed with and without a type map without re-running the
agents and hoping they behaved identically.

---

## Where this leaves the plan

Phase 3 was the plan's highest-leverage item and it is done. It fixed the most rejectable
table in the paper and supplied the overhead study's `A` condition from the same artifact,
so the two cannot drift apart.

**Phase 5 (E6) is now unblocked on the baseline**, and gains a useful property: because A
does more work than `docs/E6_OVERHEAD.md` §8 assumed, A will be *slower*, so the B−A gap
narrows — which is the honest number, and the one the paper wants. The sign of the
correction is in the paper's favour; it is reported either way.

Still open from the review: **A4** (nothing consumes `governance.*`, so reflexivity is one
level deep — a claim-scope decision for the author, not a bug), **B1** (justification
coverage is tautological as a headline — Phase 6), **E** (the `yp:` vocabulary artifact —
Phase 6/7), **F1–F4** (model and prompt provenance — Phase 4, next).
