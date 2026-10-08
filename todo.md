# Review findings & fix plan — yProv4Agents demonstrator

> Expert provenance review, 2026-10-06. Captured from the review session so nothing is
> lost. Working document: tick items as they are fixed.
>
> **Verdict up front:** the thesis is right and the architecture is sound, but several of
> the headline numbers will not survive review, and the library's advertised surface is
> much larger than what the demonstrator actually exercises.

---

## Priority queue for 8 November

> **Superseded by [`PLAN.md`](PLAN.md)** (2026-10-08), which sequences these into six phases
> and reconciles them with `docs/E6_OVERHEAD.md`. The P-numbers below are still the
> reference labels — `PLAN.md` cites them. Read `PLAN.md` for ordering, this file for the
> evidence behind each item.

Items P2, P3 and P6 are each under an hour and each converts a contested claim into a
measured one.

- [ ] **P1 — Implement `step` / `activity` / `decide` / `emit`** so `INSTRUMENTATION.md` §5
      describes real code. See [A1](#a1).
- [ ] **P2 — Fix the dangling PIDs and add `LightweightStore.load()`**, then report
      "% of cited PIDs resolvable". See [D1](#d1), [D2](#d2).
- [ ] **P3 — Fault-injection experiment** so MC1 emits FAIL and CA1 emits ESCALATE; fix the
      no-op integrity check to make the latter reachable. See [A3](#a3), [C2](#c2).
- [ ] **P4 — Build the real Strategy-A baseline** and rewrite E3 with only the rationale
      row at zero. See [B2](#b2).
- [ ] **P5 — Drop or make explicit the auto-generated `wasDerivedFrom` / `wasInformedBy`
      chains**, and re-report E1/E3 split by mechanical vs. observed. See [B4](#b4), [B3](#b3).
- [ ] **P6 — Wire prompt provenance into CAx** and record model + params (~15 lines; the
      difference between "agentic AI provenance" and "agent-shaped provenance").
      See [F1](#f1), [F2](#f2).
- [ ] **P7 — Ship `vocab/yprov4agents.ttl` + a SHACL shape**, and reframe MC1 as shape
      validation. See [Section E](#e-prov--standards-conformance).
- [ ] **P8 — Reframe E2 honestly** ([B1](#b1)); fix the CA3 bugs ([G1–G5](#g-ca3-algorithm-correctness));
      correct the reflexivity claim ([A4](#a4)).

---

## What is genuinely strong (keep, and lean on it)

- **The core argument is correct.** "The rationale for a decision is a local variable at
  the moment of decision" (`docs/INSTRUMENTATION.md` §1) is the best writing in the repo
  and it is a real structural claim, not marketing. Capture-not-retrieve holds.
- **The two-layer position** — ride OTel for Layer 1, own PROV + rationale — is
  strategically right and will age well against the observability incumbents.
- **Persist-after-consume** (`runtime/agents/ca1_storage.py`) is the one place where
  behavioural provenance has genuine information content: the justification text *differs*
  between the quorum path and the timeout path. That is the paper's best worked example and
  it is underplayed.
- **Domain-agnosticism is actually honoured** — every computation derives from PROV itself,
  not an ML schema. Rare for a claim like that to survive contact with code.
- **Two run paths with no API key required** is excellent demo engineering.
- **CA3 finding real defects in yProv4ML's own output** (8/9 activities with no end time,
  82.1% typing coverage) is the most persuasive result in the project.

---

## A. Claims the code does not support (highest paper risk)

<a id="a1"></a>
### A1. `INSTRUMENTATION.md` §3 documents an API that does not exist

It shows `@tracker.step(...)`, `with tracker.activity(...)`,
`tracker.decide(verdict, confidence, justification)`, `tracker.emit()`. None exist —
grep for `def step` / `def activity` / `def decide` / `def emit` returns nothing. The real
API is `register_entity` / `record_activity` / `to_prov_json`.

This document is §5 of the paper and answers Sandro's central question. The four methods
are thin wrappers over what already exists (~60 lines) and implementing them also makes the
"a few lines, and only at the decisions" effort claim measurable. **Fix this first.**

<a id="a2"></a>
### A2. The "canonical 6-step lifecycle" is not uniform

Your own E1 table says so: CA2 = 5.0 activities, MC1 = 2.0, MC3 = 2.0. C3 claims the
lifecycle is "instantiated by four heterogeneous agents". Either bring CA2 to six steps, or
rephrase to "a template; each agent instantiates the subset it executes" and state which
steps each omits and why.

<a id="a3"></a>
### A3. Most of the advertised verdict space never occurs

README lists REVISE/ESCALATE for CAx, REVISE for CA2, FAIL for MC1. E5 shows only
`ca1:ACCEPT x2`, `ca3:ANOMALY x2`, `mc1:PASS x8`. CAx and CA2 never write `yp:verdict` at
all — only `ca1_storage.py:180`, `ca3_analytics.py:257`, `mc1_compliance.py:74` set it.

The first question a reviewer asks is "has MC1 ever said FAIL?" Right now the answer is no.

<a id="a4"></a>
### A4. "The trackers are tracked" is one level deep, not reflexive

Nothing subscribes to `governance.>`. The README diagram annotates MC1/MC3 as "(themselves
tracked)" and `DESIGN.md` §4 says "and are themselves tracked the same way" — neither is
true. Your own E4 table lists only the four operational agents.

<a id="a5"></a>
### A5. Self-application fails

MC1's own PROV contains `ReceiveTaskActivity` + `ComplianceCheckActivity` and **no**
`yp:DecisionActivity` — so MC1's output would **FAIL MC1's own check**. And
`eval/evaluate.py::decisions()` widens the denominator to
`endswith(("DecisionActivity", "ComplianceCheckActivity", "AuditActivity"))`, which papers
over exactly that gap.

Running MC1 on MC1 is a one-line experiment and a genuinely interesting result — do not
hide it, report it.

---

## B. The headline metrics are not measurements

<a id="b1"></a>
### B1. E2 = 100% is tautological

`decisions()` counts exactly the activity kinds your code unconditionally passes
`justification=` to. Numerator and denominator come from the same call sites. No run could
ever produce another number.

**Fix:** reframe as a conformance check, or get a real denominator — count verdict/branch
points in the source and report what fraction are instrumented. That version is defensible.

<a id="b2"></a>
### B2. E3's Strategy A column is hard-coded zeros, and three of the four are false

A LangChain/LangGraph callback handler sees run trees with inputs, outputs and parents — you
can mechanically derive typed activities, entities, `used`, `wasGeneratedBy` and
`wasInformedBy` from them. Only the `yp:justification` row survives.

As written, any reviewer who has written a callback handler rejects the table, and the
credibility hit spreads to C1.

**Fix:** build the ~100-line Strategy-A baseline that actually emits PROV from callbacks
over the same agents, measure it, and let *only* the rationale row be zero. The isolated,
earned zero is a far stronger result than four unearned ones.

> **Same deliverable as E6's condition A.** `docs/E6_OVERHEAD.md` §8 specifies a
> `CallbackLogger` that "must not record justification, types, or lineage". Justification it
> cannot; types and lineage it can — so that instruction bakes in the flaw above. Build
> **one** logger for both E3 and E6b, permitted to emit types and lineage. Side effect: A
> gets slower, so B-vs-A overhead narrows, which is the number the paper wants. See
> [`PLAN.md` R2](PLAN.md#r2).

<a id="b3"></a>
### B3. "250 lineage relations" is 61% mechanical

Measured across all 24 docs:

| Relation | Count | Nature |
|---|--:|---|
| `wasAssociatedWith` | 78 | one per activity — mechanical |
| `wasInformedBy` | 54 | n−1 per doc — mechanical |
| `wasDerivedFrom` | 20 | n−1 over registration order — mechanical |
| `used` | 54 | observed dataflow |
| `wasGeneratedBy` | 44 | observed dataflow |
| `actedOnBehalfOf` | 0 | never emitted |
| **total** | **250** | **152 mechanical (61%)** |

These are functions of activity count, not independent evidence. Report `used` +
`wasGeneratedBy` (98) as observed dataflow and the rest separately.

<a id="b4"></a>
### B4. Worse — the auto-chains assert lineage you never observed

`tracker.py:216` asserts `wasDerivedFrom` between entity *i* and *i−1* by **registration
order**; `tracker.py:248` asserts `wasInformedBy` between activity *i* and *i−1* by
**index**. Register two independent entities and PROV now claims one derived from the other.

For a paper about provenance correctness this is the one thing you cannot ship. Make it
explicit (`record_activity(..., derived_from=[...])`) or drop the chain.

> **Blocks E6's scaling study.** `docs/E6_OVERHEAD.md` §7 names these two loops as its prime
> suspect for hidden super-linear cost — and it is right to. But fix them first, or the
> complexity fit characterises code that is about to be deleted. See
> [`PLAN.md` R3](PLAN.md#r3).

---

## C. The justifications themselves

<a id="c1"></a>
### C1. Most are static templates with no decision in them

CAx: `"Summary covers all {n} activities and {m} entities...; grounded in the graph counts.
Accepted."` — the verdict is constant, nothing was checked, and in LLM mode the claim is
**false**: a 3–4 sentence summary does not cover 39 entities. That is a provenance record
asserting an untruth about its own run. CA2 has the same shape.

<a id="c2"></a>
### C2. CA1's integrity check is a no-op

`store.py:65` has `put()` set `self._blobs[pid] = payload`; `store.py:75` has `resolve()`
return `self._blobs[pid]`. In the same process the disk file is never read and the hash is
recomputed over the identical in-memory object.

So `"round-trip read verified; sha-256 matched"` — the single clause distinguishing ACCEPT
from ESCALATE — is unfalsifiable.

**Fix:** make `resolve()` read from `self.root` when it exists (or add `verify_roundtrip()`
forcing a disk read). ESCALATE then becomes reachable, which also gives you a real E5.

<a id="c3"></a>
### C3. Net effect

Of 24 justified decisions, only CA3's and MC1's text varies with what actually happened.
Treating all 24 as equivalent evidence for C1 overstates it, and a reviewer who reads three
of them will notice.

> **Blocks E6.** `docs/E6_OVERHEAD.md` §9 predicts "justification costs essentially nothing
> to compute — a storage cost, not a compute cost". That holds *only because* of C1: the
> justifications contain no computation. Fix C1/C2 first and the honest claim becomes "the
> cost of a justification is the cost of gathering its evidence". See
> [`PLAN.md` R1](PLAN.md#r1) — measuring B-JUST before this is fixed publishes a flattering
> number sourced from the defect.

---

## D. Pointers-not-payloads is broken end to end

<a id="d1"></a>
### D1. Measured: PROV cites 8 PIDs; the manifest holds 2. Six dangle.

```
PIDs cited in PROV:        8
resolvable via manifest:   2
dangling:                  6
blobs on disk:             8
manifest entries:          2
```

Cause: CA1/CA2/CA3/CAx each build their own `LightweightStore` over the same directory with
separate in-memory manifests, and only CA1 calls `save()` — last writer wins.

<a id="d2"></a>
### D2. `LightweightStore` has no `load()`

`resolve()` indexes `self.manifest[pid]` for the expected hash, so a fresh process can never
dereference a PID **at all**. The design's only real test — can a third party resolve the
pointer? — currently fails.

**Fix:** `load()` on init, merge-on-save (file lock, or a single store process), and an eval
check "% of cited PIDs resolvable". That is a much better paper number than E2.

<a id="d4"></a>
### D4. `put()` hashes LF bytes but `write_text()` writes CRLF — a trap set for the C2 fix

*Found 2026-10-08 while committing the baseline archive.*

`LightweightStore.put()` computes its SHA-256 over `payload.encode("utf-8")` — an in-memory
string, so LF. It then persists with `Path.write_text()`, which on Windows translates every
`\n` to `\r\n`. For any payload containing a newline, **the bytes on disk do not hash to the
`yp:contentHash` the provenance graph asserts.**

This is dormant today only by luck: all eight current blobs are single-line (`json.dumps`
output, and one-line stub summaries), measured 0 CRLF pairs across all of them, so every
hash matches. It becomes a live failure the moment either condition changes:

- **C2 is fixed** so `resolve()` actually reads from disk — then verification fails.
- **Real LLM output is used** instead of the deterministic stub — model prose contains
  newlines.

Both are planned, and they are planned *together*, so this would surface as a confusing
integrity failure that looks like the C2 fix being wrong.

**Fix (do it as part of C2):** make the store binary end-to-end — `write_bytes(raw)` and
`read_bytes()` — so the hashed bytes and the stored bytes are the same object. Never let a
text-mode round-trip sit between a hash and its payload.

*The same class of bug bit the experiment archiver: git's `text=auto eol=lf` rewrote archived
files on commit, so manifest hashes (CRLF, 2012 B) diverged from stored blobs (LF, 1956 B).
Fixed by marking `experiments/**` as `-text` in `.gitattributes`.*

<a id="d3"></a>
### D3. PID semantics

`pid = "21.T11999/yprovstore-" + sha256[:12]` is content-addressed, so identical payloads
from different agents and sessions collapse to one provenance identity. You are also minting
inside a real Handle prefix you presumably do not control — label these as mock handles
explicitly.

---

<a id="e-prov--standards-conformance"></a>
## E. PROV / standards conformance

- [ ] **`_yprov4agents_meta` is a non-standard top-level container.** The `prov` Python
      library and ProvToolbox will reject or drop it. Move it to bundle-level attributes or
      an entity describing the record.
- [ ] **No PROV-JSON validation anywhere**, and `prov` is not in `requirements.txt`. One
      test round-tripping every emitted doc through `prov.model.ProvDocument.deserialize` is
      ~10 lines and the cheapest credibility you can buy for a "standard W3C PROV-JSON"
      claim.
- [ ] **Entity values use `yp:value`, not `prov:value`** — a standard consumer sees typed
      entities with no value, for no gain.
- [ ] **There is no `yp:` vocabulary artifact in the repo.** No OWL/RDFS/SHACL, and
      `http://hpci-lab.org/yProv4Agents#` does not dereference. C3 is "the `yp:` ontology"
      but the ontology exists only as strings inside f-strings.
      **Minimum:** `vocab/yprov4agents.ttl` declaring `yp:*Activity rdfs:subClassOf
      prov:Activity`, `yp:*Entity rdfs:subClassOf prov:Entity`, and `yp:justification` with
      domain/range and a definition. A SHACL shape for *"every `yp:DecisionActivity` has
      exactly one `yp:justification`"* would turn MC1 from bespoke Python into a
      standards-based check — strictly stronger.

---

## F. LLM provenance — the biggest substantive gap for an *agentic AI* provenance paper

<a id="f1"></a>
### F1. In LLM mode the PROV records nothing about the model

No model id, no temperature (0.2), no max_tokens (400), no prompt, no response hash, no
token counts, no latency. `runtime/util.py::get_llm()` reads `OPENAI_MODEL` defaulting to
`gpt-4o` and that string never reaches a record. The LLM steps are not reproducible or
auditable from their own provenance — precisely what the paper says Layer 2 exists for.

<a id="f2"></a>
### F2. `prompts.py` (238 lines) exists for exactly this and nothing imports it

`Prompt.provenance()` returns `yp:prompt_id` / `_version` / `_sha`; grep across `runtime/`,
`eval/`, `run_local.py` returns zero hits. CAx builds its prompt as an inline f-string. The
README advertises prompt provenance; the running system has none. Wiring it into CAx is
~5 lines and directly serves the paper.

<a id="f3"></a>
### F3. `energy.py` (311 lines) is also entirely unused

`record_activity(energy_attrs=...)` is never called. The irony: CA3 reports *"no
energy/emission streams — run is not carbon-instrumented"* as an anomaly, and it is right
about your own agents.

<a id="f4"></a>
### F4. Also never executed

`register_memory` / the memory revision chain; `principal` / `actedOnBehalfOf` delegation
(confirmed: 0 `actedOnBehalfOf` relations across all 24 docs); `granularity="METADATA"`.

Roughly 550 of ~960 library lines are dead to the demonstrator, including both largest
modules. Wire in two, or move the rest to a labelled `experimental/`. The gap between
advertised and demonstrated surface is the easiest thing for a reviewer to attack.

**Verified 2026-10-08, re: E6c's mode table:**

- `granularity="METADATA"` **works.** On a 2-activity doc: DATA 1783 bytes → METADATA 1133
  bytes (−36%), entity section and entity-side relations correctly dropped. E6c will be the
  first code anywhere to execute this path, so treat the row as a smoke test too.
- `persistence="TRANSIENT"` is **not implementable as specified.** `persistence` is a pure
  label — it appears only as a stored attribute and in the `_yprov4agents_meta` block, with
  zero behavioural effect, and all six agents call `write_prov()` unconditionally. E6c row 3
  needs ~20 lines of new code first. See [`PLAN.md` R5](PLAN.md#r5).

---

<a id="g-ca3-algorithm-correctness"></a>
## G. CA3 algorithm correctness

<a id="g1"></a>
### G1. The 3σ timing check can never fire on any input in this repo

With population stdev over *n* samples the maximum attainable z-score is `(n−1)/√n`; you
need n ≥ 11 gaps (≥ 12 timestamped activities) before 3σ is reachable at all. Agent
lifecycles have 2–6 activities; yProv4ML `example_0` has 9.

**Fix:** median + k·MAD, or IQR, or guard with "insufficient sample" rather than silently
passing. (`ca3_analytics.py:39` sets `_SIGMA = 3.0`.)

<a id="g2"></a>
### G2. `ca3_analytics.py:53` discards timezone instead of converting

`dt.replace(tzinfo=None)` makes a `+02:00` timestamp and a `Z` timestamp two hours apart
compare as simultaneous, and injects phantom 2-hour gaps into the exact series A2/A5
analyse.

**Fix (one line):** `dt.astimezone(timezone.utc).replace(tzinfo=None)` when `dt.tzinfo` is
set.

<a id="g3"></a>
### G3. Dead branch at `ca3_analytics.py:142`

`if _type_of(e).endswith("Metric") or pattern.search(label):` is immediately followed by
`if not pattern.search(label): continue` — the type-based arm can never contribute. The
function reduces to `pattern.search(label)`.

<a id="g4"></a>
### G4. `_ENERGY_PAT` and `_RESOURCE_PAT` overlap

`power` is in energy; `gpu_power_usage` matches both. A3 and A4 double-count and are not a
partition — do not report the two counts as independent.

<a id="g5"></a>
### G5. `typing_coverage < 100` is reported as an anomaly

That makes ANOMALY the default verdict for essentially any real graph (both your runs) and
collapses the verdict's information content.

**Fix:** split "completeness / data-quality findings" from "anomalies". OK then appears in
E5, and the yProv4ML completeness result gets its own, more credible category.

---

## H. Concurrency / two-path equivalence

- [ ] **`LocalBus.publish` awaits each handler inline in subscription order**, so the quorum
      flow is sequential and re-entrant — a consumer's ack is delivered from *inside* the
      producer's `publish` call. The README's ack transcript is subscription order, not
      concurrency. The local path exercises none of the NATS path's races, so the two are not
      equivalent evidence despite "the only thing swapped out is the transport".
- [ ] **CA1 leaks:** `_acks` / `_done` are never pruned, and `_timeout_guard` tasks are never
      cancelled on early quorum. Fine at n=2; mention it in limitations.

<a id="h4"></a>
- [ ] **H4 — persist-after-consume is a race against the wall clock, not a guarantee.**
      *Found 2026-10-08 by running with `--persist-timeout 6`.*

      The README calls this "the ordering that matters" and says a timeout guard exists so
      "a crashed agent can never silently lose a record". What actually happens is weaker:
      `_timeout_guard` is scheduled on `STREAM_END`, and `_finalize` awaits
      `run_in_executor`, which yields to the event loop — so the guard competes with the
      consumers rather than backing them up. Whichever finishes first wins.

      Measured: at `PERSIST_TIMEOUT_S=6`, `example_0` persisted via the timeout path with
      **zero** acknowledgements —
      `"(timeout after 6.0s; no acknowledgement from ['ca2','ca3','cax']); acknowledged by
      [none]"` — while `example_2` reached quorum in the same run. At 30s (the default) both
      reach quorum, which is why this has never been visible.

      It matters more once real LLM calls land: a consumer doing a 500–3000 ms API call,
      retried on failure, can exceed a modest timeout, at which point the property the paper
      leads with degrades to persist-anyway on every record. The verdict stays ACCEPT either
      way, so nothing surfaces the degradation except reading the justification text.

      **Two things to fix:** cancel the guard on early quorum, and make the distinction
      load-bearing — a record persisted with no consumer acknowledgement should not be
      reported as a plain ACCEPT. This is also the natural place to make ESCALATE reachable
      ([C2](#c2), [P3](PLAN.md)).

      Good news for the evaluation: the quorum-versus-timeout branch is genuine behavioural
      provenance whose justification text really does differ by what happened. It is the
      strongest existing counter-example to [C1](#c1), and worth the paper's worked example.
- [ ] **NATS core is at-most-once, no JetStream.** A provenance bus that can silently drop
      records deserves an explicit sentence in a provenance paper.

---

## I. Hygiene

- [ ] **No tests at all.** Three cheap ones carry real weight:
      (1) PROV-JSON round-trips through `prov`;
      (2) every cited PID resolves;
      (3) a fault-injection case where MC1 emits FAIL.
- [ ] Dependencies unpinned (`>=`) — pin for the paper artifact.
- [ ] `examples/mvp.py` is cited in `energy.py:32` and `__init__.py:11` but does not exist.
- [ ] `.env` is present and correctly gitignored (not read during review) — keep it out of
      whatever archive ships with the paper.
- [ ] **Not a git repo.** For a November contract deliverable that a paper cites, you want
      history and a tag.
- [ ] ~~**E6 (overhead) is the *least* important gap**~~ — **amended 2026-10-08** after
      reading `docs/E6_OVERHEAD.md`. E6 is not low-value, it is **order-dependent**: it
      measures the cost of a mechanism whose output is currently tautological (B1),
      partly fabricated (B4) and partly untrue (C1, C2), so its numbers must come after
      those fixes. And §6's output-size framing — *storage for explainability, not speed for
      explainability* — is the most defensible claim in either document; I underrated it.
      The ranking that survives: the **disputed** claims (B2, C1–C3) are more urgent than the
      **missing** one (E6), and E6 gets better the later it runs. Full reconciliation in
      [`PLAN.md`](PLAN.md) Part 1.
