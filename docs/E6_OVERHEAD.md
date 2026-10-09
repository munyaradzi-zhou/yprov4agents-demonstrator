# E6 — Instrumentation overhead

> The one evaluation number the demonstrator does not yet produce.
> **Status:** specified, not yet implemented · **Target:** `eval/bench_overhead.py`
> **Paper slot:** §7, table E6 (see `paper/OUTLINE.md`)

---

## 1. Why this measurement exists

Every other result in the evaluation says what explicit instrumentation **gains**:
78 typed activities, 44 typed entities, 250 lineage relations, `yp:justification` on
24/24 decisions — against a structural zero for callback-level instrumentation.

E6 is the other side of the ledger. The first question any reviewer asks after E3 is:

> *"Strategy B captures more. What does it cost?"*

If we cannot answer that with a measured number, the contribution reads as a free lunch,
and reviewers do not believe in free lunches. A credible, modest, honestly-framed overhead
number is worth more than an impressive one, because it is the number that makes the rest
of the evaluation trustworthy.

**The claim E6 must support:** explicit instrumentation buys typed, reflexive, justified
provenance at a cost that is negligible for LLM-bearing agents and small and bounded for
deterministic ones.

---

## 2. The dishonest version of this experiment — and how to avoid it

The tempting measurement is: time `CAxSummariser.process()` with tracking on, then off,
and report the difference as a percentage.

That measurement is **worthless**, and worse, it flatters us. CAx is dominated by a GPT-4o
call of 500–3000 ms. Instrumentation costs microseconds. The honest result would be
"overhead ≈ 0.01%", which is true, meaningless, and will be read as evasion — because the
reviewer knows the LLM is hiding the cost.

Three rules follow, and they shape the whole design:

1. **Isolate instrumentation from the work it instruments.** Measure the tracker's own
   cost directly, in microseconds per call, not as a ratio against an LLM.
2. **Use deterministic agents for the ratio.** CA1, CA2 and CA3 (heuristic mode) have no
   network call to hide behind. A percentage against *their* runtime is a real percentage.
3. **Report the LLM ratio separately and label it as context, not as the result.** "On
   CAx, instrumentation is 0.02% of wall-clock" is a legitimate thing to say *after* the
   absolute cost is on the table, not instead of it.

---

## 3. Scope — what counts as "instrumentation cost"

Three distinct costs get conflated. The paper's claim is about the first; the other two
must be measured but reported separately, because they are optional or shared.

| Layer | What it is | In our code | Counts as instrumentation? |
|---|---|---|---|
| **L1 Tracking** | building the in-memory provenance model | `register_entity`, `record_activity`, `now_iso` | **Yes — this is the claim** |
| **L2 Emission** | turning the model into W3C PROV-JSON bytes | `to_prov_json`, `json.dumps` | Yes, but once per session, not per step |
| **L3 Persistence** | getting those bytes to disk / store / bus | `write_prov`, `store.put`, bus publish | **No** — optional (`TRANSIENT` mode) and shared with any approach |

Strategy A would also pay L2 and L3 to persist its logs, so including them in the headline
overstates our cost relative to the baseline. Keep them in the table, outside the headline.

---

## 4. Experimental conditions

### 4.1 The three instrumentation conditions

| ID | Condition | What runs |
|---|---|---|
| **OFF** | no provenance | agent business logic only — the true baseline |
| **A** | callback-style | `eval.baseline.CallbackLogger`, fed the `StepEvent` stream a framework callback handler receives — and permitted to reconstruct everything that stream supports |
| **B** | explicit (ours) | `ProvTracker` driven from inside each lifecycle step |

`OFF` is the denominator. `A` matters because without it the comparison is "instrumented vs
nothing", which nobody ships; the real question is what B costs *over* A.

### 4.2 The B sub-modes — the tunability result

The library exposes knobs. Showing their cost is a genuine contribution, because it tells a
user how to buy less provenance when they need less.

