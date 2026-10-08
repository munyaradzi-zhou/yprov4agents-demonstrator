# Execution plan to 8 November

> Reconciles the review findings in [`todo.md`](todo.md) with the E6 overhead
> specification in [`docs/E6_OVERHEAD.md`](docs/E6_OVERHEAD.md).
> Drafted 2026-10-08 · 31 days to deadline.

**The short answer on ordering: E6 goes last, but its harness gets built early.**
E6 measures the cost of a mechanism whose output `todo.md` shows to be partly tautological
(B1), partly fabricated (B4), and partly untrue (C1, C2). Measuring that mechanism now
produces numbers the fixes invalidate — and in one case (R1 below) a *flattering* number
whose flattery comes from the defect. Build `bench_support.py` whenever; run the final
numbers after Phase 4.

I was wrong to rank E6 as "the least important gap" (`todo.md` I.6). It is not low-value —
it is **order-dependent**. And §6's output-size framing ("storage for explainability, not
speed for explainability") is the most defensible claim in either document; I underrated it.

---

## Part 1 — Reconciliation: nine places the two documents interact

<a id="r1"></a>
### R1. E6's central prediction is an artifact of defect C1 ⚠️ *most important*

E6 §9 predicts, and §4.2 builds a whole sub-mode (B-JUST) around:

> *"`yp:justification` costs essentially nothing to compute and a modest number of bytes to
> store — so the trade is storage for explainability, not speed for explainability."*

That prediction is correct **only because the justifications currently contain no
computation.** They are static f-strings over counts already in hand (`todo.md` C1). Of the
24, only CA3's and MC1's text varies with what happened (C3).

Fix C1/C2 and the honest finding changes shape: a real justification costs whatever it costs
to *gather its evidence* — CA3 pays for five algorithms, CA1 pays for a genuine round-trip
disk read (C2). "M5 − M4 = the cost of the headline field" stops being the right model,
because the cost moves out of `record_activity()` and into the agent.

That is a **better** claim, not a worse one: *the cost of justification is the cost of the
evidence, which is the same evidence any audit would demand anyway.* But it is only
discoverable in this order. Measure B-JUST today and you publish "≈ free", sourced from the
defect, and have to retract it.

<a id="r2"></a>
### R2. E6's condition A and review item P4 are the same deliverable — and E6 specifies it wrong

E6 §8 requires a `CallbackLogger` for condition A, and instructs:

> *"It must **not** record justification, types, or lineage, because the whole point of E3 is
> that it cannot."*

Justification: correct, it cannot. **Types and lineage: it can** (`todo.md` B2) — a
LangChain/LangGraph callback handler sees run trees with inputs, outputs and parents, from
which activities, entities, `used`, `wasGeneratedBy` and `wasInformedBy` follow mechanically.

So E6 §8 would hard-code the same false assumption that makes E3's table rejectable, and
`todo.md` P4 would build a second, contradictory baseline. **Build one logger, shared, and
let it capture everything a real handler can.**

Consequences, both good:
- E3's table becomes honest, with a single earned zero on the rationale row.
- A does more work, so A gets slower, so **B-vs-A overhead narrows** — the number the paper
  actually wants. E6 §11's "deliberately generous to A" currently means generous on timing
  and ungenerous on capability; one logger makes it generous on both.

<a id="r3"></a>
### R3. The scaling study (§7) characterises code P5 deletes

E6 §7 names its own suspect:

> *"`wasDerivedFrom` builds a chain over the entity order, `wasInformedBy` over the
> activities."*

Those are exactly the fabricated-lineage loops at `tracker.py:216` and `:248` (`todo.md`
B4) — 152 of the 250 relations, 61% (B3). §7 is right that this is where a complexity
surprise would hide, and right to look. But fix first: otherwise you fit a complexity curve
to code you are about to remove, and the sweep has to be re-run anyway.

<a id="r4"></a>
### R4. E6a benchmarks the API that isn't documented, not the one that is

Level 1 times `register_entity` (M2/M3) and `record_activity` (M4–M6). `INSTRUMENTATION.md`
§3 — paper §5 — promises `step()` / `activity()` / `decide()` / `emit()`, none of which
exist (`todo.md` A1). Benchmarking the inner layer measures something no reader will call.

