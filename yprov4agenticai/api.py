"""The developer-facing capture API: what a step touched, and why it concluded.

:class:`~yprov4agenticai.tracker.ProvTracker` is the buffer. This module is the surface a
developer actually writes against — a decorator, a context manager, and one explicit call:

    @tracker.step("PlanningActivity")
    def plan(task: str) -> str: ...

    with tracker.activity("ToolCallActivity") as prov:
        prov.consumes(task)
        result = tool.run(payload)
        prov.produces("ToolResultEntity", result, derived_from=task)

    tracker.decide(
        verdict="REVISE",
        justification="Self-consistency 0.41 < 0.70 threshold; the drafted efficiency "
                      "claim contradicts the recorded execution times. Regenerating.",
        confidence=0.41,
    )

    doc = tracker.emit()

Three properties of this layer are deliberate, and each one is a claim the demonstrator has
to be able to defend.

*The wrappers record no relation nobody declared.* Timing, typing, agent attribution and
ordering are observed, so they are automatic. Lineage is not observable from the outside: a
wrapper sees that a function ran, never which of the values in scope it treated as its
input. An earlier version of the tracker inferred lineage from the order calls were made
in, which produced a large number of confident, wrong edges. :class:`ActivityScope` is
therefore where lineage is *stated*, and the API offers no way to guess it.

*A step that raises is still recorded.* The context manager writes the activity on the way
out of the block whether the body returned or raised, stamping ``yp:outcome`` and the
exception type before re-raising. A failed step that leaves no trace is the worst case for
an audit, because the record then shows a gap where it should show a failure.

*A decision without a justification is a programming error.* :meth:`ProvTracker.decide`
raises on blank justification text. Every other field on an activity can be filled in by
machinery; this one cannot, so it is the one field the API refuses to let a caller skip.
"""

from __future__ import annotations

import datetime
import time
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:  # import cycle: tracker imports this module at runtime
    from .tracker import ProvTracker


class Span:
    """An interval that knows *when* it started from one clock and *how long* it ran from
    another.

    PROV records an activity as two instants, which invites reading both off the wall
    clock. That loses short steps: on a Windows host the system clock advances in steps of
    roughly 15.6 ms, and two ``datetime.now()`` calls either side of a sub-millisecond
    operation return the *same* value — so the record says the step took no time at all,
    and a reader cannot tell a fast step from an unmeasured one.

    So the start instant comes from the wall clock, where it belongs (it has to be
    comparable with timestamps from other processes), and the duration comes from
    :func:`time.perf_counter`, which is monotonic and has nanosecond resolution. The end
    instant is the start plus that duration. Both are then anchored in real time and the
    interval between them is accurate to well under a millisecond.

    This is also why the end instant can differ slightly from a wall-clock reading taken
    at the same moment: the two clocks drift, and over a span this short the monotonic one
    is the trustworthy half.
    """

    __slots__ = ("_counter", "started")

    def __init__(self) -> None:
        self.started = datetime.datetime.now(datetime.UTC)
        self._counter = time.perf_counter()

    @property
    def start_iso(self) -> str:
        return self.started.isoformat()

    def elapsed_s(self) -> float:
        return time.perf_counter() - self._counter

    def end_iso(self) -> str:
        """The instant this span ends, measured monotonically from where it began."""
        return (self.started + datetime.timedelta(seconds=self.elapsed_s())).isoformat()


#: Parameter name a decorated function declares to receive its :class:`ActivityScope`.
#: A function that does not declare it is wrapped unchanged, so adding provenance to an
#: existing function costs nothing until that function needs to state its lineage.
SCOPE_PARAMETER = "prov"

#: ``yp:outcome`` values. Recorded only on a step whose body raised — a successful
#: activity carries no outcome attribute, so the default document is unchanged by the
#: existence of this feature.
OUTCOME_ERROR = "error"


