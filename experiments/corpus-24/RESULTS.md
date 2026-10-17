# Demonstrator evaluation — 288 emitted PROV records

Source: `out\corpus`

## E1 — Structural coverage (per agent, mean over sessions)

| Agent | Records | Activities | Entities | Relations |
|---|--:|--:|--:|--:|
| CA1 StorageAgent | 24 | 6.0 | 4.0 | 21.0 |
| CAx NL_Summarizer | 24 | 6.0 | 4.0 | 21.0 |
| CA2 Metrics_Visualizer | 24 | 6.0 | 3.0 | 19.0 |
| CA3 Analytics_Agent | 24 | 6.0 | 4.0 | 21.0 |
| MC1 ComplianceChecker | 96 | 3.0 | 3.0 | 10.0 |
| MC3 AuditabilityAgent | 96 | 3.0 | 3.0 | 10.0 |

### E1b — Relations, by what established them

A single relation total mixes three kinds of claim and one piece of bookkeeping, which is why it should not be quoted on its own (todo.md B3). **Observed** edges are what a step said it read or wrote. **Derived** edges follow from those by PROV's own definition of `wasInformedBy`. **Declared** edges are derivations a step asserted. **Attributed** edges are one per activity, to the agent that ran it — mechanical by construction, and the largest single contributor to the total.

| How it was established | Relations | Share | Which |
|---|--:|--:|---|
| Observed | 1512 | 39% | `used` 864, `wasGeneratedBy` 648 |
| Derived | 864 | 22% | `wasInformedBy` 864 |
| Declared | 360 | 9% | `wasDerivedFrom` 360 |
| Attributed | 1152 | 30% | `wasAssociatedWith` 1152 |
| **Total** | **3888** | 100% | |

**The structural claim is 2736, not 3888.** The remaining 1152 are agent attributions: one per activity, so they measure how many steps ran rather than how they were connected. Report the split.

## E2 — Instrumentation coverage (not the headline claim)

Read this as a conformance check, not a result. Every decision point in this system is instrumented, which is worth establishing — but the code that counts decisions is the code that justifies them, so no run could produce another number. The figure says nothing about whether a justification is *informative*; see E3 for the claim that does the work.

| Agent | Decisions | With `yp:justification` | Coverage | Justification bytes | Share of the record |
|---|--:|--:|--:|--:|--:|
| CA1 StorageAgent | 24 | 24 | 100% | 286 B each | 3.7% |
| CAx NL_Summarizer | 24 | 24 | 100% | 294 B each | 3.8% |
| CA2 Metrics_Visualizer | 24 | 24 | 100% | 443 B each | 6.2% |
| CA3 Analytics_Agent | 24 | 24 | 100% | 584 B each | 7.0% |
| MC1 ComplianceChecker | 96 | 96 | 100% | 240 B each | 5.1% |
| MC3 AuditabilityAgent | 96 | 96 | 100% | 356 B each | 7.3% |
| **All** | **288** | **288** | **100%** | **95,798 B** | **5.7%** |

**The justifications are 5.7% of these records, serialised** — 95,798 of 1,670,834. That is the storage cost of the field this project argues for, in the records the agents emit; E6c's larger share is a synthetic record that justifies every step.

**288/288 records conform to `vocab/shapes.ttl`**, validated with pyshacl over RDF converted by the reference `prov` library. The rule being checked — every decision carries a non-empty justification — is declared in the vocabulary rather than written into the agent that enforces it, so this is a statement about a standard and not about an implementation. Asking the two to agree found a real divergence: MC1 accepted a justification of pure whitespace and the shape did not.

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
| `ca1:ACCEPT` | 24 |
| `ca2:REVISE` | 24 |
| `ca3:ANOMALY` | 24 |
| `cax:ACCEPT` | 24 |
| `mc1:PASS` | 96 |
| `mc3:COMPLETE` | 96 |

