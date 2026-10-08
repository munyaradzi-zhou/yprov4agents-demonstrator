# Phase 1 — Truth in the record

**Done on:** 8 October 2026 · **Commits:** `9e7de94` … `df4c9ff` (6) · **Tests:** 63 passing, 1 known-open

Plain-language companion to Phase 1 of [`PLAN.md`](../../PLAN.md). Phase 0 built the safety
net; this phase used it to fix everything that changed **what the provenance says**.

---

## 1. The one-sentence version

Before Phase 1, the demonstrator produced provenance that no standards-compliant tool could
read, containing relations nobody had observed, asserting conclusions nobody had checked,
and pointing at payloads nobody could fetch. All four are now fixed, and each is backed by
a test that fails if it regresses.

```
                        before            after
  valid W3C PROV-JSON   0 of 24           24 of 24
  PIDs dereferenceable  0 of 8            8 of 8
  verdict kinds seen    3                 6
  fabricated relations  74 of 250         0
  justifications that
    vary by outcome     2 of 24           24 of 24
```

---

## 2. What was wrong, and what each fix was

### 2.1 Nothing we emitted was readable by anyone else

The document root carried a key called `_yprov4agents_meta`. PROV-JSON treats every
top-level key as a record type and **rejects the whole document** when it meets one it
doesn't recognise — it does not skip it. So the interoperability claim was not merely
weak, it was inverted: every file was unreadable to every conforming tool.

The information it held still matters (a reader needs to know whether entities were
captured at all), so it moved to where the format allows it. PROV has an idiom for exactly
this: a *bundle* is a named set of provenance descriptions, and is itself an entity.

```
   before  (rejected)                   after  (accepted)
   ┌───────────────────────┐            ┌───────────────────────┐
   │ prefix                │            │ prefix                │
   │ agent                 │            │ agent                 │
   │ activity              │            │ activity              │
   │ entity                │            │ entity                │
   │ _yprov4agents_meta ✗  │            │   └─ yp:bundle_… ──┐  │
   └───────────────────────┘            │      prov:Bundle   │  │
                                        │      granularity ◀─┘  │
   ProvJSONException:                   │      persistence      │
   not a recognised PROV-N              │      library_version  │
   record-type keyword                  └───────────────────────┘
```

Entity values also moved from `yp:value` to the standard `prov:value` — a conforming reader
looks for the latter, so every entity previously appeared to have no value at all.

### 2.2 The integrity check could not fail

CA1 archived each record and then "verified" it by reading it back. But the store returned
the copy it had just cached in memory and re-hashed *that*, so the check compared a string
with itself.

```
   before                               after
   put("…")  ──▶ memory ──┐             put("…")  ──▶ disk ──┐
                          │                         ▲        │
   resolve() ◀────────────┘             resolve() ──┘        │
   hash(same object) — always matches   hash(bytes read back)┘
                                        — can actually differ
```

This mattered because `ACCEPT` versus `ESCALATE` turned entirely on that clause. The
verdict was unfalsifiable. Now the store reads the file, and a test that corrupts a blob on
disk proves the check has teeth.

**A trap found along the way.** `put()` hashed the text in memory (one byte per line break)
and then wrote it in text mode (two bytes on Windows). For any multi-line payload the
recorded hash described bytes that were never written. It was dormant only because every
payload so far happened to be single-line — and it would have detonated the moment the
fix above made the hash actually get checked, looking exactly like that fix being wrong.
All store I/O is now binary, tested on both CI platforms.

### 2.3 Pointers that pointed nowhere

The design puts a pointer in the graph and the payload in a store. That is only sound if
the pointer resolves — otherwise it reads as evidence while supplying none.

Four agents share one store directory. Each kept a private index, and only CA1 ever saved
it, so the index on disk held whichever agent finished last. Eight PIDs were cited; two
were listed; none could be fetched by a fresh process, because the store had no way to read
an index it hadn't written.