| Mode | Setting | Effect |
|---|---|---|
| B-DATA | `granularity="DATA"` | full PROV: activities, entities, `used`, `wasGeneratedBy`, `wasDerivedFrom` |
| B-META | `granularity="METADATA"` | activities only — no entity section, no entity-side relations |
| B-TRANS | `persistence="TRANSIENT"` | model built, nothing written to disk (isolates L3) — **implemented in Phase 4**; `emit()` suppresses the sink |
| B-JUST | justification + `extra_attrs` populated vs omitted | cost of the headline field itself |

**B-JUST is worth singling out.** "What does `yp:justification` cost?" is a question the
paper should answer with a number, since it is the thing being argued for.

**Selecting a mode is an environment variable, not a code edit.** `PROV_GRANULARITY` and
`PROV_PERSISTENCE` are read in one place (`runtime/util.py::prov_mode`) and applied by the
single factory all six agents construct their tracker through, so a sweep cannot end up
honoured by five agents and forgotten by the sixth. An unrecognised value fails at
start-up rather than inside a session already in flight.

Both modes are now exercised by the real pipeline rather than only by a unit test, which
was review finding F4: METADATA had never been executed by an agent, and TRANSIENT had no
implementation at all — it was stored, reported in the record's own bundle descriptor, and
did nothing. Measured through CA2 on a 6-activity record: METADATA drops the entity
section and the entity-side relations, keeps the activities, the agent attribution and
every justification, and is smaller by more than a tenth. **The justification survives in
the lean mode by design** — the point is to buy less data, never less reasoning, and if
METADATA shed justifications this table would stop being a comparison.

---

## 5. Three levels of measurement

### Level 1 — Microbenchmark (the primary result)

Times individual library calls in isolation. This produces the absolute, machine-independent-ish
numbers that make the claim concrete.

Operations to time separately:

| # | Operation | Why it is measured on its own |
|---|---|---|
| M1 | `now_iso()` | called **twice per activity** in our agents; `datetime.now().isoformat()` is not free and may be a surprising share of L1 |
| M2 | `register_entity(kind, value)` | plain path |
| M3 | `register_entity(..., pid=, content_hash=)` | pointer path — does the store pointer cost anything |
| M4 | `record_activity(...)` minimal | the core call |
| M5 | `record_activity(..., justification=...)` | **M5 − M4 = the cost of the headline field** |
| M6 | `record_activity(..., extra_attrs={...})` | typed attributes |
| M7 | `to_prov_json()` | L2 — serialisation of the whole model |
| M8 | `json.dumps(doc)` | bytes-out cost |
| M9 | `store.put(payload)` | L3 — sha-256 + file write |
| M10 | `write_prov(...)` | L3 — file I/O |

Protocol: ≥10 000 iterations per operation, 1 000 discarded as warm-up,
`time.perf_counter_ns()`, report **median, mean, p95, p99** in nanoseconds/microseconds.
Median is the headline (robust to OS scheduling noise); p99 shows the tail.

### Level 2 — Per-agent (the ratio result)

Runs one agent's full `process()` on a real record, under OFF / A / B.

Agents and what each one tells us:

| Agent | Mode | What it isolates |
|---|---|---|
| **CA2 Metrics_Visualizer** | deterministic | overhead against matplotlib rendering — real work, no network |
| **CA3 Analytics_Agent** | heuristic (no LLM) | overhead against pure computation over the graph — **the cleanest ratio** |
| **CA1 StorageAgent** | deterministic | overhead against hashing + I/O |
| **MC1 / MC3** | deterministic meta | overhead on the *governance* path, which is small and fast — likely the **worst-case ratio**, and therefore the one to report honestly |
| **CAx NL_Summarizer** | stub LLM **and** real LLM | stub = honest ratio; real = context only, clearly labelled |

Protocol: ≥30 repetitions per (agent × condition), interleaved rather than blocked (run
OFF,A,B,OFF,A,B… so that thermal drift and background load hit all conditions equally).
Report median wall-clock and the delta against OFF.

