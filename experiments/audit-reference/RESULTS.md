# Demonstrator evaluation — 24 emitted PROV records

Source: `out\ref`

## E1 — Structural coverage (per agent, mean over sessions)

| Agent | Records | Activities | Entities | Relations |
|---|--:|--:|--:|--:|
| CA1 StorageAgent | 2 | 6.0 | 4.0 | 21.0 |
| CAx NL_Summarizer | 2 | 6.0 | 4.0 | 21.0 |
| CA2 Metrics_Visualizer | 2 | 6.0 | 3.0 | 19.0 |
| CA3 Analytics_Agent | 2 | 6.0 | 4.0 | 21.0 |
| MC1 ComplianceChecker | 8 | 3.0 | 3.0 | 10.0 |
| MC3 AuditabilityAgent | 8 | 3.0 | 3.0 | 10.0 |

### E1b — Relations, by what established them

A single relation total mixes three kinds of claim and one piece of bookkeeping, which is why it should not be quoted on its own (todo.md B3). **Observed** edges are what a step said it read or wrote. **Derived** edges follow from those by PROV's own definition of `wasInformedBy`. **Declared** edges are derivations a step asserted. **Attributed** edges are one per activity, to the agent that ran it — mechanical by construction, and the largest single contributor to the total.

| How it was established | Relations | Share | Which |
|---|--:|--:|---|
| Observed | 126 | 39% | `used` 72, `wasGeneratedBy` 54 |
| Derived | 72 | 22% | `wasInformedBy` 72 |
| Declared | 30 | 9% | `wasDerivedFrom` 30 |
| Attributed | 96 | 30% | `wasAssociatedWith` 96 |
| **Total** | **324** | 100% | |

**The structural claim is 228, not 324.** The remaining 96 are agent attributions: one per activity, so they measure how many steps ran rather than how they were connected. Report the split.

## E2 — Instrumentation coverage (not the headline claim)

Read this as a conformance check, not a result. Every decision point in this system is instrumented, which is worth establishing — but the code that counts decisions is the code that justifies them, so no run could produce another number. The figure says nothing about whether a justification is *informative*; see E3 for the claim that does the work.

| Agent | Decisions | With `yp:justification` | Coverage |
|---|--:|--:|--:|
| CA1 StorageAgent | 2 | 2 | 100% |
| CAx NL_Summarizer | 2 | 2 | 100% |
| CA2 Metrics_Visualizer | 2 | 2 | 100% |
| CA3 Analytics_Agent | 2 | 2 | 100% |
| MC1 ComplianceChecker | 8 | 8 | 100% |
| MC3 AuditabilityAgent | 8 | 8 | 100% |
| **All** | **24** | **24** | **100%** |

**24/24 records conform to `vocab/shapes.ttl`**, validated with pyshacl over RDF converted by the reference `prov` library. The rule being checked — every decision carries a non-empty justification — is declared in the vocabulary rather than written into the agent that enforces it, so this is a statement about a standard and not about an implementation. Asking the two to agree found a real divergence: MC1 accepted a justification of pure whitespace and the shape did not.

## E3 — Strategy A (callbacks) vs Strategy B (explicit instrumentation)

Same agents, same input, **same run**. Strategy A is a callback handler fed exactly what a framework hands one: 96 step events carrying the node name, timings, input and output payloads, and errors (`yprov4agenticai.observer.StepEvent`). Both A columns are reconstructed from that single recorded stream.

| Signal | A, zero config | A, with type map | B |
|---|--:|--:|--:|
| Documents emitted | 24 | 24 | 24 |
| Activities | 96 | 96 | 96 |
| …of them typed | 0 | 96 | 96 |
| Entities | 54 | 54 | 78 |
| …of them typed | 0 | 0 | 78 |
| …carrying a resolvable pointer (`yp:pid`) | 0 | 0 | 8 |
| `used` | 72 | 72 | 72 |
| `wasGeneratedBy` | 54 | 54 | 54 |
| `wasInformedBy` | 72 | 72 | 72 |
| `wasDerivedFrom` | 30 | 30 | 30 |
| `wasAssociatedWith` | 96 | 96 | 96 |
| **Relations, total** | 324 | 324 | 324 |
| Decisions carrying `yp:verdict` | 0 | 24 | 24 |
| Decisions carrying **`yp:justification`** | 0 | 0 | 24 |
| Accepted by the reference `prov` parser | 24 | 24 | 24 |

### How to read this

**A is an upper bound, not a measurement of a particular handler.** The event stream it reads is built from the declarations the explicit instrumentation made: the entity each step said it read, the entity each step said it wrote. A real callback handler is handed a node's whole input payload and must guess which part mattered — it would over-report `used` and could not tell a load-bearing input from an incidental one. Conceding A every structural fact B declared is what makes the last row safe: the claim is not that callbacks recover less structure, it is that the rationale is unreachable *even at the ceiling*.

**Structure is recoverable, and the baseline recovers it.** A sees every step, times it, attributes it, and reconstructs 324 relations against B's 324. Any claim that callback instrumentation yields nothing is false — three of the four zeros this table used to carry were wrong.

**Types are not free.** With no configuration A types 0 activities; with 10 hand-written label patterns it types 96. That gap is the map, and the map is a per-system artifact someone writes and maintains. It works here only because these labels are descriptive. B declares the type at the call site and needs no map.

**Entity identity is the quiet loss.** A is handed payloads, not references, so it mints identity from content: 54 entities against B's 78, with no collisions in this run. None are typed, and none carry a resolvable pointer — `yp:pid`/`yp:contentHash` has no analogue in a reconstruction that never saw where a payload was stored, so the pointers-not-payloads design is not available to A at all.

**The verdict is often recoverable; the reasoning never is.** Searching everything the handler could read — 222 strings, 12,425 characters — recovers 24 of 24 verdicts and **0 of 24 justifications**. The verdicts come back only because these agents interpolate them into node labels, which is a property of the labels and not of the observation channel: a graph whose decision node is named `decide` would yield none. The justifications come back under no labelling convention at all. Each is prose composed at the moment of decision out of evidence the step never returned — a local variable, and the callback layer is outside the function.

*This is the single earned zero, and it is worth more than the four unearned ones it replaces: it isolates exactly what explicit instrumentation buys.*

## E4 — Reflexivity (the system tracks its own trackers)

| Operational agent | Governed by |
|---|---|
| CA1 StorageAgent | MC1 ComplianceChecker, MC3 AuditabilityAgent |
| CA2 Metrics_Visualizer | MC1 ComplianceChecker, MC3 AuditabilityAgent |
| CA3 Analytics_Agent | MC1 ComplianceChecker, MC3 AuditabilityAgent |
| CAx NL_Summarizer | MC1 ComplianceChecker, MC3 AuditabilityAgent |

## E5 — Verdict tally

| Agent:verdict | Count |
|---|--:|
| `ca1:ACCEPT` | 2 |
| `ca2:REVISE` | 2 |
| `ca3:ANOMALY` | 2 |
| `cax:ACCEPT` | 2 |
| `mc1:PASS` | 8 |
| `mc3:COMPLETE` | 8 |

