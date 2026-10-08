# Phase 4 — The model, the prompt, and the modes

> Follow-along notes. Plain language, with diagrams. The paper material is in
> [`../../paper/OUTLINE.md`](../../paper/OUTLINE.md) E5b and E5c.
>
> Previous: [`PHASE_3.md`](PHASE_3.md) · Plan: [`../../PLAN.md`](../../PLAN.md) Phase 4

---

## Four things were broken, and they were all the same kind of broken

Each one was a feature that existed on paper and not in the running system.

```
   F1  the record said nothing about the model        ← the substantive gap
   F2  prompts.py — 238 lines — imported by nothing
   F3  energy.py — 311 lines — called by nothing
   F4  METADATA never executed; TRANSIENT did nothing
```

The review called this out as the easiest thing for a reviewer to attack: roughly 550 of the
library's ~960 lines were dead to the demonstrator, **including both of its largest
modules**, while the README advertised what they did.

---

## F1 + F2: an LLM step you can actually repeat

### The problem

The pipeline has one non-deterministic step — the model call — and it was the one step the
record could not account for.

```
   BEFORE                                AFTER
   ──────                                ─────
   ToolCallActivity                      ToolCallActivity
     "CAx: write NL summary"               "CAx: write NL summary"
     yp:summary_mode  llm                  yp:summary_mode       llm
                                           yp:model              gpt-4o
   ...that's it.                           yp:temperature        0.2
                                           yp:max_tokens         400
   Which model? Which prompt?              yp:prompt_id          act.system
   At what temperature? How many           yp:prompt_version     2.0
   tokens? How long did it take?           yp:prompt_sha         3f831826f610
   Did it finish, or run out of            yp:prompt_tokens      226
   budget mid-sentence?                    yp:completion_tokens  107
                                           yp:llm_latency_s      5.043
   Unanswerable from the record.           yp:finish_reason      stop
                                           yp:response_sha       eaae9be93d4d
```

For a paper about **agentic AI** provenance, that gap was the most substantive one in the
artifact. A record that documents everything except the part that varies is not an auditable
record of an agent.

### The fix: a completion is not a string

`runtime/llm.py` makes the model call return something accountable:

```python
completion = self.llm.complete(ACT_SYSTEM, facts)     # a registered Prompt, not an f-string
...
prov.note(**completion.provenance())                  # everything above, in one line
```

Both LLM-bearing agents (CAx and CA3) go through the same client, so their two records are
comparable rather than each agent's own idea of what matters.

### Why the prompt is cited by *hash* and not just by name

A prompt is part of the experimental apparatus. Change its wording and the output
distribution changes — so two runs that differ need to be able to show *why*.

```
   act.system  v2.0  sha 3f831826f610     ← the record cites all three
                 │
                 ├── version: what the author claims changed
                 └── sha:     what actually changed

   Edit the text and forget to bump the version?
   The version matches, the hash does not. Caught.
```

`prompts.py` already did all of this — versions, changelogs, content hashes. It had simply
never been imported. The fix is not new infrastructure; it is making the call site go
through the registry. `complete()` takes a `Prompt`, not a string, so **a prompt cannot
reach a model without its identity reaching the record.**

### An absence is recorded as an absence

The pipeline still runs with no API key, on a deterministic fallback. That path stamps **no**
model attributes — not zeros, not `"none"`. A zero token count is a measurement, and on that
path no measurement was made. The step says `yp:summary_mode: computed` and stops there.

And when a model was configured but the call failed, the mode says so:
`computed-after-TimeoutError`. The record distinguishes *didn't try* from *tried and
failed*, which matters when someone asks why a run looks different from the last one.

### One new verdict condition fell out of this

Having `finish_reason` made a second failure mode visible. A summary cut off on its token
budget is a different artifact from one that invented a figure:

- **ungrounded** — it asserted something the graph does not support → a faithfulness problem
- **truncated** — it stopped mid-sentence because it ran out of budget → an incompleteness problem

Both now produce REVISE, with text that names which. Before, truncation was invisible: a
cut-off summary that happened to be faithful was ACCEPTed.

---

## F4: the modes, now selectable and actually exercised

