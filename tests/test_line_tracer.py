"""The in-step observer: what it records, what it misses, and that it leaves no trace behind."""

from __future__ import annotations

import sys

import pytest

from eval import line_tracer
from eval.line_tracer import LineTrace, holds, tracing


def _decide_like(size: int) -> str:
    ok = size > 10
    gap = size / 4
    label = "big" if ok else "small"
    return f"{label}:{len([gap] * 3)}"  # len(...) is computed inline, never assigned


def test_the_trace_records_assigned_scalars_and_only_changes(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(line_tracer, "TRACED_PATHS", (__file__,))
    trace = LineTrace()
    with tracing(trace):
        _decide_like(20)
    values = trace.values()
    assert holds(values, True) and holds(values, 5.0) and holds(values, "big")
    # An inline expression is never a local, so the tracer never sees its value.
    assert not holds(values, 3)
    assigned = [name for _, _, changed in trace.events for name in changed]
    assert len(assigned) == len(set(assigned)), "a local was recorded again unchanged"


def test_values_are_matched_as_the_type_they_were_recorded_as() -> None:
    assert holds([1.0], 1.0)
    assert not holds([1.0], 1)
    assert not holds([True], 1)


def test_frames_outside_the_traced_files_are_not_followed() -> None:
    trace = LineTrace()
    with tracing(trace):
        _decide_like(20)
    assert trace.lines == 0


def test_tracing_restores_whatever_tracer_was_installed() -> None:
    before = sys.gettrace()
    with tracing(LineTrace()):
        pass
    assert sys.gettrace() is before
