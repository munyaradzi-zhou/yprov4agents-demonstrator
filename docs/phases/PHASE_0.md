# Phase 0 — Building the safety net

**Done on:** 8 October 2026 · **Commits:** `7953f46` … `8695e9b` (7) · **Tag:** `v0.1.0-baseline`

This is the plain-language companion to Phase 0 of [`PLAN.md`](../../PLAN.md). It explains
what was done, why, and what it found — written to be read months later without having to
reconstruct the reasoning.

---

## 1. Why Phase 0 exists at all

Phase 1 edits `tracker.py` and `store.py`. Almost everything depends on those two files, so
touching them without a safety net means any mistake shows up as a wrong number in a paper
rather than as a failing check.

So Phase 0 builds **nothing new for the paper**. It builds the equipment that makes the
remaining phases safe:

```
   Before Phase 0                          After Phase 0
   ─────────────                           ─────────────
   no version control                      7 commits + a tagged baseline
   unpinned deps (">=3.7.0")               60 packages pinned in a lockfile
   no tests                                18 tests, 9 of them documenting defects
   out/ overwritten every run              frozen, hash-verified archives
   "did my change break anything?"         one command answers it
   nothing checks anything                 5 CI gates
```

The analogy: before renovating a house you photograph every room. If a wall later looks
wrong you can prove whether you moved it.

---

## 2. What was actually built

### 2.1 Version control with an honest first commit

The very first commit is the project **exactly as it was**, unmodified, tagged
`v0.1.0-baseline`. Nothing was tidied first. That matters because the review in
[`todo.md`](../../todo.md) measured things — "250 relations, 61% mechanical", "8 PIDs cited,
2 resolvable" — and those measurements have to stay reproducible from a commit.

Then six more commits, each one thing:

```
7953f46  import the demonstrator as received        <- tagged v0.1.0-baseline
38874b9  reproducible environment (uv + lockfile)
a1fd2a7  the test suite
b9b838b  experiment archiver + frozen baseline
c41f266  code style + type cleanliness
dc97a7c  make LLM runs real, and stop them lying
8695e9b  CI, pre-commit, lockfile-built image
```

The style commit is separate on purpose. It touched 19 files, and mixing that into a
behaviour change would hide the real edit inside a wall of reformatting.

### 2.2 A reproducible environment

`requirements.txt` said `matplotlib>=3.7.0`. That means "any version from 3.7 upwards" —
including versions that do not exist yet. A reviewer installing in 2027 could get different
numbers from yours and neither of you could explain why.

Replaced with `pyproject.toml` (what we *want*) plus `uv.lock` (what we actually *got* —
all 60 packages, exact versions). Two layers:

```
  pyproject.toml  "matplotlib, at least 3.8, less than 4"     <- intent, human-edited
         │
         │  uv lock
         ▼
  uv.lock         "matplotlib 3.11.2, sha256:a3f9…"           <- fact, machine-written
```

Also added `prov` — an independent W3C PROV parser written by someone else. The project
claims to emit standard PROV-JSON, and you cannot be the judge of your own conformance.

### 2.3 Tests that document what is broken

18 tests. **9 pass. 9 fail on purpose.**

The 9 failures are marked `xfail(strict=True)`, which is the interesting part:

```
   a normal test        a strict-xfail test
   ─────────────        ───────────────────
   passes -> green      passes -> RED  ("the defect is fixed, delete this marker")
   fails  -> red        fails  -> green ("yes, still broken, as recorded")
```

So each known defect is encoded as a test that *expects* to fail. When Phase 1 fixes it,
the suite goes red and tells you to remove the marker. Defects cannot be quietly forgotten,
and they cannot be quietly "fixed" without anyone noticing either.

### 2.4 Archives: results that survive the next run

`out/` is scratch — every run overwrites it. So `python -m eval.archive` copies a run into
`experiments/<name>/` with a manifest recording the commit, the machine, and a SHA-256
fingerprint of every file.

```
  experiments/baseline-pre-review/
    ├── MANIFEST.json       <- commit, machine, and a hash per file
    ├── example_0__cax_prov.json
    ├── …                      (24 provenance documents)
    └── store/
```

`verify` re-hashes everything against the manifest. Tested by changing one byte of a file:

```
  $ python -m eval.archive verify --name baseline-pre-review
  ! modified: RESULTS.md (sha256:e629e1ebae9f… -> sha256:d097ffb17ada…)
  (exit 1)
```

Two archives exist now: `baseline-pre-review` (the original) and `phase0-docker-llm` (the
containerised run with real GPT-4o calls).

### 2.5 `eval.compare`: "did my change alter the provenance?"