`METADATA` had never been run by an agent. `TRANSIENT` was worse — it was *stored in the
record*, reported in the record's own self-description, and did nothing at all.

> A configuration flag that does not configure anything is worse than a missing feature,
> because the record then asserts something untrue about itself.

### TRANSIENT, precisely

```
   PERSISTENT                           TRANSIENT
   ──────────                           ─────────
   build the document      ✓            build the document      ✓
   return it to the agent  ✓            return it to the agent  ✓
   publish it on the bus   ✓            publish it on the bus   ✓
   write it to disk        ✓            write it to disk        ✗
```

Narrow on purpose: it suppresses *handing the record out*, not building it. That is the one
layer `docs/E6_OVERHEAD.md` needs isolated to separate serialisation cost from construction
cost, and it is the only thing the flag ever claimed.

### One construction site

Both modes are environment variables now (`PROV_GRANULARITY`, `PROV_PERSISTENCE`), read in
one place and applied by one factory that all six agents build their tracker through:

```
   PROV_GRANULARITY=METADATA
   PROV_PERSISTENCE=TRANSIENT
            │
            ▼
      prov_mode()  ── validates, fails at start-up on a typo
            │
            ▼
     agent_tracker()  ── the single place a ProvTracker is constructed
            │
      ┌─────┴──┬─────┬─────┬─────┬─────┐
     CA1     CAx   CA2   CA3   MC1   MC3     ← none of them know the flag exists
```

Before, each agent constructed its own tracker with the same four arguments spelled out.
That is exactly how a mode flag ends up honoured by five agents and forgotten by the sixth.

**METADATA keeps every justification.** The point of the lean mode is to buy less *data*,
never less reasoning — and if it shed justifications, E6c's mode comparison would stop
being a comparison. A test pins that.

---

## F3: energy — wired in, and a negative result

`energy.py` was 311 lines nothing called. The irony the review pointed out: CA3 reports
*"no energy or emission streams"* as a completeness gap in the graphs it analyses, and it
was right about the agents that emitted it.

So it is wired in — not into one chosen step, but into the capture API, so every step closed
through `activity()` carries the energy attributable to it. Off by default (a sampling
backend has a cost of its own, and no run that is measuring *time* should be paying it).

And then the measurement said something I did not expect.

### A sampling backend cannot attribute energy to a millisecond

```
   codecarbon samples every 15 SECONDS:
   ├────────────────15s────────────────┼────────────────15s────────────────┤
                                       ▲
                                    a tick

   the agent's steps last MILLISECONDS:
   │▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│▌│

   So a delta lands on whichever step happened to read AFTER a tick.
```

Measured, first attempt:

| step | energy charged | step duration |
|---|--:|--:|
| choose what to plot | **46.9 µWh** | 0.000 s |
| render chart (matplotlib) | **4.6 µWh** | 1.328 s |

The step that did nothing was charged **ten times** what the step that drew the chart was
charged. And a second artifact was worse: the *first* step of every record was charged
**28.3 seconds** of inter-message idle time, because the energy tracker outlives any one
record and its first delta covers the gap since the last message.

### Both are fixed, and the fix is the contribution

**The idle-time charge is a bug, so it is fixed.** The baseline resets when a record begins;
no step is charged for the gap before it.

**The coarse attribution is not a bug — it is a limit** — so the record discloses it rather
than hiding it:

```
   yp:energy_kwh                     0.000001667
   yp:energy_sampling_period_s              15.0
   yp:energy_attributable                  False   ← the record says so itself
```

The number is real: that energy was consumed, by this process, in that window. What is not
real is the *attribution*. Publishing the figure with its own disclaimer attached is better
than either dropping it or presenting it as a per-step measurement.

**Energy belongs at session granularity.** Any per-step energy claim in this literature
needs a backend sampling faster than the steps, and should say what its period was. That
goes in the paper (E5c) as a negative result, which is worth more than a flattering number
we could not defend.

### One more thing the wiring found

The CodeCarbon backend resolved the local grid's carbon intensity **over the network** at
start-up. Two problems, one of which was fatal:

- *Reproducibility.* Grid intensity is an **input** to the emissions figure. A run that
  discovers it rather than stating it cannot be repeated — same workload, same machine,
  different number in a different place.
