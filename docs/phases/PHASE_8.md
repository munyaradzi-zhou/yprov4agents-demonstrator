# Phase 8 — The paper, reviewed as a reviewer would

> **Date:** 17 October 2026 · **Branch:** `paper-review` · **Review:** [`paperfeedback.md`](../../paperfeedback.md)
> · **Archives:** `decision-replay`, `corpus-24`, `line-tracer`, `framework-baseline`,
> `overhead-samples`, `model-studies`, `model-verdict`, `model-verdict-studies`
> · **Paper:** §1, §2, §3, §5

## The question

The paper was read the way a programme committee would read it, and the review recommended
a weak reject. Its sharpest objection: *of course a callback can't see inside a function —
the result is true by construction.* This phase answers that objection, and the seven others
the review raised, with code and measurements rather than rewording.

## What the review said, in one picture

```
   the claim                 the objection                       the answer (this phase)
   ─────────                 ─────────────                       ───────────────────────
   callbacks can't see   →   "true by construction"          →   state it precisely
   the reason                "a line tracer can"                 (Proposition 1), then
                             "a real framework might"            try five observers
                             "an LLM could reconstruct it"       and measure each

   justifications        →   "prose can say anything;        →   every verdict now comes
   explain decisions          who checks it?"                    from a named rule and
                                                                 replays from the record

   two records,          →   "too small; not a real          →   24 real training runs;
   our own agents             framework; not LLM decisions"      LangGraph; a model-decided
                                                                 CAx

   "no resolvable        →   "not resolving ≠ no cost"       →   equivalence tests and
   overhead"                                                     paired intervals
```

## Decisions that replay

Before this phase a decision was a verdict and a sentence. Now it is also the **name of the
rule** that produced the verdict and **the values that rule read**:

```
   yp:verdict      "ESCALATE"
   yp:justification "Integrity check failed: the stored copy does not match …"
   yp:rule         "ca1.persist/v1"
   yp:rule_inputs  {"integrity_ok": false, "envelopes_missing": 0,
                    "final_envelope_seen": true, "persist_trigger": "quorum"}
```

A rule is a small pure function in the agent's code. `decide()` runs it on the inputs and
refuses to record a verdict the rule does not give. Afterwards, `eval.replay` runs every
rule again on its recorded inputs: all 24 reference decisions, the 9 fault scenarios, 288
corpus decisions and the 24 from the Docker run replay. Edit a verdict in a stored record
and it no longer does.

The rule inputs also gave the paper a better ruler. The old measure pulled "facts" out of
the justification text with a regular expression. The new one uses the values the code
itself declares it read — and asks whether the callback baseline ever saw them:

```
                          values the rules read     seen by the baseline
   reference run (24)            36                  2   both fields of the input record
   corpus (288)                 432                 24   all fields of the input record
```

## Five ways of trying to retrieve the reason

```
   observer                          what it got
   ────────                          ───────────
   our callback baseline             2 of 36 values; never the rule
   a line tracer (sys.settrace)      7 of 12 values, buried among 15–116 per step,
                                     at 2–13× the step's time; values computed inside an
                                     expression are never assigned, so never seen
   LangChain's handler on LangGraph  110 of 240 matched — but 106 match an unrelated
                                     run just as well (zeros, small counts, labels)
   gpt-4o reading the trace          the reason for 1 of 143 decisions; CA1's cause
                                     right 9 of 27 times: chance
   operators (two models)            53% right from the baseline's record,
                                     100% from ours
```

The "matched by chance" check was added in this phase: every value is also searched for in
a different run's observations, where it can only match by accident. A bigger haystack
matches more, and LangGraph shows its handler 45 times more text than our baseline sees.

## When the model decides

CAx can now hand its verdict to the model (`CAX_VERDICT=model`). gpt-4o reviews the summary
against the producer's record and answers in fixed JSON; a rule reads the answer; the code's
own figure check still runs and is recorded beside it. On summaries with planted
fabrications:

```
   planted                         model     code check
   ───────                         ─────     ──────────
   nothing (faithful)              ACCEPT    ACCEPT        50 of 50 agree
   "48,000 training samples"       REVISE    REVISE        24 of 24 agree
   "4 GPUs"                        REVISE    ACCEPT        the code can't see these:
   "trained on ImageNet"           REVISE    ACCEPT        48 disagreements, recorded
```

The surprise: for these decisions the callback baseline **can** see the reason, because the
model's answer is the output of a step and callbacks see outputs. gpt-4o rebuilt the reason
from the trace for all 72 REVISE verdicts, and operators were right 100% of the time from
either record. That is the proposition working as stated — a reason that crosses the step's
boundary is retrievable — and it sharpens the paper's claim: capture matters most for reasons
computed in code.

## What the costs really are

"Not resolvable at three times its spread" was replaced by tests that can say *equivalent*:

```
   building the record    equivalent to no provenance within 1 ms, for 5 of 6 agents
                          (48–81 µs; CA2's 95 ms chart render jitters more than 1 ms)
   writing the record     about 1 ms per record — under 0.1% of a model call
   energy, paired         building: +3% (−5 to +9%)   writing: +16% (+9 to +22%)
```

The energy line **corrects Phase 7**, which reported no resolvable difference. Pairing each
condition with "off" in the same round removes the drift a round shares, and the write's
cost appears.

## Found on the way

- **A real bug in CAx.** Over the corpus, CAx said REVISE on half the runs: its grounding
  check read the digits in a run name (`corpus_17_GR0_17`) as figures the summary claimed.
  Fixed; the reference run is unchanged.
- **A real finding about yProv4ML.** CA3 flagged every corpus run for a 1.0 s stall before
  the first epoch, 74–87% of each run: the producer's doing, not the training's.
- **Two traps for paid runs.** `run_local.py` reads `.env` itself, so unsetting the key does
  not stop model calls — set it to empty. And `SSLKEYLOGFILE` in the environment crashes
  Python's TLS on Windows.

## What it does not show

- The operator study uses models, not people. It shows the record holds what an operator
  needs, not how a person would use it.
- One producer (yProv4ML) and one machine: the scope of a demonstrator.
- The model-decided result is one agent, one prompt and one model family.

## Check it yourself

```bash
uv run python -m eval.replay --prov-dir experiments/decision-replay
uv run python -m eval.replay --prov-dir experiments/corpus-24
uv run python -m eval.equivalence --agent experiments/overhead-samples/agent.json \
    --energy experiments/energy-session/energy.json
# the model studies, with no key: extract the archived cache, then re-score offline
mkdir -p out/study && tar -xzf experiments/model-studies/cache.tar.gz -C out/study
uv run python -m eval.diagnose --faults experiments/model-studies/faults --offline
```