Do P1 first, then benchmark **both** layers. Wrapper cost is a legitimate E6a row: "what
does the ergonomic API add over the raw calls?" is a fair question with a small answer.

<a id="r5"></a>
### R5. B-TRANS has no implementation behind it — B-META does (both verified)

E6 §4.2 row 3 wants `persistence="TRANSIENT"` to mean "model built, nothing written to
disk", isolating L3.

**Verified: `persistence` is a pure label.** It appears only in `tracker.py` as a stored
attribute and in the `_yprov4agents_meta` block — zero behavioural effect. All six agents
call `write_prov()` unconditionally. E6c row 3 needs ~20 lines of new code (honour the flag
in `write_prov`, or gate it in `base_agent`) before it can be filled in.

**Verified: `granularity="METADATA"` works end to end.** On a 2-activity document:

| mode | sections emitted | bytes |
|---|---|--:|
| DATA | prefix, agent, activity, meta, entity, wasDerivedFrom, wasAssociatedWith, wasInformedBy, used, wasGeneratedBy | 1783 |
| METADATA | prefix, agent, activity, meta, wasAssociatedWith, wasInformedBy | 1133 (−36%) |

Note that E6c will be the **first code anywhere to execute the METADATA path** (`todo.md`
F4) — so treat row 2 as a smoke test as well as a measurement.

<a id="r6"></a>
### R6. CA1's E6b row currently measures a no-op

E6 §5 Level 2 lists CA1 as isolating "overhead against hashing + I/O". But
`store.resolve()` returns the in-memory blob and never touches disk (`todo.md` C2), so the
"I/O" half does not happen. Fix P2/P3 and CA1's runtime rises for real reasons — a genuine
file read plus hash. Measure after, and add M11 `store.resolve()` to Level 1.

<a id="r7"></a>
### R7. The "meta agents are the worst case" premise is wrong as stated

E6 §5 and §9 lead with the meta agents as worst-case ratio, because "their own work is
trivial while they still emit a full typed record". They emit **2 activities**, not 6
(`todo.md` A2) — the record is not full. And A5 requires MC1 to gain a `DecisionActivity`
(today MC1's own output would fail MC1's own check), which grows the record and moves the
ratio.

Settle A2/A5 before measuring the worst case you intend to lead with. The instinct — report
your own worst case — is right and worth keeping.

<a id="r8"></a>
### R8. Level 3 throughput is confounded

E6 §5 Level 3 measures "records/second through producer → 4 operational + 2 meta agents" via
`run_local.py`. But `LocalBus.publish` awaits each handler inline in subscription order
(`todo.md` H1), so there is no concurrency: the figure is a sequential sum, and a consumer's
ack arrives from *inside* the producer's publish call.

Still worth measuring for instrumentation isolation — just label it
*sequential, single-process* and add the confound to §11 threats. Any claim about pipeline
throughput needs the NATS path.

<a id="r9"></a>
### R9. E6 has no correctness gate

A benchmark over invalid PROV is worse than no benchmark: it lends false precision. `prov`
is not in `requirements.txt` and nothing validates output (`todo.md` E). Since `to_prov_json`
*is* M7, the round-trip test must land before Level 1.

### Where E6 improves on my review

Worth recording, not just the conflicts:

- **§2's refusal of the LLM ratio** is exactly right, and sharper than anything in `todo.md`.
- **§6 output-size / bytes-per-justification** is the metric that makes the headline claim
  falsifiable. It survives every fix above and should be promoted.
- **§7's "state the complexity explicitly; if it's not O(A+E) that's a bug worth finding
  before the paper"** is the right instinct and will likely find something, given B4.
- **§8's interleaving and environment capture** are standard-of-care and absent from my review.

---

## Part 2 — The plan

Six phases. Phases 0–1 are the long pole; 5 is mostly waiting on reruns. Day estimates are
rough bands, not commitments.

### Phase 0 — Baseline and safety net · ~1 day

Nothing below is safe without this, because Phase 1 edits `tracker.py` and `store.py`, which
everything depends on.

