# Pilot run — excluded from the result, kept for the record

Run at commit `685e5e4`, eight rounds, before three design defects were fixed:

1. No warm-up round. The first window after the meter started read 12 W idle, against
   2–6 W for every later window.
2. Windows were sized from three cold sessions (170 ms) against a warm ~105 ms, so work
   windows lasted 12 s against a 20 s target.
3. Only the above-idle comparison was judged; subtracting a wandering idle power roughly
   doubled the spread.

Its conclusion agrees with the main run: no condition resolves against OFF (B: +22% above
idle at 0.7×). It is not cited as a second occasion, because it is not the same
procedure.
