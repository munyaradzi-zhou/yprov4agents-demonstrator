# Phase 6 — The vocabulary, and the truth pass

> Follow-along notes. Plain language, with diagrams. The artifacts are
> [`../../vocab/`](../../vocab/); the validator is
> [`../../eval/validate_shapes.py`](../../eval/validate_shapes.py).
>
> Previous: [`PHASE_5.md`](PHASE_5.md) · Plan: [`../../PLAN.md`](../../PLAN.md) Phase 6

---

## The last phase, and the one that was easiest to skip

The project claims three contributions. Two of them were running code. The third —
**a vocabulary for behavioural provenance** — existed only as strings inside f-strings.

```
   BEFORE                               AFTER
   ──────                               ─────
   "yp:DecisionActivity"                vocab/yprov4agents.ttl
     a string in tracker.py               · 81 defined terms
     with no definition                   · each hung off PROV-O
     no domain, no range                  · domains, ranges, comments
     nothing to validate against        vocab/shapes.ttl
                                           · 14 named SHACL constraints
                                           · applied by any SHACL engine
```

A naming convention is not a vocabulary. Nobody outside this repository could have
validated a record, disagreed with a definition, or extended the terms — which is what a
contribution of this kind has to allow.

---

## Two rules decided what went in

**Only terms the system actually emits.** The inventory came from the frozen archives under
`experiments/`, not from the documentation. That matters because this project has already
shipped the opposite mistake once: Phase 2 existed because `INSTRUMENTATION.md` described
an API that did not exist. An ontology written from the docs would be the same defect in a
different file.

**Standard terms wherever a standard term exists.** All six lineage relations are PROV's —
`used`, `wasGeneratedBy`, `wasDerivedFrom`, `wasInformedBy`, `wasAssociatedWith`,
`actedOnBehalfOf`. Nothing re-invented. The `yp:` namespace adds exactly two things PROV
has no term for:

```
            prov:Activity                          prov:Entity
                  │                                     │
      yp:AgentLifecycleActivity              yp:TaskEntity · yp:ResponseEntity
       ├── yp:ReceiveTaskActivity            yp:ToolResultEntity · yp:QualityEntity
       ├── yp:PlanningActivity
       ├── yp:ToolCallActivity        ← 1. the typed steps of a decision lifecycle
       ├── yp:AnalysisActivity
       ├── yp:PersistActivity
       ├── yp:ObservationActivity
       ├── yp:DecisionActivity ──────── yp:justification   ← 2. the reasoning
       ├── yp:ResponseActivity
       └── yp:GovernanceActivity
            ├── yp:ComplianceCheckActivity
            └── yp:AuditActivity
```

Everything else in the file — model provenance, energy, the evidence properties, the record's
self-description — is a property of one of those, defined so a consumer knows the type and
the meaning rather than guessing from the name.

---

## The shapes, and what declaring a standard immediately found

MC1 has always enforced a structural standard. The problem was *where* it lived: four
hand-written predicates inside one agent, which makes it a property of an implementation
rather than something anyone else can check against.

`vocab/shapes.ttl` declares it. Then the obvious question becomes askable for the first
time: **do the agent and the declaration agree?**

They did not.

```
   the rule:  "every decision carries a non-empty justification"

   tracker.decide()        if not justification.strip():  raise     ← rejects "   "
   eval.evaluate           if str(...).strip():                     ← rejects "   "
   mc1._check()            if not a.get("yp:justification"):        ← ACCEPTS "   "
                                                                       ↑
                                                              three implementations,
                                                              one of them different
```

A justification of pure whitespace passed MC1's check, because the check tested for a
truthy value rather than for content. The shape caught it — and in fairness, the *first*
version of the shape did not either: `sh:minLength 1` is satisfied by two spaces. Adding
`sh:pattern "\S"` fixed the shape, and stripping fixed the agent.

> **The lesson is the argument for the whole file.** A rule written once, in one place,
> cannot disagree with itself. A rule written three times in three languages will.

---

## Making sure the shapes have teeth

A shape suite that passes everything is decoration, and would be worse than nothing because
it would report perfect conformance over an empty check. So each shape is tested by
mutating a conforming record in the specific way that shape exists to catch:

| Mutation | Caught by |
|---|---|
| remove a justification | `decision-justified` |
| replace it with whitespace | `decision-justified` (the `sh:pattern`) |
| remove a verdict | `decision-concluded` |
| remove a start time | `activity-started` |
| remove an end time | `activity-ended` |
| set granularity to `SUMMARY` | `granularity-declared` |
| remove a content hash | `pointer-verifiable` |
| change it to `md5:deadbeef` | `pointer-verifiable` (the pattern) |

Eight mutations, eight objections. The shapes are also validated *as SHACL*, against
SHACL's own metashapes — a malformed shape file silently validates nothing.

### How a PROV-JSON record reaches a SHACL engine

```
   record_prov.json
        │
        │  prov.ProvDocument.deserialize()      ← the reference library, not ours
        ▼
   RDF (turtle)
        │
        │  pyshacl, with vocab/shapes.ttl + yprov4agents.ttl
        ▼
   conformance report       →   24/24 records conform
```

Using the reference library for the conversion is deliberate: a record that will not
round-trip through an independent PROV implementation is not conformant PROV, so the
validation inherits the E0 conformance gate for free.

One thing worth knowing if you read the shapes: **PROV-JSON's key names are not PROV-O's
property names.** The JSON says `prov:startTime`; the ontology says `prov:startedAtTime`.
My first shapes used the JSON names and reported 0 of 24 conforming — which looked like a
catastrophic defect in the records and was a defect in the shapes. Worth checking which
side is wrong before believing a result like that.

---

## What this buys E2