> **Note the likely headline:** the meta agents are the worst case, because their own work
> is trivial while they still emit a full typed record. Lead with that number. A paper that
> reports its own worst case is far more credible than one that reports its best.

### Level 3 — Pipeline (the systems result)

The whole demonstrator, end to end, instrumented vs not.

- **Throughput**: records/second through producer → 4 operational + 2 meta agents.
- **Latency**: first record in → last governance record out.
- **Bus load**: envelopes published, total bytes on the wire.
- **Peak RSS** per container/process.

Run with `run_local.py` for a clean single-process measurement (no broker, no container
scheduling noise), and optionally with docker-compose to show the transport cost separately.
Use `STREAM_DELAY_MS=0` so the artificial pacing does not dominate.

---

## 6. Metrics

| Metric | Unit | Level | Notes |
|---|---|---|---|
| Per-call latency | ns / µs | 1 | median, p95, p99 |
| Per-activity cost | µs | 1 | M4 + 2×M1, the realistic cost of one tracked step |
| Per-session cost | µs | 1–2 | 6 activities + entities + `to_prov_json` |
| Agent wall-clock | ms | 2 | median over ≥30 reps |
| Relative overhead | % | 2 | (B − OFF)/OFF, and (B − A)/A |
| Throughput | records/s | 3 | |
| Peak memory | MB | 2–3 | `tracemalloc` for the tracker; RSS for the process |
| Tracker state size | bytes | 2 | `len(acts)`, `len(ents)` → serialised size |
| **Output size** | bytes/record | 2 | **a real cost** — PROV-JSON emitted per record, storage and bus load |
| Bytes-per-justification | bytes | 2 | output-size delta for the headline field |

Output size matters as much as time at scale: at HPC rates the storage and bus cost of the
provenance is likelier to bind than the CPU cost of producing it.

---

## 7. Scaling study

Overhead must be shown to be **linear and bounded**, not quietly quadratic. A long-running
agent accumulates activities and entities, and `to_prov_json()` walks all of them —
`wasDerivedFrom` builds a chain over the entity order, `wasInformedBy` over the activities.

Measure `to_prov_json()` and total tracked cost at:

```
activities ∈ {10, 100, 1 000, 10 000}   × entities ∈ {10, 100, 1 000, 10 000}
```

Plot cost vs size, fit, and **state the complexity explicitly**. Expectation is O(A + E);
if the measurement shows otherwise, that is a bug worth finding before the paper, not after.

Also record memory growth across the same sweep — the tracker keeps everything in memory
until emission, which is a real constraint for a long-lived agent and should be stated.

---

## 8. Harness design

```
eval/
  bench_overhead.py      # entry point; --level micro|agent|pipeline|scaling|all
  bench_support.py       # timing helpers, stats, condition shims
out/
  bench/
    micro.json           # raw per-operation samples
    agent.json
    pipeline.json
    scaling.json
    E6_RESULTS.md        # rendered paper table
```

### Core timing helper

```python
def timeit(fn, *, iters=10_000, warmup=1_000):
    """Return {median, mean, p95, p99, n} in nanoseconds."""
    for _ in range(warmup):
        fn()
    samples = []
    for _ in range(iters):
        t0 = time.perf_counter_ns()
        fn()
        samples.append(time.perf_counter_ns() - t0)
    samples.sort()
    return {
        "median": samples[len(samples) // 2],
        "mean":   sum(samples) / len(samples),
        "p95":    samples[int(0.95 * len(samples))],
        "p99":    samples[int(0.99 * len(samples))],
        "n":      iters,
    }
```

### The OFF and A conditions

`OFF` requires the agents to run without a tracker. Rather than forking every agent, add a
**null tracker** with the identical interface and empty bodies, selected by environment
variable, so the measured difference is the tracker and nothing else:

```python
class NullTracker:
    def __init__(self, *a, **k): pass
    def register_entity(self, *a, **k): return ""
    def record_activity(self, *a, **k): return None
    def to_prov_json(self): return {}
```

