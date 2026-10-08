# Demonstrator evaluation — 24 emitted PROV records

Source: `out`

## E1 — Structural coverage (per agent, mean over sessions)

| Agent | Records | Activities | Entities | Relations |
|---|--:|--:|--:|--:|
| CA1 StorageAgent | 2 | 6.0 | 3.0 | 21.0 |
| CAx NL_Summarizer | 2 | 6.0 | 2.0 | 19.0 |
| CA2 Metrics_Visualizer | 2 | 5.0 | 2.0 | 16.0 |
| CA3 Analytics_Agent | 2 | 6.0 | 3.0 | 21.0 |
| MC1 ComplianceChecker | 8 | 2.0 | 1.0 | 5.0 |
| MC3 AuditabilityAgent | 8 | 2.0 | 2.0 | 7.0 |

## E2 — Justification coverage (the headline claim)

| Agent | Decisions | With `yp:justification` | Coverage |
|---|--:|--:|--:|
| CA1 StorageAgent | 2 | 2 | 100% |
| CAx NL_Summarizer | 2 | 2 | 100% |
| CA2 Metrics_Visualizer | 2 | 2 | 100% |
| CA3 Analytics_Agent | 2 | 2 | 100% |
| MC1 ComplianceChecker | 8 | 8 | 100% |
| MC3 AuditabilityAgent | 8 | 8 | 100% |
| **All** | **24** | **24** | **100%** |

## E3 — Strategy A (callbacks) vs Strategy B (explicit instrumentation)

Same agents, same input. Strategy A figures are structural: a framework callback fires *after* a node returns and cannot read that node's local variables, so the decision rationale is unreachable by construction.

| Signal | Strategy A | Strategy B (this demo) |
|---|--:|--:|
| Typed activities | 0 | 78 |
| Typed entities | 0 | 44 |
| Lineage relations | 0 | 250 |
| Decisions with `yp:justification` | 0 (impossible) | 24 |
| W3C PROV-JSON output | no | yes |

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
| `ca3:ANOMALY` | 2 |
| `mc1:PASS` | 8 |

