# Demonstrator evaluation — 24 emitted PROV records

Source: `out\replay`

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

| Agent | Decisions | With `yp:justification` | Coverage | Justification bytes | Share of the record |
|---|--:|--:|--:|--:|--:|
| CA1 StorageAgent | 2 | 2 | 100% | 284 B each | 4.0% |
| CAx NL_Summarizer | 2 | 2 | 100% | 292 B each | 4.1% |
| CA2 Metrics_Visualizer | 2 | 2 | 100% | 430 B each | 6.6% |
| CA3 Analytics_Agent | 2 | 2 | 100% | 738 B each | 9.4% |
| MC1 ComplianceChecker | 8 | 8 | 100% | 240 B each | 5.4% |
| MC3 AuditabilityAgent | 8 | 8 | 100% | 356 B each | 7.8% |
| **All** | **24** | **24** | **100%** | **8,260 B** | **6.4%** |

**The justifications are 6.4% of these records, serialised** — 8,260 of 129,408. That is the storage cost of the field this project argues for, in the records the agents emit; E6c's larger share is a synthetic record that justifies every step.

**24/24 records conform to `vocab/shapes.ttl`**, validated with pyshacl over RDF converted by the reference `prov` library. The rule being checked — every decision carries a non-empty justification — is declared in the vocabulary rather than written into the agent that enforces it, so this is a statement about a standard and not about an implementation. Asking the two to agree found a real divergence: MC1 accepted a justification of pure whitespace and the shape did not.

## E3 — Strategy A (callbacks) vs Strategy B (explicit instrumentation)

*Not measured for this run: no observation stream at `out\baseline\_events.json`. Produce one with `python run_local.py --baseline`, which records what a framework callback handler would have seen over the same work.*

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

