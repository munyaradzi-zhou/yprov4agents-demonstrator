# Phase 5 — What it costs

> Follow-along notes. Plain language, with diagrams. The rendered tables are
> [`../E6_RESULTS.md`](../E6_RESULTS.md); the specification and the scored predictions are
> [`../E6_OVERHEAD.md`](../E6_OVERHEAD.md).
>
> Previous: [`PHASE_4.md`](PHASE_4.md) · Plan: [`../../PLAN.md`](../../PLAN.md) Phase 5

---

## The question

The project argues that agents should record *why* they decided what they decided. The
obvious objection is: **what does that cost?**

If the answer is "a lot", the contribution is academic. So this phase measures it — at four
scales, under four conditions, twice.

```
   micro     one library call                 "what does record_activity() cost?"
   agent     one agent's whole step           "what does instrumenting CA3 cost?"
   scaling   a record of 10 → 10,000 steps    "does it stay linear?"
   pipeline  the whole six-agent system       "what does the system cost?"
```

---

## The answer, in one table

| | |
|---|---|
| Recording a `yp:justification` | **nothing measurable** (26 ns against ±352 ns of noise) |
| Storing one | **278 bytes — 21.7% of a record** |
| Building a complete typed record | ~40 µs |
| **Writing that record to a disk** | **~1,500 µs** |

The trade is **storage for explainability, not speed for explainability.** That was the
prediction written down before the harness existed, and it held.

And the thing nobody predicted: **almost all of the measurable overhead is one file
write.**

---

## The three conditions, and why they fall out cleanly

The study needs to compare "no provenance", "callback-level provenance" and "ours". The
clean way to do that is to change *one* thing at a time:

```
   ┌──────────┬────────────────────┬──────────────────────┐
   │condition │ tracker            │ observer             │
   ├──────────┼────────────────────┼──────────────────────┤
   │ OFF      │ NullTracker        │ none                 │
   │ A        │ NullTracker        │ CallbackLogger       │  ← Phase 3's baseline
   │ B-TRANS  │ ProvTracker        │ none, nothing written│  ← Phase 4's mode
   │ B        │ ProvTracker        │ none                 │
   └──────────┴────────────────────┴──────────────────────┘
          the six agents are IDENTICAL in all four
```

`NullTracker` accepts every call `ProvTracker` accepts and records nothing. It lives in the
library, not in the benchmark, for a reason worth stating: **"run with no provenance" is a
product feature**, not test scaffolding. A user who wants zero overhead wants exactly this
object, and a shim that exists only inside a benchmark measures the shim.

A then falls out for free, and *correctly*: a framework still calls the nodes under
condition A, so a callback handler still sees them. A null tracker therefore still
*notifies* while building nothing. Modelling it any other way would have made the baseline
weaker than a real handler — the one thing a baseline must never be.

A test compares the two classes member by member, so OFF cannot quietly drift into being a
different code path.

---

## Getting the measurement right was most of the work

Three times the harness produced a plausible, wrong number. Each is worth recording,
because each is a mistake that is easy to ship.

### 1. The clock is not free, and several operations cost less than reading it

```
   reading the clock twice around an empty call:  ~200 ns   ← the floor
   register_entity():                             ~570 ns   ← under 3× the floor
```

A benchmark that reports 570 ns without reporting that its floor is 200 ns is not reporting
anything. So the harness **measures its own floor every run** and carries it with every
figure.

For the operations below a microsecond it also measures in **batches of 1,000** — one clock
pair spanning a thousand calls, which drops the per-call floor to 0.2 ns. The cost is that
the *tail* of a batched measurement is a tail of batch means, which is narrower than
reality. So those operations are measured **twice**: batched for a median the clock can
resolve, unbatched for a p95 and p99 that mean what a reader will take them to mean.

### 2. Subtracting two separate measurements does not work

The headline figure is a difference: `record_activity(justification=…)` minus
`record_activity()`. Measured as two separate runs and subtracted, it came out:

```
   attempt 1:   +52 ns
   attempt 2:   −30 ns     ← negative
   attempt 3:  +201 ns
```

The quantity is a few tens of nanoseconds; the gap between two measurements taken a second
apart on a busy laptop is larger than that. **The sign was not even stable.**

The fix is the one the agent level already used — interleave:

```
   one round:   [1000 × baseline] [1000 × variant]   → difference taken INSIDE the round
   repeat 40 times, report the median difference and its spread
```

Whatever the machine was doing applies to both halves. The honest result then arrives:
**26 ns against a round-to-round spread of ±352 ns — below what this machine can measure.**

"Below the noise" is a *better* result than "52 ns", because 52 ns was never real.

### 3. A ratio against noise is not a ratio

The first agent table reported CA2's instrumentation as a **5% speed-up**. CA2 renders a
PNG with matplotlib: its own work is ~110 ms and its instrumentation is ~2 ms, so thirty
samples cannot resolve the difference.

Now every ratio is checked against the dispersion of the samples it came from, and prints
*in noise* when it fails. Three figures in the final tables say that, and they are the
honest entries.

> A benchmark that has never been wrong has never been checked. `tests/test_bench.py`
> exists because of mistake 2 — it checks, among other things, that timing the *same*
> function twice is reported as **no difference**.

---

## What it found in the library

Two defects. Both were invisible at every scale the tests and the demonstrator use, both
produced perfectly correct output, and both would have shipped as claims.

### `to_prov_json()` was reading the disk on every call

Level 1's very first number was wrong by two orders of magnitude: 6.19 ms to serialise a
six-activity document, against a prediction of 10–100 µs.

```
   _library_version()    6.78 ms   ← reads installed package metadata, from disk
   to_prov_json()        7.14 ms
                         ──────
   so 95% of serialising a provenance record was a metadata lookup
```

Called once per emitted record, per agent, per message. A `functools.cache` fixes it — the
installed version of a package cannot change under a running interpreter:

**6.78 ms → 23 µs. 306×.**

### Recording a derivation was quadratic

The scaling sweep exists to check one claim: that cost grows linearly with the size of the
graph. It did not.

```
   fitted exponent on building a record:   1.468     ← superlinear
   a 10,000-activity record:               1.72 s
```

One line:

```python
if source and (source, eid) not in self.derivations:   # a list scan: O(n) each, O(n²) total
```

The same class already solved this three fields away, with a set alongside the list, for
memory ids. It just had not been applied here.

**1.72 s → 131 ms. Exponent 1.468 → 1.066.**

This is the one that matters most. At six activities — the scale of every test and every
demo — it is unmeasurable. It would have reached the paper as an unqualified O(A+E) claim.
`E6_OVERHEAD.md` §7 said: *state the complexity explicitly; if it is not O(A+E) that is a
bug worth finding before the paper, not after.* It was, and it was.

**Neither defect changed a single byte of emitted provenance.** The CI gate that compares
every run against a frozen archive is what establishes that, and it passed unchanged
through both fixes.

---

## Where the overhead actually is

```
  MC1 Compliance — the worst case in the system

  its own work        ████  96 µs
  + build the record  ██    42 µs
  + write it to disk  ████████████████████████████████████████  1,532 µs
                                                                 ──────────
                                                        total   +1,645%

  96 + 42 = 138 µs of work and provenance.
  Then 1,532 µs to put it on a disk.
```

Read that honestly and it says two things at once. **Yes, the worst case is +1,645%** — a
meta agent's own work is trivial while it still emits a complete typed record, and a paper
that leads with its own worst case is worth more than one that leads with its best.

**And 97% of it is a `write_text` call.** Explicit instrumentation is not expensive to
compute; persisting its output is. That is a deployment decision, and Phase 4's modes make
it:

| | time | bytes |
|---|--:|--:|
| full | — | — |
| `TRANSIENT` (build, don't write) | **−91%** | −0% |
| `METADATA` (activities only) | −3% | **−37.6%** |

Two independent measurements — the agent decomposition and the mode comparison — reach the
same conclusion from different directions. That agreement is worth more than either number.

---

## The surprise: the timestamp dominates

```
   one tracked step  ≈ 4.5 µs
   ├── record_activity()   ~1.6 µs
   └── now_iso()           ~2.9 µs     ← 64% of the step
       ├── read the clock      ~0.5 µs
       └── format as ISO-8601  ~1.6 µs  ← 3× the reading it formats
```

The most expensive part of recording a step is **turning an instant into a string**. Not
building the graph, not the justification, not the typing — producing text.

That is a property of what W3C PROV stores, not of this library. Anyone implementing a
provenance library in a hot path should know it, and `E6_OVERHEAD.md` §9 had actually
flagged the possibility in advance: *"if it rivals `record_activity`, cache the clock or
pass timestamps in."* It does rival it. It exceeds it.

---

## Which numbers a reader should trust

Run the whole study twice on separate occasions, and compare:

```
   One tracked step          3.57 µs → 4.49 µs    +25.7%   ← don't quote this
   Six-step session         52.13 µs → 62.73 µs   +20.3%   ← or this
   Bytes per justification       278 → 278         +0.0%   ← quote this
   METADATA output             4,800 → 4,800       +0.0%   ← and this
   Exponent: build             1.077 → 1.066       −1.0%   ← and this
```

Absolute timings move by a quarter on the same machine with the same code. Byte counts are
**identical to the byte**. Exponents agree to 1%.

§11 predicted this — "the ratios and the complexity class are the transferable results" —
and now it is measured rather than asserted, which lets the results document tell a reader
which columns to rely on. That table is E6f, and it was not in the original plan.

---

## What changed, file by file

| File | What happened |
|---|---|
| `eval/bench_support.py` | **new** — timing with a measured floor, batching, robust dispersion, paired differences, complexity fit, environment capture |
| `eval/bench_overhead.py` | **new** — the four levels and the four conditions |
| `eval/bench_report.py` | **new** — renders E6a–E6f from the results files; nothing typed by hand |
| `yprov4agenticai/null.py` | **new** — the off switch, in the library because it is a feature |
| `yprov4agenticai/tracker.py` | the two performance defects fixed |
| `runtime/util.py` | `PROV_INSTRUMENTATION=off` selects the null tracker |
| `tests/test_bench.py` | **new** — 21 tests, including that identical work reports no difference |
| `docs/E6_RESULTS.md` | **new**, generated |
| `docs/E6_OVERHEAD.md` | §9 scores the predictions; §10, §12, §13 updated; §11 unchanged and still right |
| `paper/OUTLINE.md` | E6 written from the measurements |

---

## How to run it yourself

```bash
uv sync --extra eval --extra dev

python -m eval.bench_overhead --level all --repetitions 30
python -m eval.bench_report                      # → docs/E6_RESULTS.md

# the stability section needs two occasions
python -m eval.bench_overhead --level all --out-dir out/bench-occasion1
python -m eval.bench_report --bench-dir out/bench --compare out/bench-occasion1
```

Hygiene: mains power, other applications closed, `PROV_ENERGY` off. The harness warns if a
trace hook is installed, because then it would be measuring the profiler.

Both occasions' raw results are archived under `experiments/phase5-overhead/bench/` and
`.../bench-occasion1/`, with the manifest's hashes, so the rendered tables can be
regenerated from the evidence rather than trusted.

---

## Where this leaves the plan

Phase 5 was the long pole and it is done, ahead of the 25 October deadline
[`PLAN.md`](../../PLAN.md) set for starting it.

**Phase 6 is the last one**: the `yp:` vocabulary as a real artifact (contribution C3
currently exists only as strings inside f-strings), reframing E2 with a denominator that
isn't tautological, splitting the relation reporting, and a full documentation truth-pass.

One decision there is the author's rather than the implementer's: **A4** — nothing consumes
`governance.*`, so the reflexivity in contribution C2 is one level deep. It can be
implemented as a real loop or narrowed in the text, and E4 reads differently either way.