The hardest question during a refactor. A plain file diff cannot answer it, because
timestamps legitimately differ between two runs of *identical* code.

So `eval.compare` ignores what is expected to vary (timestamps, activity numbering) and
compares what carries meaning (which typed activities and entities exist, how they relate,
what each decision concluded and why):

```
  $ python -m eval.compare --before experiments/baseline-pre-review --after out --strict

  signal              before     after     delta
  documents               24        24        +0
  activities              78        78        +0
  entities                44        44        +0
  relations              250       250        +0
  justifications          24        24        +0

  No structural differences.
```

This is how the 19-file reformat was *proven* harmless rather than assumed harmless. It is
also wired into CI, so a change that alters the provenance without saying so fails the build.

### 2.6 CI: five gates, cheapest first

```
  push / pull request
         │
         ├─ lint       ruff            seconds
         ├─ types      mypy            seconds
         ├─ test       pytest on Windows AND Linux, Python 3.11 and 3.12
         ├─ pipeline   run it, evaluate it, compare --strict, conformance
         └─ image      build container, assert every agent imports
```

Windows is in the matrix deliberately — see finding D4 below. A Linux-only matrix could
never catch it.

No CI job calls a paid API. LLM runs are produced locally and archived.

---

## 3. What Phase 0 found

Building the equipment uncovered six things. Two were already suspected; four are new.

### 3.1 ⚠️ Not one emitted document is valid W3C PROV-JSON

The review said the non-standard `_yprov4agents_meta` block "will reject or drop". The
measurement is worse: the reference parser throws a hard error on **24 of 24** documents.

```
  ProvJSONException: '_yprov4agents_meta' is not a recognised PROV-N
  record-type keyword (e.g. 'entity', 'activity', 'wasGeneratedBy')
```

A companion test removes *only* that block and the same document parses cleanly — so
nothing else about the serialisation is wrong. The fix is confined to where one block lives.
This moved from a Phase 6 cleanup to a Phase 1 blocker.

```
  {                                   {
    "prefix":   {…},                    "prefix":   {…},
    "agent":    {…},                    "agent":    {…},
    "activity": {…},        ───▶        "activity": {…},
    "entity":   {…},                    "entity":   {…}
    "_yprov4agents_meta": {…}         }
  }                                   
  rejected: 24 of 24                  parses: all 24
```

### 3.2 ⚠️ The provenance cannot tell GPT-4o from a hardcoded string (finding C4)

The whole pipeline was run twice — once with the deterministic stub, once with real GPT-4o
calls — and the output diffed.

```
                              stub run        real GPT-4o run
  typed activities            78              78
  entities / relations        44 / 250        44 / 250
  CAx justification           ╔════════ byte-identical ════════╗
  summary text                stub prose      model prose
  records that a model ran    nothing         nothing
```

CAx asserts the same sentence either way: *"Summary covers all 9 activities and 39 entities
of the source record; grounded in the graph counts and activity labels. Accepted."*

The summary payload changes, and so does its hash. But the **decision** does not move, and
nothing anywhere names the model, the temperature, or even the fact that an API was called.

For a project whose entire thesis is that provenance must capture *why*, this is the gap
stated as sharply as it can be — and it is now demonstrated rather than argued. Re-running
this exact two-run diff after Phase 1 and Phase 4 is itself a publishable result.

### 3.3 Runs were silently pretending to use the model

Two separate problems stacked on top of each other.

**The transport.** This network inspects encrypted traffic via a proxy, which Python did not
trust, so *every* OpenAI call failed and every agent fell back to canned output. Fixed by
verifying against the operating system's certificate store (`truststore`).

**The lie.** The code said:

```python
mode = "LLM" if self.llm else "stub"     # reports the CONFIGURATION
```

That reports what was *set up*, not what *happened*. An agent given a working model whose
call then failed returned canned text and still logged `(LLM)`. A real run did exactly this:

```
  [ca3] LLM narration failed (Connection error.); using computed report
  [ca3] analysed example_2 (LLM) -> ANOMALY        <- but it did not use the LLM
```

Now the mode is an outcome (`llm`, `computed`, or `computed-after-APIConnectionError`), and
`LLM_REQUIRED=1` makes a failed call abort the run instead of falling back. Set it for any
run you intend to publish: the fallback is right for a demo and wrong for a measurement.

### 3.4 New finding D4 — a hash trap waiting for Phase 1

`store.put()` hashes the text in memory, where line breaks are one byte (`\n`). It then
writes the file with `write_text()`, which on Windows turns each into two bytes (`\r\n`).

```
  in memory:   "line one\nline two"        hashed  -> abc123…
  on disk:     "line one\r\nline two"      hashes  -> def456…   MISMATCH
```