```
   before                                    after
   ca1 ─┐                                    ca1 ─┐
   ca2 ─┼─▶ _manifest.json  (last writer     ca2 ─┼─▶ <hash>.txt      payload
   ca3 ─┤     overwrites)      wins)         ca3 ─┤   <hash>.meta.json  sidecar
   cax ─┘                                    cax ─┘        │
                                                           ▼
   2 of 8 listed, 0 resolvable                _manifest.json  (rebuilt from
                                                               the sidecars)
                                              8 of 8 resolvable, cold start
```

Each write now touches only files named after its own content hash, so there is nothing to
lock and nothing to clobber.

### 2.4 Relations nobody had observed

This was the most serious finding, because relations *are* a provenance system's claims.

The tracker asserted that each entity was derived from whichever entity was registered
before it, and each activity informed by whichever activity was recorded before it. Both
rules produce exactly n−1 edges for n items no matter what the agent did. Two unrelated
entities recorded in sequence produced a claim that one came from the other.

Derivation is now **declared** by the agent. Informed-by is now **derived**, because PROV
defines it precisely — *a2* was informed by *a1* when *a1* generated an entity *a2* used —
so it is computable from dataflow rather than something anyone has to assert.

The correction changes the shape of the graph, not just the count:

```
   claimed before (index order)          actual dataflow (now)

   Receive                               Receive
     │                                     │ TaskEntity
     ▼                                   ┌─┴─┐
   Planning                              ▼   ▼
     │                               Planning ToolCall
     ▼                                        │ ResponseEntity
   ToolCall                                 ┌─┴─┐
     │                                      ▼   ▼
     ▼                              Observation Response
   Observation                            │ QualityEntity
     │                                     ▼
     ▼                                  Decision
   Decision
     │                            Planning and ToolCall are siblings —
     ▼                            both read the task. Response reads the
   Response                       summary, not the decision.
```

The old chain asserted dependencies that weren't there and missed the structure that was.

### 2.5 Justifications that said the same thing regardless

CAx claimed its summary was "grounded in the graph counts" — without consulting the graph.
It was restating its own prompt. CA2 said "accept chart" without saying which of two
materially different charts it had made.

Both now check what they claim:

- **CAx** matches every figure of ten or more in the summary against the graph's own
  counts. An unsupported figure gives `REVISE` and names it. The justification also states
  the limit of what this shows — no invented figures, *not* completeness — because a
  one-sided check shouldn't be reported as a guarantee.
- **CA2** records which chart exists. It has always had two branches: plot the graph's
  numeric values, or fall back to graph composition. The fallback is what the real
  yProv4ML records actually take, and that was invisible. It is now `REVISE` — the chart is
  valid but describes the record rather than the run.

### 2.6 The compliance checker failed its own check

MC1 demanded that every agent record a justified decision step, and recorded none itself.
Applying MC1 to MC1 is a one-line experiment, and it failed. MC3 was in the same position,
and worse: an auditor with no verdict can never report a problem.

Both now separate doing the work from concluding about it. MC3's verdict is substantive: a
trail is only a trail if its steps can be ordered, so `COMPLETE` requires every activity to
carry a start *and* end time, else `PARTIAL` naming the gap. On real input this matters —
yProv4ML declares start times for 8 of 9 activities with no end time.

### 2.7 "Persist after consume" was a race, not a guarantee

CA1's timeout guard was never cancelled, and the code yields to the event loop while a
consumer works — so the guard *competed* with the consumers instead of backing them up.

```
   advertised                     what happened
   ──────────                     ─────────────
   consumers finish               consumers ─┐
        ↓                                    ├─ whoever finishes first wins
   CA1 archives                   timeout  ──┘
   (timeout only if one dies)
```

At a 6-second timeout one record was archived with **zero** acknowledgements while another
in the same run reached full quorum — and both reported `ACCEPT`.

Quorum now stands the guard down. And the verdict distinguishes the two cases: archiving
without confirmed consumption forfeits the ordering the whole architecture is built around,
so it escalates rather than passing silently.

This is now the project's best worked example, because the same code produces genuinely
different records:

```
  example_0  ESCALATE  via timeout  2/3 acknowledged  consumption-unconfirmed
  example_2  ACCEPT    via quorum   3/3 acknowledged  —
```

### 2.8 Five defects in the analytics agent

