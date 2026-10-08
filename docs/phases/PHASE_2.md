# Phase 2 — The documented API

> Follow-along notes. Plain language, with diagrams. The technical argument lives in
> [`../INSTRUMENTATION.md`](../INSTRUMENTATION.md) §3; this file explains what changed, why,
> and what to look at if you want to check it yourself.
>
> Previous: [`PHASE_1.md`](PHASE_1.md) · Plan: [`../../PLAN.md`](../../PLAN.md) Phase 2

---

## The problem in one sentence

The project's own documentation described an API — `step()`, `activity()`, `decide()`,
`emit()` — that **did not exist**. Anyone who read the design document and then opened the
code found a different library.

That was finding **A1** in the review. It matters more than a documentation slip, because
`docs/INSTRUMENTATION.md` §3 is the text that becomes §5 of the paper. A paper cannot
describe an interface its artifact does not provide.

```
  BEFORE                                   AFTER
  ──────                                   ─────

  docs: "a decorator, a context            docs: the same three things
         manager, and one call"                  ↕  (they now agree)
            ↕  ✗ disagree                  code: step() · activity() ·
  code: register_entity(...)                      decide() · emit()
        record_activity(...)                       ↓ built on
                                           code: register_entity(...)
                                                 record_activity(...)
```

The old primitives did not go away. They are the layer underneath, still public, and the
new calls are thin wrappers over them. That "thin" is a claim, and it is tested — more on
that below.

---

## What the three pieces do

The design rests on one idea, which the project calls **capture, not retrieve**: *the reason
for a decision is a local variable at the moment of decision.* Nothing watching from outside
can recover it afterwards, because it was never written down.

So the API splits along that line. Two of the pieces observe; one states.

### 1. `activity()` — a block that records itself

```python
with tracker.activity("ToolCallActivity", "render the chart") as prov:
    prov.consumes(task)                    # ← I read this
    png = render(spec)                     # ← the actual work
    prov.produces("ToolResultEntity", png.name, derived_from=task)   # ← I wrote this
```

The block measures its own start and duration, types itself, attributes itself to the
agent, and — importantly — records itself **even if the body raises**, stamping the
exception type before letting the error propagate.

That last part is not a nicety. A step that crashes and leaves no trace produces a record
with a *hole* in it, and a hole is indistinguishable from a step that never ran. An auditor
needs to see a failure, not an absence.

### 2. `step()` — the same thing, as a decorator

```python
@tracker.step("PlanningActivity")
def plan(task: str) -> str:
    ...
```

For functions. A function that needs to state its lineage asks for the scope by declaring a
parameter named `prov`, and gets it:

```python
@tracker.step("ToolCallActivity")
def run(payload: str, *, prov: ActivityScope) -> str:
    prov.consumes(task_entity)
    return tool.run(payload)
```

### 3. `decide()` — the one line a machine could not write

```python
tracker.decide(
    verdict="REVISE",
    justification="Self-consistency 0.41 < 0.70 threshold; the drafted efficiency claim "
                  "contradicts recorded execution times. Regenerating.",
    confidence=0.41,
)
```

**A blank justification raises an error.** This is the one deliberate rudeness in the API.
Every other field on an activity can be filled in by machinery, so the library fills them
in. This one cannot be, so the library refuses to let you skip it — at the call site, while
the reasoning is still sitting there in a variable.

### And `emit()` — hand the finished document somewhere

```python
tracker = ProvTracker(sid=session, sink=partial(write_prov, role, session))
...
return tracker.emit()        # serialise → give it to the sink → also return it
```

The "sink" is just a function. That is what keeps the library free of any opinion about
transport: a bus publish, a file write, an HTTP POST — all the same to it.

---

## The honest part: what is automatic and what is not

The temptation with a convenience layer is to make it look as if it does more than it can.
The review's biggest Phase 1 finding was exactly that failure mode — the tracker used to
*invent* lineage by assuming each record came from the one before it, producing 74 confident
wrong edges out of 250 — so this is the place it would come back.

