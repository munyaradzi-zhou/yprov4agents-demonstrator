# Phase 7 — Energy, measured where it can be

> **Date:** 10 October 2026 · **Archive:** [`experiments/energy-session`](../../experiments/energy-session)
> · **Code:** `eval/bench_energy.py`, `yprov4agenticai/energy.py` · **Paper:** §6.3, Table 4

## The question

The timing study (E6) asked: *does recording provenance make the agents slower?* This phase
asks the same question in **joules**: does it make them use more energy?

Phase 4 had already shown that energy can't be charged to individual steps — the meter
ticks once a second at best, and a step lasts a few milliseconds. So this study measures
where energy *can* be measured: over long windows of complete sessions.

## What was found before measuring anything

**The laptop's energy meter works.** Earlier phases concluded that on this Windows laptop
no hardware energy counter was readable, and that every energy figure was therefore a
model. That turned out to be wrong. The Windows Energy Meter Interface (EMI), which
codecarbon reads, follows the real load the way a meter should:

```
   what the processor was doing          what EMI reported
   ────────────────────────────          ─────────────────
   nothing (idle)                        ▏▏▏▏▏  ~5 W
   one core busy                         ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇  ~21 W
   all eight threads busy                ▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇▇  ~31 W
   nothing again                         ▏▏▏▏  ~4 W
```

A model would not do that; these are plausible readings for this laptop's 15 W-class
processor. The earlier "it reads zero" came from reading the meter **before its first
sample** — until it samples, its running total is zero.

**Memory is still a guess.** codecarbon has no meter for RAM; it assumes a flat 10 W. So
every energy figure on this machine is part measured (processor) and part modelled
(memory). Adding them together produces a number that is neither — so this study never
does, and the library now labels such a figure `partly-measured` instead of `measured`.

That label had its own bug, found because the meter turned out to work: it checked that
the *total* was above zero, and the RAM guess always makes the total above zero. It now
checks the processor's own reading.

## How the measurement works

```
  one SESSION  = all six agents processing one real yProv4ML record   (~105 ms)
  one WINDOW   = 97 sessions under one condition, energy read at both edges
  one ROUND    = a window for each of OFF · A · B-TRANS · B, plus an IDLE window

  warm-up round (recorded, not counted)
  round 1:   OFF   A   B-TRANS   B   IDLE
  round 2:   A   B-TRANS   B   IDLE   OFF        ← the starting condition rotates,
  round 3:   B-TRANS   B   IDLE   OFF   A           so none always follows the same one
   …
  round 12
```

- **OFF** — no provenance at all. **A** — the callback baseline. **B-TRANS** — the full
  record built but not saved. **B** — built and saved, as shipped.
- **IDLE** — the machine doing nothing for 20 s, to know what it draws anyway.
- A difference counts as a result only if it is **3× bigger than the round-to-round
  spread** — the same rule the timing study uses.

## What it found

> **Corrected in Phase 8.** The table below judges each condition by comparing medians
> against their spread, and nothing resolves. Pairing each condition with OFF *in the same
> round* removes the drift a round shares, and on the same archived data it resolves the
> write: B costs **+16%** per session (95% interval +9 to +22%), while building alone is
> +3% (−5 to +9%). See [`PHASE_8.md`](PHASE_8.md) and `eval/equivalence.py`.

| Processor energy per session | OFF | A | B-TRANS | B |
|---|--:|--:|--:|--:|
| joules | 1.43 | 1.47 | 1.50 | 1.72 |
| vs OFF | — | +2.7% | +5.3% | +20.3% |
| …is that a result? (needs 3×) | | no (0.5×) | no (0.9×) | no (2.3×) |
| time per session | 104 ms | 105 ms | 105 ms | 114 ms |

**No difference is big enough to call a result.** Recording provenance does not change
a session's energy by an amount this machine can separate from its own variation.

**But the medians lean the same way as the timing study.** Building the record barely
moves anything (+5%); building *and saving* it moves more (+20%), and takes 9% longer.
That is the timing study's finding again: the cost is the write to disk, not the record.

```
   building the record    ▏ +5%    in the noise
   saving it as well      ▇▇▇▇ +20%  closer, still in the noise (2.3× of the 3× needed)
```

## Two runs, one kept

The first run (the **pilot**) had three flaws, found by reading its numbers:

1. **No warm-up.** Its first window read 12 W idle — three times any later idle window.
   The main run's warm-up round read 12.8 W too, which confirms it: the first window
   after the meter starts is not representative, so it is now recorded and excluded.
2. **Badly sized windows.** They were sized from three still-cold sessions, so they ran
   12 s instead of 20 s.
3. **Only one comparison.** Subtracting the idle power adds the idle power's own wobble
   (2–6 W from background activity), so it is now judged both with and without that
   subtraction.

The pilot is kept in the archive under `pilot/`, labelled, and not cited — it was a
different procedure. Its answer was the same: nothing resolves.

## What it does not show

- **The processor meter covers the whole chip**, not just this program. Anything else
  running on the laptop lands in the same reading. Interleaving shares that out fairly,
  and the idle windows subtract it, but it is still there — a quiet machine would give
  tighter numbers.
- **One laptop.** A server with a Linux RAPL counter would be the natural second
  occasion.
- **Calibration still over-estimated session time** (207 ms against a true 104 ms), so
  windows lasted about 10 s rather than 20 s — still ten meter samples each.

## Check it yourself

```bash
uv run --extra energy --extra eval --extra dev \
  python -m eval.bench_energy --rounds 12 --window-s 20 --country ITA --out-dir out/bench-energy
uv run python -m eval.archive verify --name energy-session
```
