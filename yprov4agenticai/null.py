"""The off switch: a tracker that honours the whole interface and records nothing.

Two audiences, and they want the same object.

*A user who wants no provenance.* Instrumented code should not have to be edited, or
wrapped in conditionals, to be run without instrumentation. :class:`NullTracker` accepts
every call :class:`~yprov4agenticai.tracker.ProvTracker` accepts, returns the same kinds of
value, and builds no graph — so ``PROV_INSTRUMENTATION=off`` is a deployment decision rather
than a code change.

*The overhead study.* It is also condition OFF of ``docs/E6_OVERHEAD.md`` §4.1 — the
denominator every overhead figure is divided by. That is why it lives here rather than in
the benchmark harness: the agents have to be measured *unmodified*, and a shim that exists
only inside a benchmark measures the shim.

The three conditions then differ only in what is installed, which is what makes them
comparable:

===========  ====================================  ==========================
condition    tracker                               observer
===========  ====================================  ==========================
OFF          NullTracker                           none
A            NullTracker                           CallbackLogger
B            ProvTracker                           none
===========  ====================================  ==========================

Condition A falls out for free, and correctly. A framework callback handler still sees node
boundaries under A — the framework is the thing calling the nodes — so a null tracker still
*notifies*, while building nothing. What A lacks is the explicit layer, not the
observation layer, and modelling it any other way would make the baseline weaker than a
real handler.

The one thing a null tracker must hold is the payloads a step declared, because an observer
is handed values and there would otherwise be nothing to hand it. Two dictionary operations
per step, which is a fair account of what a callback-level integration costs in bookkeeping.
"""

from __future__ import annotations

from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

from .observer import StepEvent, get_default_observer
from .rules import Rule, Scalar

_R = TypeVar("_R")

#: What ``to_prov_json`` returns: an empty document, not a malformed one. A consumer that
#: reads the output of a disabled tracker should find nothing, not fail.
EMPTY_DOCUMENT: dict[str, Any] = {}


class NullScope:
    """The handle :meth:`NullTracker.activity` yields. Accepts declarations, keeps values.

    Mirrors :class:`~yprov4agenticai.api.ActivityScope`'s surface. Values declared through
    :meth:`produces` are retained only so that an installed observer can be handed them;
    nothing else is kept.
    """

    __slots__ = ("_tracker", "consumed", "generated", "kind", "label")

    def __init__(self, tracker: NullTracker, kind: str, label: str) -> None:
        self._tracker = tracker
        self.kind = kind
        self.label = label
        self.consumed: str | None = None
        self.generated: str | None = None

    def consumes(self, entity_id: str) -> str:
        self.consumed = entity_id
        return entity_id

    def produces(
        self,
        kind: str,
        value: str,
        *,
        suffix: str = "",
        pid: str | None = None,
        content_hash: str | None = None,
        derived_from: str | None = None,
    ) -> str:
        entity_id = self._tracker.register_entity(kind, value, suffix=suffix)
        self.generated = entity_id
        return entity_id

    def generates(self, entity_id: str) -> str:
        self.generated = entity_id
        return entity_id

    def reads_memory(self, entity_id: str) -> str:
        return entity_id

    def note(self, **attributes: Any) -> None:
        return None

    def measured(self, energy_attrs: dict[str, Any]) -> None:
        return None

    def concludes(
        self, verdict: str, justification: str, *, confidence: float | None = None
    ) -> None:
        # The justification is not stored, but it is still refused when blank: a caller
        # should not discover at deployment time that the check it relied on was only
        # running while provenance happened to be enabled.
        if not justification.strip():
            raise ValueError(f"verdict {verdict!r} was given no justification.")

    def record(self, started_at: str, ended_at: str, error: BaseException | None) -> None:
        """Close this step: notify an installed observer, record nothing.

        The same signature as :meth:`~yprov4agenticai.api.ActivityScope.record`, and for
        the same reason the class exists at all — a scope that is missing part of the real
        scope's surface is not the same code path with recording removed. The benchmark's
        own test suite compares the two surfaces member by member.

        This call is the entire cost of condition A.
        """
        observer = self._tracker.observer or get_default_observer()
        if observer is None:
            return
        values = self._tracker.values
        consumed = values.get(self.consumed or "")
        generated = values.get(self.generated or "")
        observer(
            StepEvent(
                session=self._tracker.sid,
                agent=self._tracker.agent_label,
                name=self.label,
                started_at=started_at,
                ended_at=ended_at,
                inputs=() if consumed is None else (consumed,),
                outputs=() if generated is None else (generated,),
                error=None if error is None else type(error).__name__,
            )
        )