class ActivityScope:
    """The handle a running step uses to declare what it touched.

    The scope is not itself a context manager: :meth:`ProvTracker.activity` owns entering
    and exiting, so there is exactly one place that decides when a record is written and
    no way to open a scope that never closes.

    Yielded by :meth:`ProvTracker.activity` and injected into a function decorated with
    :meth:`ProvTracker.step` that declares a ``prov`` parameter. Nothing is written to the
    tracker until the scope closes, so a body that raises still produces exactly one
    activity record rather than a partial one.

    The methods return the entity id they act on, which lets a declaration sit inline in
    the expression that uses the value::

        payload = store.resolve(prov.consumes(pointer_entity))
    """

    __slots__ = (
        "_tracker",
        "attributes",
        "confidence",
        "consumed",
        "energy",
        "generated",
        "justification",
        "kind",
        "label",
        "memory",
        "verdict",
    )

    def __init__(self, tracker: ProvTracker, kind: str, label: str) -> None:
        self._tracker = tracker
        self.kind = kind
        self.label = label
        self.consumed: str | None = None
        self.generated: str | None = None
        self.justification: str | None = None
        self.verdict: str | None = None
        self.confidence: float | None = None
        self.attributes: dict[str, Any] = {}
        self.energy: dict[str, Any] = {}
        self.memory: str | None = None

    # ── declarations ──────────────────────────────────────────────────────────────
    def consumes(self, entity_id: str) -> str:
        """Declare the entity this step read. Emits ``prov:used``."""
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
        """Register the entity this step wrote and declare it generated here.

        Emits ``prov:wasGeneratedBy``, and ``prov:wasDerivedFrom`` when ``derived_from``
        names a source. The derivation is a separate argument on purpose: a step that
        generates an entity has not necessarily derived it from what it read, and the two
        claims are not interchangeable.

        Heavy payloads belong in the content-addressed store, with ``pid`` and
        ``content_hash`` carrying the pointer — see :mod:`yprov4agenticai.store`.
        """
        entity_id = self._tracker.register_entity(
            kind,
            value,
            suffix=suffix,
            pid=pid,
            content_hash=content_hash,
            derived_from=derived_from,
        )
        self.generated = entity_id
        return entity_id

    def generates(self, entity_id: str) -> str:
        """Declare an already-registered entity as this step's output."""
        self.generated = entity_id
        return entity_id

    def reads_memory(self, entity_id: str) -> str:
        """Declare the memory state this step reasoned over. Emits an extra ``prov:used``."""
        self.memory = entity_id
        return entity_id

    def note(self, **attributes: Any) -> None:
        """Stamp additional attributes onto the activity.

        Keys are written as given, so they carry their own prefix: ``yp:``-prefixed for
        this vocabulary's terms. Evidence a reader may want to check belongs here;
        assertions about what happened belong in the justification.
        """
        self.attributes.update(attributes)

    def measured(self, energy_attrs: dict[str, Any]) -> None:
        """Attach an energy delta measured across this step."""
        self.energy.update(energy_attrs)

    def concludes(
        self, verdict: str, justification: str, *, confidence: float | None = None
    ) -> None:
        """Record this step's verdict and the reasoning behind it.

        For a step that both does work and decides about it. A decision that stands alone
        reads better as :meth:`ProvTracker.decide`.
        """
        if not justification.strip():
            raise ValueError(
                f"verdict {verdict!r} was given no justification. The rationale for a "
                f"decision is a local variable at the moment of decision: if it is not "
                f"captured here it cannot be recovered later."
            )
        self.verdict = verdict
        self.justification = justification
        self.confidence = confidence

    # ── writing ───────────────────────────────────────────────────────────────────
    def record(self, started_at: str, ended_at: str, error: BaseException | None) -> None:
        """Write this scope to the tracker as a single activity record."""
        attributes = dict(self.attributes)
        if self.verdict is not None:
            attributes["yp:verdict"] = self.verdict
        if error is not None:
            attributes["yp:outcome"] = OUTCOME_ERROR
            attributes["yp:error_type"] = type(error).__name__
        self._tracker.record_activity(
            self.kind,
            self.label,
            started_at,
            ended_at,
            in_eid=self.consumed,
            out_eid=self.generated,
            justification=self.justification,
            energy_attrs=self.energy or None,
            confidence_score=self.confidence,
            extra_attrs=attributes or None,
            used_mem=self.memory,
            verbose=self._tracker.verbose,
        )