`A` is `eval.baseline.CallbackLogger`, attached to the process-wide observation hook
(`yprov4agenticai.observer`). It receives one `StepEvent` per step — node name, start and
end instants, serialised inputs and outputs, and the error if the step raised — and
reconstructs as much W3C PROV as that stream supports.

**This paragraph previously said A "must not record justification, types, or lineage,
because the whole point of E3 is that it cannot." That was wrong on two of the three, and
building to it would have hard-coded the error into the measurement.** A callback handler
sees run trees with inputs, outputs and parents; activities, entities, `used`,
`wasGeneratedBy` and `wasInformedBy` follow mechanically, and with a label-to-type map so
do typed activities. Measured over the same run, A recovers 96 of 96 activities, all 324
relations, and — by scraping the verdict out of a node label — 24 of 24 verdicts.

What it recovers is **0 of 24 justifications**, and that is now a measurement rather than a
stipulation: every justification in the explicit record is searched for across every string
the handler was handed. See `eval/baseline.py` and the E3 table in `paper/OUTLINE.md`.

Two consequences for this study, both good:

- A does more work than this section assumed, so **A is slower**, so **B − A narrows** —
  which is the number the paper actually wants. §11's "deliberately generous to A" now
  means generous on capability as well as on timing.
- A and E3 are the same artifact. The overhead study and the capability comparison cannot
  drift apart, because there is only one baseline.

### Interleaving

Run conditions round-robin, not in blocks:

```python
for rep in range(N):
    for cond in ("OFF", "A", "B"):     # interleaved, not OFF×N then A×N then B×N
        record(cond, run_once(cond))
```

### Environment capture

Every results file must record: CPU model, core count, RAM, OS and version, Python version,
whether the machine was on AC power, and whether an LLM was involved. Without this the
numbers are not reproducible and a reviewer is entitled to discount them.

---

## 9. Predictions, and how they turned out

The predictions were written down before the harness existed, which is the only thing that
makes them worth scoring. Results in [`E6_RESULTS.md`](E6_RESULTS.md).

| Quantity | Predicted | Measured | Verdict |
|---|---|---|---|
| `record_activity` | 1–5 µs | ~1.7 µs | ✅ |
| `now_iso` | 0.5–2 µs | ~2 µs, **and more than `record_activity` itself** | ⚠️ at the top of the range, and the warning fired |
| Justification cost (M5 − M4) | near zero in time; tens-to-hundreds of bytes | **below what the machine can resolve**; 278 bytes, 21.7% of a record | ✅ emphatically |
| `to_prov_json` (6 activities) | 10–100 µs | ~25 µs — *after* fixing a 6.78 ms defect it exposed | ✅, eventually |
| Per-session L1+L2 total | well under 1 ms | 50–85 µs | ✅ |
| Deterministic agent overhead | low single-digit % | **20–57%**, almost all of it the file write | ❌ the prediction was wrong |
| Meta-agent overhead | highest ratio, possibly 10%+ | **+1450% to +1700%** | ✅ direction, badly wrong magnitude |
| LLM agent overhead | <0.1% | inside the noise, as predicted | ✅ |
| Pipeline throughput | bounded by the LLM, not the tracker | inside the noise; bounded by a fixed persist window | ✅ for the wrong reason |
| Scaling | O(A + E) | **1.47 before a fix, 1.04 after** | ❌ then ✅ |

**The prediction that mattered held.** `yp:justification` costs nothing measurable in time
and 278 bytes in storage, so the trade is **storage for explainability, not speed for
explainability**. §9 called this "almost certainly what the data will show" and it is — but
it is worth more now, because the justifications have since been made to *compute* something
(`PLAN.md` R1) and the result survived that change.

**Two predictions were wrong, and both were wrong in the same way.** Agent-level overhead
came in at twenty to seventeen hundred percent rather than single digits, and the scaling
exponent was 1.47 rather than 1.0. Both were wrong because they reasoned about building a
provenance record and the costs were somewhere else: in one `write_text` call, and in a
list scan nobody had looked at. A prediction about a system's cost that is not checked
against the system is a guess about where the author thinks the cost is.