```
   ┌──────────────────────────────────────────────────────────────┐
   │  What a wrapper can SEE          │  What it CANNOT see       │
   ├──────────────────────────────────┼───────────────────────────┤
   │  that the function ran           │  which value in scope it  │
   │  when it started                 │    treated as its input   │
   │  how long it took                │  what it considers its    │
   │  that it raised, and with what   │    output                 │
   │  which agent it belongs to       │  WHY it concluded what    │
   │  where it sits in the sequence   │    it concluded           │
   └──────────────────────────────────┴───────────────────────────┘
            ↓ automatic                         ↓ declared
     typing, timing, attribution,        used, wasGeneratedBy,
     ordering, failure capture           wasDerivedFrom, verdict,
                                         yp:justification
```

There is exactly one relation in the middle: `wasInformedBy`. PROV defines it precisely —
activity *a₂* was informed by *a₁* when *a₁* generated something *a₂* used — so it is
**computed** from declarations already made, not guessed. 72 of the run's 324 relations are
derived that way.

---

## Proving the wrappers are thin

A convenience layer that quietly changes the output would invalidate every number measured
before it. So the claim "the API adds no record kind of its own" is checked two ways.

**In a unit test.** The same six-step lifecycle is built twice — once through
`record_activity`, once through `activity()`/`decide()` — and the two documents are reduced
and compared. Same types, same relations, same verdicts, same justification text.

**On the whole system.** All six agents were moved onto the API, and the emitted provenance
was diffed against the frozen Phase 1 archive:

```
signal              before     after     delta
documents               24        24        +0
activities              96        96        +0
entities                78        78        +0
relations              324       324        +0
justifications          24        24        +0

No structural differences.
```

Twenty-four documents, zero structural change. That is what makes this a refactor rather
than a change of behaviour — and the CI gate now compares against the Phase 2 archive, so
it stays true.

---

## The thing we did not expect to find

While checking the port, one number looked wrong: **every activity in the record claimed to
have taken zero time.** All 96 of them. Not approximately zero — exactly.

The cause turned out to be the clock, not the code.

```
   The Windows system clock does not tick smoothly. It jumps,
   about every 15.6 milliseconds:

   clock value   │■■■■■■■■■■■■■■■■│■■■■■■■■■■■■■■■■│■■■■■■■■■■■■■■■■│
                  ↑              ↑
                  t0             t1        ← both readings land in the
                  (same value!)                same tick, so t1 - t0 = 0

   Meanwhile the actual work:
                  ├──┤  0.4 ms — real, measured, and invisible
```

Two `datetime.now()` calls either side of a sub-millisecond operation return the **same
value**. So the record said the step was instantaneous, which is indistinguishable from a
step nobody measured. (Verified directly: three consecutive `datetime.now()` calls on this
host returned one identical timestamp.)

The fix is standard practice and the API now does it everywhere: take the **start instant**
from the wall clock, because it has to be comparable with timestamps from other processes,
and take the **duration** from `time.perf_counter()`, which is monotonic and
nanosecond-resolution. The end instant is start + duration.

Same run, same agents, same graph:

| | activities recording zero duration | work attributed to the steps that did it |
|---|--:|--:|
| Before | **96 of 96** | 0.000 ms |
| After | **0 of 96** | 272.829 ms |

Per agent, after:

```
   ca2 (renders a PNG)        ██████████████████████████  239.021 ms
   ca1 (writes + re-reads)    █▌                           12.099 ms
   cax (summarises)           █▍                           11.290 ms
   ca3 (five algorithms)      █▏                            9.635 ms
   mc3 (audits)                                              0.454 ms
   mc1 (checks compliance)                                   0.330 ms
```

