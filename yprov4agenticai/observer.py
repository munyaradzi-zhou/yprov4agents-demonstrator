"""The Layer-1 observation hook: what an outside observer of a step can see.

``docs/INSTRUMENTATION.md`` argues that behavioural provenance is two layers — *what/when*,
which an external observer can collect automatically, and *why*, which it cannot. This
module is the seam between them. A :class:`StepEvent` carries exactly what a framework
callback handler is handed when a node finishes, and nothing else.

That restriction is enforced by the type, not by a convention. There is no field here for a
verdict, a justification, an activity type or an entity identifier, so a consumer of this
stream cannot accidentally be given information that a real callback handler would never
have had. It is what makes the Strategy A baseline in :mod:`eval.baseline` a measurement
rather than a stipulation: the comparison is only worth something if the competing
condition is genuinely restricted to the competing condition's inputs.

Two consumers are intended:

*A Strategy A baseline* (built: :mod:`eval.baseline`), which reconstructs as much W3C PROV
as a callback handler can and thereby measures what explicit instrumentation adds.

*An OpenTelemetry bridge* (not built; the scouting target), which would map the same events
onto GenAI spans. Nothing in the architecture has to change to accept it.

The default observer is process-wide, deliberately. That is the model the observability
ecosystem already uses — a global tracer provider installed once at start-up, with
instrumented code unaware of it — and it is what lets the baseline attach to six agents
that construct their own trackers without any of them being modified for the experiment.

    with observe(logger.record):          # installs; restores the previous one on exit
        run_the_pipeline()
"""

from __future__ import annotations

import contextlib
from collections.abc import Callable, Iterator
from dataclasses import dataclass, field

#: A function that receives finished steps. Returning anything is pointless and ignored;
#: raising from one would break the instrumented agent, so implementations must not.
Observer = Callable[["StepEvent"], None]

_default_observer: Observer | None = None


@dataclass(frozen=True, slots=True)
class StepEvent:
    """One finished step, described only in terms an outside observer could have.

    The correspondence with LangChain's ``BaseCallbackHandler`` is deliberate and close:
    ``on_chain_start(serialized, inputs, run_id, parent_run_id)`` supplies :attr:`agent`,
    :attr:`name`, :attr:`inputs` and :attr:`session`; ``on_chain_end(outputs, run_id)``
    supplies :attr:`outputs`; ``on_chain_error(error, run_id)`` supplies :attr:`error`.
    Timings a handler takes itself, by reading the clock on either side.

    Attributes
    ----------
    session
        The run this step belongs to — a framework's ``run_id``.
    agent
        What the framework knows the running chain or graph as.
    name
        What the framework knows this node as. In a graph this is the node's name; here it
        is the step's ``prov:label``. Either way it is a developer-chosen string, which is
        the point: it carries whatever meaning the developer happened to put in it, and an
        observer can only pattern-match against that.
    inputs, outputs
        The *values* the step read and wrote. Values, not identifiers: a callback handler
        is handed payloads and has no stable identity for them, so a consumer that needs
        entity identity has to mint it from the content. That limitation is real and is
        one of the things the baseline measures.
    error
        The exception type, when the step raised. A handler does see failures.
    """

    session: str
    agent: str
    name: str
    started_at: str
    ended_at: str
    inputs: tuple[str, ...] = ()
    outputs: tuple[str, ...] = ()
    error: str | None = None
    #: Framework-level metadata a handler would also receive (model name, token counts).
    #: Empty in this demonstrator; present so the shape is not a lie about what Layer 1
    #: can carry.
    metadata: dict[str, str] = field(default_factory=dict)


def set_default_observer(observer: Observer | None) -> Observer | None:
    """Install the process-wide observer. Returns the one it replaced."""
    global _default_observer
    previous, _default_observer = _default_observer, observer
    return previous


def get_default_observer() -> Observer | None:
    return _default_observer


@contextlib.contextmanager
def observe(observer: Observer) -> Iterator[Observer]:
    """Install an observer for the duration of a block, then restore the previous one."""
    previous = set_default_observer(observer)
    try:
        yield observer
    finally:
        set_default_observer(previous)
