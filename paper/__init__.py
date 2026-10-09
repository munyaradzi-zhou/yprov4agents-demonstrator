"""Paper artifacts: the outline, and the figures rendered from the frozen archives.

A package so ``python -m paper.figures`` resolves the same way the evaluation harness does,
and so the figure code can import from ``eval`` — every figure reads its numbers out of
``experiments/`` rather than carrying them inline.
"""