Why this matters beyond tidiness: **CA3 mines exactly these timestamps.** Its timing
algorithms look for where a run spent its time, and against a timeline of zero-length
events they can only see the gaps *between* steps, never the steps themselves. The overhead
study planned for Phase 5 has the same dependency. A record that cannot say how long
anything took cannot support a claim about cost.

A test now enforces it (`test_every_activity_has_a_measurable_extent`), so no agent can
regress to reading one clock twice.

---

## A side effect worth noticing: the archives are becoming a regression history

Each frozen archive now fails exactly the check its era had not yet met:

```
  experiments/baseline-pre-review   ✗ test_emitted_artifacts_are_valid_prov_json
                                      (0 of 24 documents parsed — Phase 1 fixed this)

  experiments/phase1-reference      ✓ valid PROV-JSON
                                    ✗ test_every_activity_has_a_measurable_extent
                                      (96 of 96 zero-duration — Phase 2 fixed this)

  experiments/phase2-reference      ✓ both
```

Nobody designed that. It falls out of freezing a hash-verified run at the end of each phase
and writing a test for each defect as it is found. It means the project can demonstrate not
just that it is correct now, but that each specific defect is genuinely gone.

---

## What changed, file by file

| File | What happened |
|---|---|
| `yprov4agenticai/api.py` | **new** — `ActivityScope` (where a step declares what it touched) and `Span` (the two-clock timing) |
| `yprov4agenticai/tracker.py` | the four API methods; `sink` and `verbose` as constructor options; `now_iso()` moved here as the system's one clock |
| `runtime/agents/*.py` | all six agents ported; work now happens *inside* the blocks that record it |
| `runtime/util.py` | re-exports `now_iso` from the library instead of defining a second one |
| `tests/test_capture_api.py` | **new** — 23 tests, including the raw-vs-API equivalence check |
| `tests/test_prov_conformance.py` | one test added: every activity must have a measurable extent |
| `docs/INSTRUMENTATION.md` | §3 rewritten against code that exists; §5's stale figures corrected |
| `.github/workflows/ci.yml` | the no-unannounced-change gate now compares against `phase2-reference` |

Two smaller things got fixed on the way, both in the "one definition of one thing" family:

* The runtime had its own `now_iso()`. Two clocks in one system is how timestamps end up
  disagreeing about ordering, so the library owns the definition now and the runtime
  imports it.
* CA3's justification said "Applied 5 algorithms" with the number typed as a literal, next
  to a list of their names typed separately. Both now come from one tuple, so they cannot
  drift apart from each other or from the code that runs.

---

## How to check any of this yourself

```bash
# the unit tests for the API, including the raw-vs-API equivalence proof
uv run pytest tests/test_capture_api.py -v

# run the whole demonstrator, then confirm the port changed nothing structural
uv run python run_local.py --out-dir out
uv run python -m eval.compare --before experiments/phase2-reference --after out --strict

# see the timing fix: this fails against Phase 1's archive, passes against Phase 2's
uv run pytest -m conformance --prov-dir experiments/phase1-reference
uv run pytest -m conformance --prov-dir experiments/phase2-reference
```

---

## Where this leaves the plan

Phase 2 is what **Phase 3** was waiting for. Phase 3 builds the callback-based baseline
(Strategy A) that the paper compares against, and that comparison is only fair if Strategy
B is measured through the interface a reader would actually use. It now exists.

It also unblocks [`PLAN.md`](../../PLAN.md) R4: the overhead study can time *both* layers
and report what the ergonomic wrapper costs over the raw calls — a fair question with, on
the evidence so far, a very small answer.

Still open from the review, unchanged by this phase: **A4** (nothing consumes
`governance.*`, so reflexivity is one level deep — a claim-scope decision, not a bug),
**B1** (justification coverage is tautological as a headline), **B2** (Phase 3),
**B3** (relation reporting, now partly addressed in the docs), **E** (the `yp:` vocabulary
artifact, Phase 6), **F1–F4** (model and prompt provenance, Phase 4).
