## E3 — Strategy A (callbacks) vs Strategy B (explicit instrumentation)

Same agents, same input, **same run**. Strategy A is a callback handler fed exactly what a framework hands one: 1152 step events carrying the node name, timings, input and output payloads, and errors (`yprov4agenticai.observer.StepEvent`). Both A columns are reconstructed from that single recorded stream.

| Signal | A, zero config | A, with type map | B |
|---|--:|--:|--:|
| Documents emitted | 288 | 288 | 288 |
| Activities | 1152 | 1152 | 1152 |
| …of them typed | 0 | 1152 | 1152 |
| Entities | 648 | 648 | 936 |
| …of them typed | 0 | 0 | 936 |
| …carrying a resolvable pointer (`yp:pid`) | 0 | 0 | 96 |
| `used` | 864 | 864 | 864 |
| `wasGeneratedBy` | 648 | 648 | 648 |
| `wasInformedBy` | 864 | 864 | 864 |
| `wasDerivedFrom` | 360 | 360 | 360 |
| `wasAssociatedWith` | 1152 | 1152 | 1152 |
| **Relations, total** | 3888 | 3888 | 3888 |
| Decisions carrying `yp:verdict` | 0 | 288 | 288 |
| Decisions carrying **`yp:justification`** | 0 | 0 | 288 |
| Accepted by the reference `prov` parser | 288 | 288 | 288 |

### How to read this

**A is an upper bound, not a measurement of a particular handler.** The event stream it reads is built from the declarations the explicit instrumentation made: the entity each step said it read, the entity each step said it wrote. A real callback handler is handed a node's whole input payload and must guess which part mattered — it would over-report `used` and could not tell a load-bearing input from an incidental one. Conceding A every structural fact B declared is what makes the last row safe: the claim is not that callbacks recover less structure, it is that the rationale is unreachable *even at the ceiling*.

**Structure is recoverable, and the baseline recovers it.** A sees every step, times it, attributes it, and reconstructs 3888 relations against B's 3888. Any claim that callback instrumentation yields nothing is false — three of the four zeros this table used to carry were wrong.

**Types are not free.** With no configuration A types 0 activities; with 10 hand-written label patterns it types 1152. That gap is the map, and the map is a per-system artifact someone writes and maintains. It works here only because these labels are descriptive. B declares the type at the call site and needs no map.

**Entity identity is the quiet loss.** A is handed payloads, not references, so it mints identity from content: 648 entities against B's 936, with no collisions in this run. None are typed, and none carry a resolvable pointer — `yp:pid`/`yp:contentHash` has no analogue in a reconstruction that never saw where a payload was stored, so the pointers-not-payloads design is not available to A at all.

**Verdicts are often recoverable; justifications verbatim, never.** Searching everything the handler could read — 2664 strings, 158,241 characters — recovers 288 of 288 verdicts and 0 of 288 justifications *verbatim*. The verdicts come back only because these agents interpolate them into node labels: a graph whose decision node is named `decide` would yield none. The verbatim zero is close to guaranteed for any sentence assembled at run time, so it is reported as what it is and not rested on. The table below asks the harder question.

### E3b — The rationale, fact by fact

Each justification is split into the checkable facts it cites — multi-digit figures, hashes, quoted names, files and agent roles — and each fact is searched for, as a whole token, in everything the handler could read. This over-counts: a role name in the stream is not knowledge that the role acknowledged.

| Agent | Decisions | Verbatim | Facts cited | Facts visible to A | All facts visible |
|---|--:|--:|--:|--:|--:|
| ca1 | 24 | 0 | 168 | 144 (86%) | 0 |
| ca2 | 24 | 0 | 96 | 96 (100%) | 24 |
| ca3 | 24 | 0 | 212 | 118 (56%) | 0 |
| cax | 24 | 0 | 92 | 68 (74%) | 0 |
| **operational** | 96 | 0 | 568 | 426 (75%) | 24 |
| mc1 | 96 | 0 | 96 | 96 (100%) | 96 |
| mc3 | 96 | 0 | 96 | 96 (100%) | 96 |
| **meta** | 192 | 0 | 192 | 192 (100%) | 192 |

**The inputs to a decision can be observable; the rule that combined them is not.** Across the 96 operational decisions, 75% of the facts the justifications cite were somewhere in the callback stream, and for 24 of 96 every cited fact was. What A lacks for those is which facts were combined, against what threshold, to reach the verdict — the decision rule, which was never a value the step passed anywhere.

**The 192 meta-agent justifications are restatements**, and are reported apart for that reason: they describe structure A already reconstructs in full (100% of their facts visible). Counting them would inflate any headline built on the operational agents.