class NullTracker:
    """Accepts everything :class:`ProvTracker` accepts; records nothing.

    The constructor signature is deliberately permissive — it takes whatever the real
    tracker takes and ignores what it cannot honour — so swapping the two is a one-line
    change at the construction site and nowhere else.
    """

    def __init__(
        self,
        sid: str,
        granularity: str = "DATA",
        persistence: str = "PERSISTENT",
        agent_label: str = "NL_Summarizer",
        principal: dict | None = None,
        sink: Callable[[dict], Any] | None = None,
        verbose: bool = False,
        observer: Any | None = None,
        energy: Any | None = None,
    ) -> None:
        self.sid = sid
        self.granularity = granularity
        self.persistence = persistence
        self.agent_label = agent_label
        self.principal = principal
        self.sink = sink
        self.verbose = verbose
        self.observer = observer
        self.energy = energy
        #: Synthetic entity id -> the value a step declared, held for the observer only.
        #: Public because the scope reads it when it closes; a callback handler is handed
        #: payloads, so there has to be somewhere for the payloads to be.
        self.values: dict[str, str] = {}
        self._steps = 0

    # ── the primitives ────────────────────────────────────────────────────────────
    def register_entity(
        self,
        kind: str,
        value: str,
        suffix: str = "",
        pid: str | None = None,
        content_hash: str | None = None,
        derived_from: str | Sequence[str] | None = None,
    ) -> str:
        entity_id = f"yp:{kind}_{self.sid}{suffix}"
        self.values[entity_id] = value
        return entity_id

    def register_memory(
        self,
        value: str,
        turn: int | None = None,
        pid: str | None = None,
        content_hash: str | None = None,
    ) -> str:
        return f"yp:MemoryStateEntity_{self.sid}_m{turn or 0}"

    def record_activity(self, kind: str, label: str, t0: str, t1: str, **kwargs: Any) -> None:
        self._steps += 1

    # ── the capture API ───────────────────────────────────────────────────────────
    def step(
        self, kind: str, label: str | None = None, *, produces: str | None = None
    ) -> Callable[[Callable[..., _R]], Callable[..., _R]]:
        def decorate(function: Callable[..., _R]) -> Callable[..., _R]:
            return function

        return decorate

    @contextmanager
    def activity(self, kind: str, label: str | None = None) -> Iterator[NullScope]:
        scope = NullScope(self, kind, label or kind)
        started = _now()
        error: BaseException | None = None
        try:
            yield scope
        except BaseException as raised:
            error = raised
            raise
        finally:
            self._steps += 1
            scope.record(started, _now(), error)

    def decide(
        self,
        verdict: str,
        justification: str,
        *,
        confidence: float | None = None,
        label: str | None = None,
        kind: str = "DecisionActivity",
        consumed: str | None = None,
        generated: str | None = None,
        attributes: dict[str, Any] | None = None,
        started_at: str | None = None,
        rule: Rule | None = None,
        evidence: Mapping[str, Scalar] | None = None,
    ) -> None:
        if not justification.strip():
            raise ValueError(f"verdict {verdict!r} was given no justification.")
        self._steps += 1
        scope = NullScope(self, kind, label or f"decide: {verdict}")
        scope.consumed = consumed
        scope.generated = generated
        scope.record(started_at or _now(), _now(), None)

    def emit(self, sink: Callable[[dict], Any] | None = None) -> dict:
        return EMPTY_DOCUMENT

    def to_prov_json(self) -> dict:
        return EMPTY_DOCUMENT

    # ── state ─────────────────────────────────────────────────────────────────────
    def to_state(self) -> dict:
        return {"disabled": True, "sid": self.sid, "steps": self._steps}

    @classmethod
    def from_state(cls, sid: str, d: dict) -> NullTracker:
        # The state of a tracker that records nothing is nothing, so there is nothing to
        # restore from `d`. Accepted anyway, because a caller swapping the two classes
        # should not have to special-case the call.
        del d
        return cls(sid)


def _now() -> str:
    """The clock a callback handler would read. Imported lazily to keep the off path thin."""
    import datetime

    return datetime.datetime.now(datetime.UTC).isoformat()