- *It crashed.* On this Windows host the lookup aborted the whole process with
  `OPENSSL_Uplink … no OPENSSL_Applink` before any agent ran. An energy backend is not
  allowed to take the pipeline down with it.

Now it uses codecarbon's offline tracker with a declared country (`PROV_ENERGY_COUNTRY`),
so there is no network call and the grid intensity is a stated parameter. Run with
`PROV_ENERGY=codecarbon PROV_ENERGY_COUNTRY=ITA` and it works.

---

## A test-infrastructure lesson

Three test files had each hand-built an agent with `__new__`, assembling its attributes one
by one. The moment `StreamingAgent` gained an energy tracker, **ten tests failed** — none of
them for a reason related to what they were testing.

The fix is one shared fixture that constructs agents through their real `__init__`. Worth
recording because the failure mode is subtle: a fixture that bypasses a constructor is a
second, undocumented definition of what an object is, and it drifts silently.

---

## What changed, file by file

| File | What happened |
|---|---|
| `runtime/llm.py` | **new** — `Completion` (a model response that accounts for itself) and `LLMClient`; the vendor call is injected, so tests exercise the provenance without a network |
| `yprov4agenticai/prompts.py` | CA3's narration prompt registered (it was an inline f-string); the docstring no longer claims a wiring that did not exist |
| `yprov4agenticai/tracker.py` | `persistence` is behavioural and validated; energy read at every step boundary; the baseline resets per record |
| `yprov4agenticai/energy.py` | offline-capable backend with a declared grid intensity; deltas disclose their sampling period and whether they are attributable |
| `runtime/util.py` | `prov_mode()`, `energy_tracker()`, and `agent_tracker()` — one construction site; the LLM helpers moved out |
| `runtime/base_agent.py` | one energy tracker per agent, not per message |
| `runtime/agents/*.py` | all six use the factory; CAx and CA3 record model provenance |
| `tests/conftest.py` | one `headless_agent` fixture, replacing three hand-built ones |
| `tests/test_llm_provenance.py` · `tests/test_modes.py` | **new** — 24 tests between them |
| `pyproject.toml` | an `energy` extra, declared and kept out of CI's install |
| `docs/E6_OVERHEAD.md` · `paper/OUTLINE.md` | §4.2 and §11 updated; E5b and E5c added |

---

## How to check any of this yourself

```bash
# the deterministic path: no model, so no model attributes — which is the point
uv run python run_local.py --out-dir out
uv run python -m eval.compare --before experiments/phase4-reference --after out --strict

# the modes, through a real agent
PROV_PERSISTENCE=TRANSIENT uv run python run_local.py --out-dir out.t   # writes nothing
PROV_GRANULARITY=METADATA  uv run python run_local.py --out-dir out.m   # no entity section

# energy, with a stated grid intensity and no network call
uv sync --extra energy
PROV_ENERGY=codecarbon PROV_ENERGY_COUNTRY=ITA uv run python run_local.py --agents ca2

# the model path is archived rather than re-run, because it costs money
cat experiments/phase4-llm/example_0__cax_prov.json | grep yp:prompt_sha
```

---

## Where this leaves the plan

Phase 4 was the last phase before measurement. **Phase 5 (E6) is unblocked**, and better set
up than `docs/E6_OVERHEAD.md` anticipated:

- `TRANSIENT` exists, so E6c's third row can be filled rather than left blank.
- `METADATA` is selectable by environment variable, so the mode sweep is a loop over an env
  var instead of six code edits.
- CAx's and CA3's records now state model latency separately from everything else, so the
  parts of a wall-clock figure that are *the model* can be separated from the parts that are
  *the instrumentation* — which §11 lists as a threat to validity.
- Energy is off by default, so it cannot contaminate a timing run.

`PLAN.md` wants Phase 5 started by **25 October**. It is the 8th.

Still open from the review: **A4** (nothing consumes `governance.*` — a claim-scope decision
for the author), **B1** (justification coverage is tautological as a headline), **E** (the
`yp:` vocabulary artifact), and two library features still unexercised by the demonstrator —
`register_memory` and `actedOnBehalfOf` delegation. Phase 6 decides each: demonstrate, or
label as experimental and say so.
