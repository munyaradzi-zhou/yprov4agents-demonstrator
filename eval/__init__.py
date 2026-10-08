"""Evaluation harness: turn a demonstrator run into the paper's numbers.

Three tools, each answering a different question about a run:

``evaluate``
    What did this run produce? Renders the paper's result tables (E1–E5) from the emitted
    provenance, so every figure is reproducible from artifacts rather than transcribed.
``compare``
    Did a change alter the provenance, and only in the way it claimed? Compares two runs
    structurally, ignoring what legitimately differs between runs of identical code.
``archive``
    Can this run still be trusted as evidence? Freezes a run with a hash-verified
    manifest, its git commit, and the environment it came from.

This is a package rather than a namespace directory so that ``eval.compare`` has exactly
one module name — the tests import it, and two names for one file is an error a type
checker is right to refuse.
"""