Right now nothing breaks, by luck: every stored payload happens to be a single line
(measured — 0 CRLF pairs across all 8 blobs, model output included). It detonates the moment
**either** of two planned things happens: Phase 1 makes the integrity check actually read
from disk, or a model returns multi-paragraph prose.

It would then look like the Phase 1 fix being wrong, which is why it is recorded now. The
real fix is to make the store binary end-to-end.

The same class of bug bit the archiver: git rewrites line endings on commit, so archived
files would have failed verification on a fresh clone. Caught and fixed by storing archives
verbatim. This is why Windows is in the CI matrix.

### 3.5 New finding H4 — "persist after consume" is a race, not a guarantee

CA1 is supposed to save a record only after the other agents have used it, with a timeout as
a safety net. What actually happens is weaker: the timeout competes with the consumers
instead of backing them up.

```
   advertised                          actual
   ──────────                          ──────
   consumers finish                    consumers ─┐
        ↓                                         ├─ whoever finishes first wins
   CA1 saves                           timeout  ──┘
   (timeout only if one dies)
```

Demonstrated by lowering the timeout to 6 seconds: `example_0` saved with **zero**
acknowledgements while `example_2` reached full quorum in the same run. The verdict stayed
`ACCEPT` either way, so nothing surfaced the degradation.

This matters more with real model calls, which take 0.5–3 seconds and can be retried.

There is a silver lining: the quorum-versus-timeout branch is the one place in the whole
system where the justification text genuinely differs according to what happened. It is the
best existing counter-example to the "justifications are templates" criticism, and deserves
to be the paper's worked example.

### 3.6 Smaller things found and fixed

- **A garbage-collection bug.** CA1's timeout guard was created without keeping a reference
  to it, so Python could collect the task before it ran — deleting the safety net that
  exists to make a record unloseable. Found by a lint rule, not by testing.
- **The documented Docker workflow could never have worked.** Compose looks for `.env` next
  to the compose file (`deploy/`), not at the project root, so the API key never reached a
  container. A second bug would have overwritten it with an empty string even if it had.
- **A latent crash in `energy.py`**, found by the type checker, in code the demonstrator
  never runs.
- **Version drift.** The package reported `0.1.0` while the project said `0.2.0`. Now read
  from one source.
- **Your `.env` had been corrupted** — six lines of prose (the text of a chat message) had
  been pasted in, which broke Docker entirely. Cleaned; the key is intact; a backup is at
  `.env.corrupted.bak`.
- **An unrelated process (PID 6248) is running CodeCarbon** and writing `emissions.csv` into
  the project folder. Left running — it is not mine to kill — but it samples CPU power
  continuously and **must be stopped before Phase 5 benchmarks**, which require a quiet
  machine.

---

## 4. How to drive any of this yourself

```bash
# one-time setup
uv sync --all-extras

# run the demonstrator, in one process
uv run python run_local.py --out-dir out --persist-timeout 25

# ... or the real thing: 8 containers around a NATS broker
#     (LLM_REQUIRED=1 makes it abort rather than fall back to canned output)
LLM_REQUIRED=1 docker compose -f deploy/docker-compose.yml up --build

# the paper's tables
uv run python -m eval.evaluate --out-dir out --markdown out/RESULTS.md

# did I change the provenance?
uv run python -m eval.compare --before experiments/baseline-pre-review --after out

# tests  (9 pass, 9 expected-fail — that is correct)
uv run pytest -v

# freeze a run you care about, then prove later it is untouched
uv run python -m eval.archive freeze --name my-run --note "why this exists"
uv run python -m eval.archive verify --name my-run
uv run python -m eval.archive list
```

---

## 5. Where this leaves Phase 1

Phase 1 is "truth in the record" — every fix that changes what the provenance *says*. It now
starts with more than it was planned with:

| | item | status going in |
|---|---|---|
| **new** | move `_yprov4agents_meta` out of the document root | blocker; 24/24 documents invalid |
| **new** | make the store binary end-to-end (D4) | must land *with* the C2 fix, not after |
| **new** | cancel the timeout guard on quorum (H4) | and stop reporting a no-ack save as plain ACCEPT |
| B4 | explicit lineage; drop the index-order chains | unchanged |
| C2 + D1/D2 | real round-trip read, `load()`, merged manifest | unchanged |
| C1 | justifications that reflect what was checked | now motivated by the C4 measurement |
| A5/A2 | MC1 gains a DecisionActivity | unchanged |
| G1–G5 | the CA3 analytics bugs | unchanged |

Each has a failing test waiting for it, and `eval.compare` will show exactly what each fix
changed in the provenance — which is the evidence Phase 6 needs to rewrite the paper's
tables honestly.