E2 used to be labelled "the headline claim" at 24/24 = 100%. It cannot be a result: the
code that counts decisions is the code that justifies them, so no run could produce another
number. Phase 1 relabelled it an instrumentation-coverage check, which was honest but left
it thin.

The shapes give it something to stand on:

```
   coverage      24 of 24 decisions carry a justification
                 ↑ judged by us, against our own definition — a conformance check

   conformance   24 of 24 records satisfy vocab/shapes.ttl
                 ↑ judged by pyshacl, against a declared standard — a different claim
```

The denominator is still tautological. **The judge is not.** That is the available
improvement, and it is a real one.

---

## The truth pass, which found three stale claims

Going through every document and tracing each claim to code:

**`docs/DESIGN.md` still carried the four wrong Strategy A zeros** — 0 typed activities,
0 entities, 0 relations, no PROV-JSON. Phase 3 measured all four and three were false.
Replaced with the measured table and a sentence saying it used to be wrong.

**The README's verdict table had two errors.** It credited CAx with an `ESCALATE` it cannot
emit, and credited MC3 with no verdict at all when it emits `COMPLETE`/`PARTIAL`. The table
now also marks which verdicts have been *observed* in an archived run (bold) versus which
are merely reachable in code — because a verdict that has never fired is a claim, not a
result.

**The reflexivity claim needed its qualifier.** "The system tracks its own trackers" is
true one level deep, and the README's opening paragraph did not say so.

---

## A4, resolved by deciding the claim rather than the code

This was the one item flagged as the author's decision rather than the implementer's, and
it is worth explaining why it resolved the way it did.

```
   producer.*  ──→  operational agents  ──→  agent.lifecycle.*
                                                    │
                                                    ▼
                                            meta agents  ──→  governance.*
                                                                   │
                                                                   ✗ nothing subscribes
```

Nothing consumes `governance.*`, so the governance layer is not itself governed in a run.
The options were: close the loop, or narrow the claim.

**Closing the loop does not terminate.** An agent that governs governance output emits a
record that is itself governance output, and the next pass has the same shape. A depth
counter would work, and it would also be a rule someone can forget to check. Publishing to
a prefix nobody subscribes to makes the recursion impossible *by construction*, which is a
stronger guarantee than a guard.

So the claim is narrowed, with the reason attached — and the bound became a **tested
invariant** rather than a documented gap:

```python
def test_governance_output_is_terminal_by_design() -> None:
    assert not governance_consumers, (
        "an agent now consumes governance output. Unbounded reflexivity does not "
        "terminate — if this is deliberate, the depth must be bounded in the subject "
        "and this test should assert that bound."
    )
```

What still holds is the capability half: **MC1 satisfies its own standard.** Before Phase 1
it did not — the compliance agent demanded a justified decision activity and emitted none,
so its own output would have failed its own check. Nothing but self-application finds that,
and it is a sharper statement of reflexivity than a governance table.

**The suite now has no strict-xfails.** Every review finding is either fixed or has an
invariant asserting the bound.

---

## What changed, file by file

| File | What happened |
|---|---|
| `vocab/yprov4agents.ttl` | **new** — 81 terms, every emitted one taken from a frozen archive |
| `vocab/shapes.ttl` | **new** — 14 named SHACL constraints, the declared standard |
| `eval/validate_shapes.py` | **new** — PROV-JSON → RDF → pyshacl, with a report |
| `runtime/agents/mc1_compliance.py` | cites `vocab/shapes.ttl`, records it as `yp:standard`, and strips before testing a justification |
| `eval/evaluate.py` | E1b splits the relation total; E2 reframed and given the SHACL figure |
| `tests/test_shapes.py` | **new** — 15 tests: valid Turtle, valid SHACL, every emitted term defined, eight mutations caught, and the agent-versus-shapes agreement |
| `tests/test_reflexive_governance.py` | the A4 xfail replaced by the invariant it was documenting |
| `README.md` | verdict table corrected and marked observed-vs-reachable; reflexivity qualified |
| `docs/DESIGN.md` | §5's results table replaced with measured figures; **§5b limitations added** |
| `paper/OUTLINE.md` | E2 rewritten, E1b added, A4 resolved, F10 added |
| `.github/workflows/ci.yml` | SHACL validation is now a gate |

---

## How to check any of this yourself

```bash
uv sync --extra eval --extra dev

# validate a run against the declared shapes
uv run python -m eval.validate_shapes --prov-dir out

# the shapes' own tests, including the eight mutations and the agent-vs-shapes check
uv run pytest tests/test_shapes.py -v --prov-dir experiments/phase6-vocabulary

# see a shape object: strip a justification and revalidate
uv run python -m eval.validate_shapes --prov-dir out --verbose
```

---

## Where this leaves the project

**All six phases are complete, and every finding in the review register is resolved.**
31 October was the deadline for *starting* the overhead study; it finished on the 9th.

| Phase | | What it established |
|---|---|---|
| 0 | ✅ | the safety net — and six defects, four of them new |
| 1 | ✅ | truth in the record: 0/24 → 24/24 conformant, fabricated lineage removed |
| 2 | ✅ | the documented API exists; 96/96 zero-duration activities → 0/96 |
| 3 | ✅ | Strategy A measured, not asserted; the one earned zero isolated |
| 4 | ✅ | an LLM step repeatable from the record; a negative result on energy |
| 5 | ✅ | what it costs — and two library defects the measurement found |
| 6 | ✅ | the vocabulary as an artifact; the truth pass; A4 decided |

What remains is writing, and two things worth deciding with a supervisor rather than alone:
whether a bounded second level of governance is worth building for the paper, and which of
the three remaining fault-injection cases (E5) to run so that every advertised verdict has
been observed rather than merely reached.