The worst was a check that **could never fire**. The anomaly detector used a 3-sigma rule,
but with a population standard deviation over *n* samples the largest attainable score is
(n−1)/√n — so 3σ needs at least 11 gaps. Every graph in the repository has fewer. The check
always passed, silently, which is the worst thing a check can do.

Replaced with a median-based rule, which stays attainable on small samples. Two further
problems appeared while testing it, both real:

- The median absolute deviation is **exactly zero** whenever more than half the gaps are
  identical — the normal case, since producers log at a fixed cadence then stall once. A
  zero scale rejected precisely the outlier being looked for.
- A near-zero median makes *everything* an outlier, so scores reached seven figures and
  sub-second noise was reported beside a 103-second stall.

Both fixed, and the result is now worth reading:

```
  example_0  gap before 'Training'                 5.049s   64.7% of a   7.810s run
  example_2  gap before 'CrossEntropyLoss/Training' 102.870s 91.4% of a 112.530s run
```

CA3 identifies where each run spent its time, from provenance alone.

The other four: timezone offsets were **discarded instead of applied** (so a `+02:00`
timestamp compared equal to the `Z` timestamp two hours earlier, injecting phantom gaps); a
dead branch in stream classification; energy and resource classifiers that overlapped and
double-counted; and partial typing reported as an *anomaly*, which made `ANOMALY` the
verdict for essentially every real graph. That last one is now a separate "completeness"
category — the finding is kept, it's just reported as what it is, which is also more
credible.

---

## 3. What this did to the evaluation

```
  E5 verdict tally        before            after
  ──────────────────────────────────────────────────────
  ca1:ACCEPT              2                 2
  ca2:REVISE              —                 2      ← new, and a real finding
  ca3:ANOMALY             2                 2
  cax:ACCEPT              —                 2
  mc1:PASS                8                 8
  mc3:COMPLETE            —                 8
                          3 kinds           6 kinds
```

`ca2:REVISE ×2` is worth a sentence in the paper: neither bundled yProv4ML record carries
anything numeric that can be plotted. That was true before and completely hidden.

**The lifecycle claim is now precise.** All four operational agents emit 6 activities. The
meta agents emit 3 — Receive, Act, Decide — which is a genuine subset, since they neither
plan nor answer a caller. "A template; each agent instantiates the subset it executes" is
the honest phrasing, and it's now backed by the numbers rather than contradicted by them.

---

## 4. Still open, by design

| finding | where it goes |
|---|---|
| Nothing consumes `governance.*`, so reflexivity is one level deep | Phase 6 — implement it, or narrow the claim in the docs |
| The justification-coverage metric is tautological | Phase 6 |
| The Strategy-A comparison column is unmeasured | Phase 3 — build the real baseline |
| Relation totals mix mechanical and observed edges | Phase 6 reporting |
| No `yp:` vocabulary artifact exists | Phase 7 |
| No model id, sampling parameters, or prompt hash recorded | Phase 4 |

The last one is the big one. A model-backed run and a deterministic one are now
*distinguishable* (`yp:summary_mode`), which they weren't before — but the record still
doesn't say *which* model, at what temperature, under which prompt. Until it does, an
LLM-bearing step is not reproducible from its own provenance.

---

## 5. Driving it yourself

```bash
# the whole pipeline, deterministic
uv run python run_local.py --out-dir out --persist-timeout 20

# with real model calls (aborts rather than silently falling back)
LLM_REQUIRED=1 uv run python run_local.py --out-dir out --persist-timeout 45

# show the degraded persist path that used to be invisible
uv run python run_local.py --out-dir out --persist-timeout 6   # -> one ESCALATE

# did I change the provenance, and how?
uv run python -m eval.compare --before experiments/phase1-reference --after out
uv run python -m eval.compare --before experiments/baseline-pre-review --after out

# everything Phase 1 asserts
uv run pytest -v
```

Three frozen archives, all hash-verified:

| archive | what it is |
|---|---|
| `baseline-pre-review` | the demonstrator as received; backs the review measurements |
| `phase0-docker-llm` | containerised run with real GPT-4o; evidence for finding C4 |
| `phase1-reference` | post-Phase-1 deterministic run; what CI compares against |