- [ ] `git init`, commit current state, tag `pre-review-baseline`
- [ ] Copy `out/` → `out.baseline/` (it's gitignored, and B3/D1 are measurements *of* it)
- [ ] Add `prov` to `requirements.txt`; pin all versions (`todo.md` I.2)
- [ ] **Three tests, two of which must fail:**
  - `test_prov_roundtrip` — every `out/*_prov.json` through `ProvDocument.deserialize`
  - `test_pids_resolvable` — **fails today, 2/8** (documents D1/D2)
  - `test_mc1_on_mc1` — **fails today** (documents A5)
- [ ] Delete the dead `examples/mvp.py` references (`energy.py:32`, `__init__.py:11`)

Red tests as specification. They are also paper material: "the demonstrator's own
conformance suite caught N defects" is a better story than silence.

### Phase 1 — Truth in the record · ~4–6 days

Everything that changes *what the PROV says*. All of it precedes all measurement.

- [ ] **P5 / B4** — explicit lineage; drop the index-order `wasDerivedFrom` / `wasInformedBy`
      chains (`tracker.py:216`, `:248`). *Unblocks [R3](#r3).*
- [ ] **P2 / D1 D2** — `LightweightStore.load()`, merge-on-save, one manifest. Re-run
      `test_pids_resolvable` to green. *Unblocks [R6](#r6).*
- [ ] **C2** — `resolve()` reads from `self.root`; real round-trip. Makes CA1's ACCEPT
      falsifiable and ESCALATE reachable. *Unblocks [R6](#r6).*
- [ ] **P3** — fault injection: MC1 emits FAIL, CA1 emits ESCALATE. Fixes the empty E5.
- [ ] **C1** — CAx/CA2 justifications state what was actually checked, or state the verdict
      honestly. *Unblocks [R1](#r1) — do not skip; this is what makes E6's B-JUST meaningful.*
- [ ] **A5 / A2** — MC1 gains a `DecisionActivity`; settle CA2's step count and the
      6-step claim. *Unblocks [R7](#r7).*
- [ ] **E1 / E2** — move `_yprov4agents_meta` out of the top level; `prov:value` not
      `yp:value`. Re-run `test_prov_roundtrip`. *Unblocks [R9](#r9).*
- [ ] **G1–G5** — the CA3 bugs. G2 (tz discard) and G3 (dead branch) are one-liners; G1
      (3σ unreachable) and G5 (typing-coverage-as-anomaly) need a judgement call.

Checkpoint: re-run `run_local.py` + `eval.evaluate`, diff against `out.baseline/`, and write
down what moved. That diff is itself a result.

### Phase 2 — The documented API · ~1–2 days ✅ *complete 2026-10-08*

- [x] **P1** — `step()` / `activity()` / `decide()` / `emit()` as thin wrappers
      (`yprov4agenticai/api.py` + four methods on `ProvTracker`)
- [x] Rewrite `INSTRUMENTATION.md` §3 against the code that now exists
- [x] Port one agent to the new API as the worked example — and then **all six**, because
      the port surfaced [J1](todo.md#j1): 96 of 96 activities recorded a zero duration,
      which no single-agent port would have fixed. Structural diff against
      `phase1-reference`: **empty**, across 24 documents.
- [x] Measure durations monotonically (`api.Span`) — zero-duration activities 96/96 → 0/96

*Unblocks [R4](#r4).* Done before Level 1, so E6a can time the API readers will call — and
can now also read per-step durations out of the record itself, which [J1](todo.md#j1) had
made impossible.

### Phase 3 — The shared baseline · ~2 days ✅ *complete 2026-10-08*

- [x] **P4 = E6 condition A** — one `CallbackLogger` (`eval/baseline.py`), serving both E3
      and E6b, permitted to emit types and lineage (**not** crippled per E6 §8)
- [x] Rewrite E3 with measured A values; only the rationale row is zero — and A ties B on
      activities, relations and PROV-JSON conformance, which is a stronger result than any
      gap would have been
- [x] Amend E6 §8 and §11 to match; §11 now states A as an **upper bound**, since its event
      stream is built from B's own declarations
- [x] Archive `experiments/fault-escalate` — the first run in which `ca1:ESCALATE` fires,
      and the A-versus-B worked example for §7

*Resolves [R2](#r2).* The single highest-leverage item in the plan: it fixes the most
rejectable table in the paper and the baseline for the overhead study at the same time.

### Phase 4 — Enable the modes E6 needs · ~1 day ✅ *complete 2026-10-08*

- [x] Give `persistence="TRANSIENT"` actual behaviour (*[R5](#r5)*) — `emit()` suppresses
      the sink; the record is still built, returned and published, which is precisely the
      layer E6c needs isolated
- [x] Smoke-test `granularity="METADATA"` through a real agent — both modes are now
      selected by environment variable through one tracker factory, so a sweep cannot be
      honoured by five agents and forgotten by the sixth
- [x] **P6 / F1 F2** — `runtime/llm.py`: model, temperature, max_tokens, token counts,
      latency, finish reason and the prompt's id/version/sha, on both LLM-bearing agents.
      Archived in `experiments/phase4-llm`
- [x] Decide on `energy.py` (F3) — **wired in**, per step, opt-in via `PROV_ENERGY`.
      Wiring it surfaced a methodological finding worth the paper: per-step energy
      attribution is unsound when the backend samples less often than a step lasts, and
      the record now discloses that rather than presenting the figure as an attribution

### Phase 5 — E6 proper · ~4–5 days, spread over two sittings

Per `docs/E6_OVERHEAD.md` §13, amended by R1–R9. **`bench_support.py` is independent of
every phase above — build it whenever there's a gap.**

- [ ] `bench_support.py` — `timeit`, percentiles, environment capture *(parallelizable now)*
- [ ] `NullTracker` (condition OFF)
- [ ] Level 1: M1–M10, **plus M11 `store.resolve()`** ([R6](#r6)), **plus wrapper-vs-raw**
      ([R4](#r4))
- [ ] Level 2: per-agent OFF/A/B, interleaved, ≥30 reps, + output bytes
- [ ] Scaling sweep + complexity fit ([R3](#r3) — now over the corrected tracker)
- [ ] Level 3 pipeline, **labelled sequential/single-process** ([R8](#r8))
- [ ] E6c modes: DATA / METADATA / TRANSIENT / justification on-off
- [ ] `E6_RESULTS.md` renderer, tables E6a–E6d
- [ ] Re-run on a second occasion; confirm stability

### Phase 6 — Vocabulary, tables, truth pass · ~3 days

- [ ] **P7** — `vocab/yprov4agents.ttl` + SHACL shape; reframe MC1 as shape validation
- [ ] **P8 / B1** — reframe E2 as a conformance check, or give it a real denominator
- [ ] **B3** — re-report E1/E3 split mechanical vs. observed
- [ ] **A3 / A4** — correct the verdict table and the reflexivity claim in README and
      `DESIGN.md` §4
- [ ] **H2 / H3** — CA1 state pruning, or state both in limitations; disclose at-most-once
- [ ] Full doc truth-pass: every claim in README / DESIGN / INSTRUMENTATION / OUTLINE traced
      to code or deleted

---

## Part 3 — Critical path and what to cut

```
Phase 0 ─┬─ Phase 1 ──┬─ Phase 2 ─── Phase 3 ──┬─ Phase 5 ─── Phase 6
         │            │                        │
         └─ (tests)   └─ Phase 4 ──────────────┘

bench_support.py ·········· parallel, any time ········>
```

**Hard sequence:** 0 → 1 → {2, 4} → 3 → 5 → 6. Phase 3 needs Phase 2's API to instrument B
fairly; Phase 5 needs 3's baseline and 4's modes.

**If time runs short, cut in this order:**

1. **Level 3 pipeline throughput** — confounded by [R8](#r8) anyway; replace with a sentence
   in limitations.
2. **E6c B-TRANS row** — needs new code ([R5](#r5)) for a row that mostly restates L3 is optional.
3. **`energy.py` wiring** (F3) — move to `experimental/` and say so.
4. **A2's CA2 sixth step** — rephrasing the lifecycle claim is cheaper and just as honest.

**Do not cut:** the three Phase 0 tests, Phase 1 in full, Phase 3, the scaling sweep, or the
`yp:` vocabulary. The scaling sweep is where a real bug may still be hiding; the vocabulary
is contribution C3, which currently exists only as strings inside f-strings.

**Two sittings are required** for E6 (§12 asks for stability across occasions), so Phase 5
cannot be compressed into the final week. Working backwards from 8 November: Phase 5 should
start by **25 October** at the latest.