**And the warning in the `now_iso` row fired.** It said: *if it rivals `record_activity`,
cache the clock or pass timestamps in.* It does rival it — it exceeds it — and the
decomposition says why: 80% of it is `.isoformat()`, not reading the clock. That is a
finding about PROV's record format rather than about this library, and it is the most
transferable thing in the study for anyone else building one.

---

## 10. Reporting — the paper table

**The tables are rendered, not transcribed.** They live in
[`E6_RESULTS.md`](E6_RESULTS.md), generated by `python -m eval.bench_report` from the
results files under `out/bench/`. Nothing in that document is typed in by hand, which is
the only way a results table stays true after its third revision.

| Table | What it answers | Where the figure comes from |
|---|---|---|
| **E6a** | what one library call costs | `micro.json` |
| **E6b** | what one agent's step costs, and how much of that is the write | `agent.json` |
| **E6c** | what each tunable mode buys and costs | `modes.json` |
| **E6d** | whether the cost stays linear as a graph grows | `scaling.json` |
| **E6e** | what the whole pipeline costs *(sequential — see [R8](../PLAN.md#r8))* | `pipeline.json` |
| **E6f** | which of the above figures survive a change of occasion | two results directories |

E6f was not in the original plan and earns its place. §12 asks for a second occasion to
confirm stability; what it confirms is that the absolute timings move by up to 44% between
occasions while the byte counts are identical and the complexity exponents agree to 2%. A
reader needs to be told which columns to rely on, and that is a measurement rather than a
disclaimer.

Three deviations from the table shapes sketched above, each because the measurement said so:

- **E6b gained a B-TRANS column.** "B costs 40% more than OFF" is unactionable until it is
  split into what the record costs to build and what it costs to store. It is almost
  entirely the latter.
- **E6a reports a timer floor and a resolvability verdict per figure.** Several operations
  cost less than two clock reads, and a difference smaller than the noise it was measured
  against is reported as unresolvable rather than as a number.
- **"Cost of one tracked step" is M4 + 1×M1, not M4 + 2×M1.** Phase 2's `Span` reads the
  wall clock once per step rather than twice, and derives the end instant monotonically.

---

## 11. Threats to validity — state these in the paper

- **Single machine, single OS.** Absolute microsecond figures are machine-specific; the
  *ratios* and the *complexity class* are the transferable results.
- **Python.** A compiled implementation would shift the absolute numbers substantially. Say so.
- **Small documents.** Two yProv4ML records of ~9 activities / 39 entities. The scaling
  study exists precisely to cover this, but real HPC runs are larger and untested.
- **Network and LLM variance** dominate any wall-clock figure involving CAx; this is why
  those rows are labelled context. The record now states the model, sampling parameters,
  token counts and measured latency per call, so a reader can see how much of a CAx figure
  is the model rather than the instrumentation.
- **Energy is off during timing runs, deliberately.** A sampling backend has a cost of its
  own and no run that is measuring time should pay it (`PROV_ENERGY` defaults to off).
  There is a sharper reason not to report per-step energy at all: codecarbon's default
  sampling period is 15 seconds and these steps last milliseconds, so a delta lands on
  whichever step read after a tick. Measured here: a step of 0.000 s was handed 46.9 µWh
  while the rendering step that did the work was handed 4.6 µWh. The emitted record
  discloses this (`yp:energy_sampling_period_s`, `yp:energy_attributable`) rather than
  presenting the figure as an attribution. **Energy belongs at session granularity**, and
  any per-step energy claim needs a backend sampling faster than the steps.
- **The A baseline is an upper bound, not a specific framework's handler.** It is our own
  reconstruction, and the event stream it consumes is built from the declarations the
  explicit instrumentation made — the entity each step said it read, the entity each step
  said it wrote. A real handler is handed a node's whole input payload and has to guess
  which part mattered; it would over-report `used` and could not separate a load-bearing
  input from an incidental one. Conceding A every structural fact B declared is deliberate:
  it makes the capability claim a statement about the ceiling rather than about one
  implementation's quality. State it this way in the paper — "even granted every structural
  fact explicit instrumentation declares" is a stronger sentence than any comparison
  against a handler someone could call unfair.
- **A's timing is correspondingly optimistic.** It appends an event per step and does its
  reconstruction offline, so the measured A cost excludes the serialisation a real handler
  performs inline. B − A is therefore an upper bound on the difference, in B's disfavour.
- **In-process bus** for Level 3; broker and container overheads are measured separately and
  are not instrumentation cost.

---

## 12. How to run

```bash
cd yprov4agents-demonstrator
uv sync --extra eval --extra dev

python -m eval.bench_overhead --level micro                  # Level 1
python -m eval.bench_overhead --level agent --repetitions 30  # Level 2
python -m eval.bench_overhead --level scaling                 # §7 sweep
python -m eval.bench_overhead --level modes                    # E6c
python -m eval.bench_overhead --level pipeline                 # Level 3
python -m eval.bench_overhead --level all --repetitions 30      # everything

python -m eval.bench_report                                   # render E6_RESULTS.md
python -m eval.bench_report --bench-dir out/bench-occasion2 \
       --compare out/bench-occasion1                           # add E6f
```

Measurement hygiene: close other applications, mains power rather than battery, and run the
whole suite on at least two separate occasions before the numbers go anywhere near the
paper. Keep `PROV_ENERGY` off — a sampling backend has a cost of its own and no timing run
should be paying it. The harness refuses to run quietly under a profiler: it prints a
warning if a trace hook is installed, because that would measure the tool.

**A benchmark that has never been wrong has never been checked.** This one found two
library defects (§9) and three defects in itself: a mode table that reported METADATA as
slower than DATA, a write-cost column that printed a negative, and a difference computed by
subtracting two separately-taken measurements of operations a few hundred nanoseconds
apart. All three produced plausible numbers. `tests/test_bench.py` exists because of the
third one — in particular, it checks that timing the *same* function twice is reported as
no difference.

---

## 13. Implementation checklist

- [x] `NullTracker` with the ProvTracker interface, selected by env var
      (`yprov4agenticai/null.py`; `PROV_INSTRUMENTATION=off`, and it is a product feature
      rather than benchmark scaffolding — a user who wants no provenance wants this object)
- [x] `CallbackLogger` (condition A) — built in Phase 3 and shared with E3, which is why
      the two cannot drift apart. A null tracker still *notifies*, so OFF and A differ only
      by an installed observer
- [x] `bench_support.py` — `timeit`, percentile stats, environment capture, **plus** a
      measured timer floor, robust dispersion, paired differences, and a complexity fit
- [x] Level 1: M1–M12 microbenchmarks (M11 `store.resolve()` per [R6](../PLAN.md#r6);
      M12 wrapper-vs-primitives per [R4](../PLAN.md#r4); M1 decomposed into clock and
      formatting)
- [x] Level 2: per-agent OFF/A/**B-TRANS**/B, interleaved, 30 reps
- [x] Level 2: output-size measurement per condition
- [x] §7 scaling sweep + complexity fit — **found a quadratic defect**
- [x] Level 3: pipeline wall clock, records/s, documents and bytes written
- [x] Mode comparison: DATA / METADATA / TRANSIENT / justification on-off
- [x] `E6_RESULTS.md` renderer producing tables E6a–E6f
- [x] Re-run on a second occasion; stability reported as E6f
- [x] `paper/OUTLINE.md` §E6 written from the rendered results

Not done, and deliberately: **peak RSS per process** and **bus-load byte counts** at Level
3. The pipeline figure is inside the noise for time, so a memory figure attached to it
would be decoration; `tracemalloc` peak memory is measured where it means something, in the
scaling sweep (§7), which is the case a long-lived agent actually faces.
