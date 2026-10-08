# Paper — outline and results slots

> **Working title:** *Reflexive Provenance for Agentic AI: Capturing Why, Not Only What*
> **Target:** e-Science Agentic Workshop (IEEE, 6pp) — Paper 1 of the two-paper plan
> **Status:** demonstrator built and producing all evaluation numbers; prose to be written
> **Hard constraint:** draft by 8 November 2026 (contract end)

---

## The claim, in one sentence

> Behavioural provenance for agentic systems has an **irreducible explicit component** —
> the rationale for a decision is a local variable at the moment of decision, so no
> callback, span, or post-hoc query can recover it — and a small explicit instrumentation
> layer over W3C PROV captures it, at a cost we measure, for every agent in a running
> multi-agent system including the agents that govern the others.

Three defensible contributions:

- **C1 — Capture, not retrieve.** A structural argument, with a measured A-vs-B result,
  that decision rationale is unreachable by external observation.
- **C2 — A reflexive multi-agent provenance architecture.** Operational agents emit typed
  PROV about themselves; meta agents consume *that* and emit PROV in turn. Demonstrated
  running, not proposed.
- **C3 — `yp:` ontology + the 6-step lifecycle as a reusable template**, instantiated by
  four heterogeneous agents (deterministic, LLM, hybrid) and governed uniformly.

---

## Structure (6 pages)

| § | Content | Status |
|---|---|---|
| 1 | **Introduction** — agentic systems are being deployed into science infrastructure; we can say what they did, not why; the EU AI Act asks why | reuse Paper-1 draft |
| 2 | **Background** — W3C PROV; the yProv ecosystem (yProv4ML, yProv4WFs, yProvStore); agentic lifecycles | reuse |
| 3 | **Related work** — PROV-AGENT (Souza et al.); AER/Vispute non-identifiability; observability stacks (LangSmith, Langfuse, OpenLLMetry, OTel GenAI semconv); agentic-pattern literature | reuse + refresh |
| 4 | **The `yp:` ontology and the 6-step lifecycle** — activity/entity classes; `yp:justification` (DD-001); 3-phase grouping | reuse |
| 5 | **Instrumentation: two layers** — why Layer 1 can be automatic and Layer 2 cannot; the API; framework-agnosticism via adapters | **from `docs/INSTRUMENTATION.md`** |
| 6 | **The demonstrator** — bus, producer, 4 operational + 2 meta agents, persist-after-consume, reflexive emission | **from `docs/DESIGN.md`** |
| 7 | **Evaluation** — E1–E5 below | **numbers ready** |
| 8 | **Discussion & limitations** — what the demonstrator does not yet do | to write |
| 9 | **Conclusion & future work** — the governance actuation loop; HPC deployment | to write |

---

## Evaluation — every number comes from `eval/evaluate.py`

Reproducible: `python run_local.py && python -m eval.evaluate --out-dir out`.

### E1 — Structural coverage per agent

| Agent | Records | Activities | Entities | Relations |
|---|--:|--:|--:|--:|
| CA1 StorageAgent | 2 | 6.0 | 3.0 | 21.0 |
| CAx NL_Summarizer | 2 | 6.0 | 2.0 | 19.0 |
| CA2 Metrics_Visualizer | 2 | 5.0 | 2.0 | 16.0 |
| CA3 Analytics_Agent | 2 | 6.0 | 3.0 | 21.0 |
| MC1 ComplianceChecker | 8 | 2.0 | 1.0 | 5.0 |
| MC3 AuditabilityAgent | 8 | 2.0 | 2.0 | 7.0 |

### E2 — Justification coverage (**the headline**)

**24 / 24 decisions carry a non-empty `yp:justification` — 100%**, across deterministic,
LLM, and hybrid agents, and across both operational and meta layers.

### E3 — Strategy A vs Strategy B (same agents, same input)

| Signal | Strategy A (callbacks) | Strategy B |
|---|--:|--:|
| Typed activities | 0 | 78 |
| Typed entities | 0 | 44 |
| Lineage relations | 0 | 250 |
| Decisions with `yp:justification` | **0 — impossible** | 24 |
| W3C PROV-JSON | no | yes |

### E4 — Reflexivity

All four operational agents are governed by both meta agents; the meta agents emit their
own typed PROV. Visible in the artifact names: `…__cax_prov.json` → `…__cax__mc1_prov.json`.

### E5 — Verdicts

`ca1:ACCEPT ×2` · `ca3:ANOMALY ×2` · `mc1:PASS ×8`.

### E6 — Instrumentation overhead *(to measure)*

Wall-clock and records/sec with the tracker enabled vs disabled. Basis exists
(`exp_instr_overhead.py` in the main repo); port and run on this demonstrator.
**This is the one evaluation gap.**

### A qualitative result worth a paragraph

On its first run CA3 reported genuine defects in **yProv4ML's own output**: 8 of 9
activities declare `prov:startedAtTime` but no end time, and entity typing coverage is
82.1%. An analytics agent over provenance finds real problems in real provenance — useful
evidence that the agent is doing work, not theatre.

---

## Figures

| # | Figure | Source |
|---|---|---|
| F1 | Two-layer instrumentation (what/why) | `docs/INSTRUMENTATION.md` §2 |
| F2 | Demonstrator architecture (bus, agents, store behind CA1) | `docs/DESIGN.md` §4 |
| F3 | 6-step lifecycle in 3 phases, S5 starred | existing deck slide 5 |
| F4 | Persist-after-consume sequence (hold → acks → quorum → persist) | run log |
| F5 | A-vs-B bar chart | E3 |
| F6 | Reflexivity chain (`cax` → `mc1`/`mc3`) | artifact names |

---

## To do

1. **Measure E6** (overhead) — the only missing number.
2. Write §8 and §9; refresh §3 against the scouting results.
3. Render F1–F6.
4. Decide authorship and venue date with Sandro.

## Honest limitations (state them; they are defensible)

- In-process/single-broker; no multi-node HPC deployment yet.
- CA3 is post-mortem; in-flight continuous-query analytics is future work.
- The meta layer observes and gates but does not yet reconfigure operational agents.
- Two provenance records from one producer family (yProv4ML); yProv4WFs untested here,
  though nothing in the agents is ML-specific.
