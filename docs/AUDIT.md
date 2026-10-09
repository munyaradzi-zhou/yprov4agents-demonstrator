# Project audit — red flags and pitfalls

> **Date:** 9 October 2026 · **Scope:** the whole repository at commit `54fd011` —
> reproducibility, CI gates, runtime agents, the evaluation methodology, tests, security,
> and whether the paper's claims match the artifacts.
> **Rule followed:** nothing below is an opinion about style. Every finding marked
> *proven* was reproduced with a command, and the command is given so the finding can be
> re-checked once it is fixed.
> **Parked:** energy (PR #11), by decision.

---

## The picture in one diagram

Where each problem sits on the path from a record arriving to a sentence in the paper:

```
  producer ──► bus ──► agents ──► records ──► CI gates ──► archives ──► paper
     │          │         │          │            │            │          │
     │          │         │          │            │            │          └─ [2] headline test is
     │          │         │          │            │            │                 verbatim-only
     │          │         │          │            │            └─ [4] "dirty" flag is
     │          │         │          │            │                  always true
     │          │         │          │            └─ [1] lineage changes pass the
     │          │         │          │               "unannounced change" gate
     │          │         │          └─ [5][6] MC1 and SHACL disagree;
     │          │         │             shapes pass a record with no decision
     │          │         └─ [8] CA1 silently drops a repeated session
     │          └─ [3] a lost chunk becomes a well-formed, wrong record
     └─ [8] multi-rank inputs would collide on one session id
```

---

## Severity 1 — affects whether a published claim is true

### [1] The CI gate for "provenance did not change unannounced" cannot see lineage *(proven)*

**What happens.** `eval.compare --strict` compares *how many* relations of each kind a
record has, never *what they connect*. Repointing a `used` edge at the wrong entity and
reversing a `wasDerivedFrom` leaves every count unchanged, and the gate reports *"No
structural differences"* with exit code 0.

**Why it matters.** This is exactly the defect class the paper discloses in §7 — fabricated
lineage with plausible counts. The gate that is supposed to stop it recurring is blind to
it.

**Fix.** Compare relation *endpoints*, canonicalised so volatile ids don't cause noise:
map each endpoint to a stable key (activity type, entity type plus label), and compare the
resulting sets of `(relation, from-key, to-key)` triples. Add a test that mutates one edge
and expects the gate to fail.

**Re-check.** Copy any archived `*_prov.json`, swap one `used` target, run
`uv run python -m eval.compare --before <orig> --after <mutated> --strict` — it must exit 1.

### [2] The paper's central number is measured with a test that is nearly true by construction *(proven)*

**What happens.** "0 of 24 justifications recoverable" means *no justification appears
verbatim, as a whole sentence,* in what the callback layer saw. Verdicts are searched with a
lenient token match; justifications with an exact whole-sentence match. A sentence assembled
at runtime will almost never survive intact anywhere, so the zero is close to guaranteed.

Measured instead at the level of *facts* (multi-digit numbers, hashes, role names,
identifiers) against the archived callback stream in `experiments/phase3-reference`:

| agent | justifications | informative facts | visible to Strategy A |
|---|--:|--:|--:|
| CA1 | 2 | 15 | 13 (87%) |
| CA2 | 2 | 7 | 7 (100%) |
| CA3 | 2 | 11 | 6 (55%) |
| CAx | 2 | 7 | 3 (43%) |
| MC1 | 8 | 8 | 8 (100%) |
| MC3 | 8 | 6 | 6 (100%) |

This measure over-counts — a role name appearing in the stream is not the same as knowing
that role acknowledged — so the truth lies between the two. But one conclusion holds under
either: **16 of the 24 justifications come from MC1 and MC3, and they restate structure
Strategy A already reconstructs in full** (e.g. *"6 activities (1 justified) in order
ReceiveTask → Planning → …"*).

**Why it matters.** A reviewer who looks at one MC3 justification will find the headline
overstated, and the paper's strongest claim rests on it.

**Fix.** Narrow the claim rather than drop it. What is not observable is the *decision
rule*: which facts were combined, and how, to reach the verdict. Report:
(a) the verbatim result, named as such; (b) a fact-level result split by agent;
(c) the headline over the **8 operational decisions**, with the meta agents reported
separately as restatements. The structural argument in §1 does not depend on the number and
survives intact.

### [3] A lost chunk produces a well-formed record about a truncated input *(confirmed in code)*

**What happens.** Every envelope carries a `sequence_no`; nothing reads it. On `STREAM_END`,
an agent processes whatever it has buffered. If the relations chunk is lost on the
at-most-once bus, the agent emits a complete-looking PROV record with activities, entities
and no edges. MC1 passes it, and CA1 persists it.

**Why it matters.** The paper discloses that the bus can *lose* provenance. This is worse
— it can *silently corrupt* it, and the result looks like success.

**Fix.** Put the chunk count in `STREAM_START` (or `STREAM_END`), check
`sequence_no` continuity on finalize, and on a gap emit an explicit
`yp:verdict = INCOMPLETE` record instead of processing. Add a test that drops one chunk.

---

## Severity 2 — undermines a reproducibility or conformance statement

### [4] Every archive says it came from a dirty working tree, because freezing makes it so *(proven)*

**What happens.** `eval/archive.py::freeze` copies files into `experiments/<name>/`
*before* reading `git status`. The new directory is untracked at that moment, so the
manifest always records `dirty: true`. A freeze from a perfectly clean tree reproduced this.

**Why it matters.** All eleven archives say `dirty: true`, so the flag carries no
information — and an archive taken from a genuinely modified tree cannot be told apart from
a clean one. The manifests also don't record *what* was modified, so no archive can be
rebuilt from its commit.

**Fix.** Read git state before copying; ignore the archive's own path; record the list of
modified tracked files and a hash of `git diff HEAD`. Then re-freeze the archives the paper
cites (`phase3-reference`, `phase4-llm`, `phase5-overhead`, `verdict-reachability`) from
committed code. `eval/archive.py` has **0% test coverage**, which is how this survived.

### [5] MC1 and the declared SHACL standard disagree in both directions *(proven)*

| record | MC1 (runtime) | SHACL (the "external judge") |
|---|---|---|
| justification written as a PROV-JSON typed literal of whitespace, `{"$": " ", "type": "xsd:string"}` | **passes** | rejects |
| a step untyped, or typed with a non-existent class | **passes** | **conforms** |
| `yp` prefix bound to the wrong namespace | **passes** | — |

> **Corrected while fixing it.** An earlier version of this table also listed "a decision
> loses its `prov:type`" as conforming to the shapes, as evidence for [6]. That was a bad
> test, not a finding: the ontology gives `yp:justification` the domain
> `yp:DecisionActivity`, so under RDFS inference a node carrying a justification *is* a
> decision, and the shapes were right to accept it. MC1, which has no reasoner, now
> rejects that record — a deliberate difference, pinned by a test rather than hidden.

The first row reintroduces the whitespace defect the paper says was found and fixed, through
the standard PROV-JSON encoding for typed literals — the encoding third-party tools emit, and
third-party records are the case MC1 exists for. The check named `typed-activities` only
tests that the activity dictionary is non-empty; it never inspects a type.

**Fix.** Normalise typed literals (`{"$": …}`) before checking; make `typed-activities`
check that each activity's `prov:type` is a `yp:` lifecycle class; check the namespace URIs,
not only the prefix names. Extend `test_the_shapes_and_the_compliance_agent_agree` with
every mutation above, so the two implementations are tested against each other on the cases
where they actually differ.

### [6] The shapes accept a record with no decision in it *(proven)*

**What happens.** Every shape targets a class, so it is silent when no node of that class
exists. Deleting the decision activity outright — the node and the relations that mention
it — leaves a record that **conforms** under the shapes as published. "24 of 24 records
satisfy the published shapes" would remain true of a pipeline that recorded no decisions
at all. *(The first version of this finding demonstrated it by removing the decision's
`prov:type`. That was the wrong test — see the correction under [5] — and the finding was
re-established by deletion before it was fixed.)*

**Fix.** Add an existence constraint: target the agent and require at least one
`yp:DecisionActivity` via an inverse-path qualified count (`sh:qualifiedMinCount 1`). MC1's
comment that "SHACL cannot require a node to exist" is not right, and should go with the fix.

### [7] The model is recorded as an alias, not the version that answered *(confirmed)*

**What happens.** All four LLM records in `experiments/phase4-llm` say
`"yp:model": "gpt-4o"`. That is an alias the provider re-points over time. The resolved
snapshot (`response.model`) and `system_fingerprint` are not recorded.

**Why it matters.** The paper presents `yp:model` as what lets an auditor repeat the call;
an alias sends the auditor to a different model.

**Fix.** Record the requested alias *and* `response.model` and `system_fingerprint`
(`yp:model_requested`, `yp:model_resolved`, `yp:system_fingerprint`).

---

## Severity 3 — real defects with no published claim resting on them yet

**[8] CA1 silently drops a session it has seen before.** `_done` returns early for any
repeated session id, with no record emitted — so a replayed record, or a producer restarted
by compose's `restart: on-failure`, is never persisted and nothing says so. Latent, related
issue: the session id is the *folder name*, and yProv4ML's per-rank files (`…_GR0`, `…_GR1`)
share a folder in multi-GPU runs, so they would collide. *Fix:* key sessions by file, and on
a duplicate emit a `DUPLICATE` decision rather than silence.

**[9] The paper says the rule is "written once in one place".** It is written twice — in
Python inside MC1, and in SHACL — and kept in agreement by a test. Findings [5] and [6] show
the two disagree today. *Fix:* reword §3 to "declared once, and the runtime subset tested
against the declaration", and make that true via [5].

**[10] The distributed path is never exercised in CI.** `producer.py` has 0% coverage and
`net_bus.py` 30%; the container job only checks that modules import, and compose never runs.
The paper calls the system "running, containerised". *Fix:* a CI job with a NATS service
container that runs the six agents plus the producer and passes the same `compare` and shapes
gates as the in-process run.

**[11] The code that writes the paper's numbers is untested.** `bench_overhead.py` and
`bench_report.py` are at 0%; `evaluate.py` at 16%. The 254-vs-278-bytes inconsistency found
earlier was in this code. *Fix:* golden-file tests over a tiny fixed results file.

---

## Severity 4 — hygiene

- **A second copy of a live API key on disk.** `.env.corrupted.bak` holds a 164-character
  `OPENAI_API_KEY` value next to the live `.env`. Neither has ever been committed, and neither
  reaches the image. Delete the backup.
- **NATS is exposed on all interfaces with no authentication** (ports 4222 and 8222 in
  compose). Bind to `127.0.0.1` for a laptop demo.
- **Images and CI actions are pinned by tag, not digest** (`python:3.11-slim`,
  `uv:0.7.2`, `actions/*@v4`). Tags can move; for a paper artifact, pin digests.
- **`Homepage` in `pyproject.toml`** points at `HPCI-Lab/yprov4agenticai`, not this
  repository.
- **`asyncio.get_event_loop()` inside coroutines** (`base_agent.py`, `ca1_storage.py`) is
  deprecated; use `get_running_loop()`.
- *(Parked with energy.)* `codecarbon>=2.5,<4` spans versions that behave differently on
  this host: 3.2.7 used a load model, while 3.3.1 selects the Windows EMI interface.

---

## Found while fixing

The audit did not predict these; fixing its findings exposed them. They are listed because an
audit that reports only what it anticipated understates how much was wrong.

- **CA1 could hold a record for ever.** Its timeout guard was armed by `STREAM_END`, so losing
  that one envelope left nothing to fire: never persisted, never reported. Armed by the first
  envelope now. *(fixed with AU2)*
- **CA1 could persist a partial copy at quorum.** Consumers can finish before CA1's own copy
  has fully arrived. It now waits for its own `STREAM_END`. *(fixed with AU2)*
- **The session-naming rule was written twice**, slightly differently, in the containerised
  producer and in `run_local.py`. One function now. *(fixed with AU6)*
- **A record file silently replaced any earlier record of the same session and role**, and
  `run_local.py` never cleared its previous run's records — so a run that emitted fewer of
  them left stale files to be counted as its own. No archive was affected (every archive's
  records fall within 14 s of each other), but the next freeze could have been. *(fixed with
  AU6)*
- **`run_local.py` is outside the CI type check**, and it has two type errors, one of which
  is real: its in-process bus is passed where a `NetworkBus` is expected, with no shared
  protocol saying the two are interchangeable. *(open, AU13)*
- **This audit's own evidence for [6] was wrong** — see the correction under [5].

## Checked and found sound

- **No secret has ever been committed.** Searched the full history for key-shaped strings;
  only `.env.example` was ever added. `.env` is excluded from the Docker build context.
- **Dependencies** are bounded, locked, and installed with `--frozen` in both CI and the image.
- **Windows is in the test matrix**, deliberately, for the line-ending/hash issue.
- **Archive integrity checking works** for what it claims: it detects modified, missing and
  untracked files relative to the manifest. (It is not a *reproduction* check — only
  `verdict-reachability` is re-derived by CI.)

---

## Suggested order of work

```
  [1] gate sees lineage ──┐
  [3] chunk completeness ─┼─► re-run pipeline ──► [4] re-freeze archives ──► [2] rewrite
  [5][6] MC1 + shapes ────┤                          from clean commits         the headline
  [7] model snapshot ─────┘                                                     measure
```

Fix the code first ([1], [3], [5], [6], [7]); then re-run and re-freeze once, from a
clean tree, with the corrected manifest ([4]); then rewrite the paper's headline
measurement against those archives ([2], [9]). Doing it in this order means the archives
are frozen once, not three times.
