"""
tracker.py — ProvTracker: accumulate W3C PROV records, serialise to PROV-JSON
=============================================================================
The core of yProv4AgenticAI. A framework-agnostic, dependency-free buffer that an
agent drives explicitly (Strategy B): ``register_entity`` names the typed entity a
step produced, ``record_activity`` names the typed ``yp:`` activity with its lineage,
optional energy delta, optional confidence, the decision ``justification``, and any
extra attributes (e.g. prompt provenance). ``to_prov_json`` emits a standard W3C
PROV-JSON document.

Two layers, one buffer. ``register_entity`` / ``record_activity`` / ``to_prov_json`` are
the primitives; ``step`` / ``activity`` / ``decide`` / ``emit`` are the capture API a
developer writes against, and they are thin — they add no record kind of their own (see
:mod:`yprov4agenticai.api`).

No external PROV library is required — the document is built directly. Granularity
(DATA vs METADATA), persistence label, and the agent label are per-instance config
that round-trips through ``to_state``/``from_state`` so the tracker can be carried
inside an agent's serialisable state (e.g. a LangGraph ``State`` dict).
"""

from __future__ import annotations

import datetime
import functools
import inspect
from collections.abc import Callable, Iterator, Mapping, Sequence
from contextlib import contextmanager
from typing import Any, TypeVar

from .api import SCOPE_PARAMETER, ActivityScope, Span
from .energy import EnergyTracker
from .observer import Observer
from .rules import Rule, Scalar, canonical, check

YP_NAMESPACE = "http://hpci-lab.org/yProv4Agents#"

#: Granularity: how much of the graph is built.
GRANULARITY_DATA = "DATA"
GRANULARITY_METADATA = "METADATA"
GRANULARITIES = (GRANULARITY_DATA, GRANULARITY_METADATA)

#: Persistence: whether ``emit()`` hands the finished document to its sink.
#:
#: TRANSIENT means the record is built and returned but not written out. It exists to
#: isolate serialisation cost from construction cost in the overhead study, and for the
#: case where a record is wanted in memory — forwarded, inspected, discarded — without
#: touching a disk.
#:
#: This was a pure label until Phase 4: it was stored, reported in the bundle descriptor,
#: and had no effect whatsoever, so a record marked TRANSIENT was written to disk like any
#: other (review finding F4). A configuration flag that does not configure anything is
#: worse than a missing feature, because the record asserts something untrue about itself.
PERSISTENCE_PERSISTENT = "PERSISTENT"
PERSISTENCE_TRANSIENT = "TRANSIENT"
PERSISTENCES = (PERSISTENCE_PERSISTENT, PERSISTENCE_TRANSIENT)

_R = TypeVar("_R")


def now_iso() -> str:
    """Current UTC instant, ISO 8601, as recorded in ``prov:startTime``/``prov:endTime``.

    The one clock in the system. Timestamps from different clocks are the usual reason two
    records of the same run disagree about ordering, and the analytics agent reads these
    values as a timeline — so the library that writes them owns the definition and the
    runtime imports it rather than re-deriving it.
    """
    return datetime.datetime.now(datetime.UTC).isoformat()


@functools.cache
def _library_version() -> str:
    """Version of the library that wrote a record, stamped into its bundle descriptor.

    Read from installed distribution metadata so it cannot drift from pyproject.toml, and
    cached because that read goes to the filesystem.

    The cache is not a micro-optimisation. Uncached, this call is **6.78 ms** on the
    development machine and ``to_prov_json`` is 7.14 ms — so 95% of the cost of
    serialising a provenance document was a metadata lookup, paid once per emitted record,
    per agent, per message. The overhead study found it at Level 1 (see
    ``docs/E6_RESULTS.md``), which is what Level 1 is for.

    Safe to cache for the lifetime of the process: the installed version of a package
    cannot change under a running interpreter.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("yprov4agents-demonstrator")
    except PackageNotFoundError:
        return "0.0.0+unknown"


class ProvTracker:
    """Accumulates W3C PROV records and serialises them to PROV-JSON.

    Parameters
    ----------
    sid          : session id (used to mint activity/entity URIs).
    granularity  : "DATA" (full graph: activities + entities + lineage) or
                   "METADATA" (typed activities only, no entities/entity relations).
    persistence  : PERSISTENT (default) or TRANSIENT. TRANSIENT builds and returns the
                   record but does not hand it to the sink — see ``emit``.
    agent_label  : prov:label for the single (operational) agent node.
    principal    : optional {"label": str, "type": qname} — the agent or person on
                   whose authority the operational agent acted. When set, emits a
                   second prov:Agent node and one prov:actedOnBehalfOf edge
                   (harmonised from yProv4WFs; standard PROV relation, no new
                   predicate). Default None => no principal node/edge is emitted
                   (the default output stays byte-identical).
    sink         : optional callable invoked by ``emit()`` with the serialised
                   document — a bus publish, a file write, an HTTP POST. Keeping it a
                   plain callable is what keeps this module free of any transport
                   dependency. Not part of ``to_state``: a tracker restored from a
                   state dict has no sink, because a destination is a property of the
                   process that is running, not of the record it is building.
    verbose      : whether ``record_activity`` narrates each step on stdout. The
                   demonstrator's console trace is a teaching device; a measurement run
                   or a library embedded in someone else's agent wants it off.
    observer     : optional Layer-1 hook, receiving a restricted view of each step as it
                   finishes (:mod:`yprov4agenticai.observer`). Falls back to the
                   process-wide default. Steps recorded through ``record_activity``
                   directly are not observed — the hook sits in the capture API, which is
                   where a step's boundaries are known.
    energy       : optional :class:`~yprov4agenticai.energy.EnergyTracker`. When given,
                   every step closed through the capture API carries the energy and
                   emissions attributable to it. Off by default: a sampling backend has a
                   cost of its own, and a measurement run that is about time should not be
                   paying it.
    """

    YP = YP_NAMESPACE

    def __init__(
        self,
        sid: str,
        granularity: str = "DATA",
        persistence: str = "PERSISTENT",
        agent_label: str = "NL_Summarizer",
        principal: dict | None = None,
        sink: Callable[[dict], Any] | None = None,
        verbose: bool = True,
        observer: Observer | None = None,
        energy: EnergyTracker | None = None,
    ) -> None:
        if granularity not in GRANULARITIES:
            raise ValueError(f"granularity must be one of {GRANULARITIES}, got {granularity!r}")
        if persistence not in PERSISTENCES:
            raise ValueError(
                f"persistence must be one of {PERSISTENCES}, got {persistence!r}. It is a "
                f"behavioural setting, not a free-text label: TRANSIENT suppresses the sink."
            )
        self.sid = sid
        self.granularity = granularity
        self.persistence = persistence
        self.agent_label = agent_label
        self.sink = sink
        self.verbose = verbose
        # Layer-1 observation hook. None means "use the process-wide default, if any";
        # see yprov4agenticai.observer for why that default is global.
        self.observer = observer
        # Optional energy source, read once per step (see `activity`). One tracker per
        # agent, not per record: a backend that samples hardware counters has a start-up
        # cost, and restarting it per message would measure the restart.
        self.energy = energy
        if energy is not None:
            # Discard whatever accumulated before this record began. The energy tracker
            # outlives any one record, so the first delta would otherwise cover the agent's
            # idle time since the previous message and be charged to this record's first
            # step. Measured before this line existed: 28.3 seconds of waiting attributed
            # to "receive producer record".
            energy.stop_carbon_tracked_block()
        # Optional delegation: the principal the operational agent acted for.
        # None (default) => no principal agent node and no prov:actedOnBehalfOf
        # edge, so to_prov_json is byte-identical to the output without a principal.
        self.principal = principal
        self.acts: list = []
        self.ents: dict = {}  # eid -> {t: kind, v: value, [pid, hash]}
        self._ent_order: list = []
        #: Declared (source, generated) entity pairs. Only what a caller stated.
        self.derivations: list[tuple[str, str]] = []
        #: O(1) membership mirror of `derivations`, for the duplicate check.
        #:
        #: The check used to scan the list — `(source, eid) not in self.derivations` — which
        #: is O(n) per declaration and therefore O(n²) over a run. The scaling study
        #: measured the consequence: building a 10,000-activity record took 1.72 s and the
        #: fitted exponent was 1.47, not 1.0. The same pattern was already solved three
        #: fields below for memory ids and simply not applied here, which is how this kind
        #: of defect survives review: it is invisible at the six-activity scale every test
        #: and every demo uses, and it only bites a long-lived agent.
        self._derivation_pairs: set[tuple[str, str]] = set()
        # ── Optional, additive features (memory model + lightweight PID) ──────
        # These stay EMPTY on a default lifecycle run, so to_prov_json emits the
        # exact same document (the default 6/6/27 graph is byte-identical).
        self.mem_chain: list = []  # ordered MemoryStateEntity ids (revision chain)
        self._mem_ids: set = set()  # O(1) dedup mirror of mem_chain (not serialised)
        self.used_mem: list = []  # (activity_id, memory_eid) extra used-edges

    # ── called once per lifecycle node to register the entity it produced ─────
    def register_entity(
        self,
        kind: str,
        value: str,
        suffix: str = "",
        pid: str | None = None,
        content_hash: str | None = None,
        derived_from: str | Sequence[str] | None = None,
    ) -> str:
        """Name the typed entity a step produced.

        Parameters
        ----------
        pid, content_hash
            Optional, for the pointers-not-payloads design: when the payload lives in a
            data store the entity carries only ``yp:pid`` and ``yp:contentHash``.
        derived_from
            The entity or entities this one was actually derived from. Declaring it emits
            ``prov:wasDerivedFrom``.

            This must be stated, never inferred. The tracker previously asserted a
            derivation between each entity and the one registered before it, so
            registration order silently became lineage: two unrelated entities recorded in
            sequence produced a claim that one came from the other. Those edges accounted
            for a large share of the relations the evaluation counted, and a provenance
            system cannot assert lineage it did not observe.
        """
        eid = f"yp:{kind}_{self.sid}{suffix}"
        # METADATA mode: skip entity storage entirely. The ID is still returned so
        # callers' downstream string references don't break (filtered at serialisation
        # time — see to_prov_json).
        if self.granularity == "METADATA":
            return eid
        if eid not in self.ents:
            self._ent_order.append(eid)
        rec = {"t": kind, "v": value}
        if pid is not None:
            rec["pid"] = pid
        if content_hash is not None:
            rec["hash"] = content_hash
        self.ents[eid] = rec

        if derived_from:
            sources = [derived_from] if isinstance(derived_from, str) else list(derived_from)
            for source in sources:
                if source and (source, eid) not in self._derivation_pairs:
                    self._derivation_pairs.add((source, eid))
                    self.derivations.append((source, eid))
        return eid

    # ── optional: register a MemoryStateEntity for a loop turn ─────────────────
    def register_memory(
        self,
        value: str,
        turn: int | None = None,
        pid: str | None = None,
        content_hash: str | None = None,
    ) -> str:
        """Mint a yp:MemoryStateEntity (memory model). Each call
        appends to a revision chain: memory turn k prov:wasDerivedFrom turn k-1,
        typed prov:Revision at serialisation time (PROV-JSON has no standalone
        wasRevisionOf record). Memory entities are deliberately kept OUT of
        self._ent_order so the linear lifecycle derivation spine is untouched.
        Heavy transcripts live behind pid/content_hash. Returns the entity id.

        Additive: if never called, self.mem_chain stays empty and no memory
        nodes/relations are emitted (default output unchanged)."""
        if turn is None:
            turn = len(self.mem_chain)
        eid = f"yp:MemoryStateEntity_{self.sid}_m{turn}"
        if self.granularity == "METADATA":
            return eid
        rec = {"t": "MemoryStateEntity", "v": value}
        if pid is not None:
            rec["pid"] = pid
        if content_hash is not None:
            rec["hash"] = content_hash
        self.ents[eid] = rec
        if eid not in self._mem_ids:  # O(1) set membership (was O(N) on the list)
            self.mem_chain.append(eid)
            self._mem_ids.add(eid)
        return eid

    # ── called once per lifecycle node to record the activity ─────────────────
    def record_activity(
        self,
        kind: str,
        label: str,
        t0: str,
        t1: str,
        in_eid: str | None = None,
        out_eid: str | None = None,
        justification: str | None = None,
        energy_attrs: dict | None = None,
        confidence_score: float | None = None,
        extra_attrs: dict | None = None,
        used_mem: str | None = None,
        verbose: bool | None = None,
    ) -> None:
        # extra_attrs carries any additional yp: attributes to stamp onto the
        # activity — e.g. prompt provenance (yp:prompt_id / _version / _sha) so the
        # PROV record documents which prompt contract drove each LLM step.
        # used_mem (optional, memory model): the MemoryStateEntity this activity
        # reasoned over this turn — emitted as an extra prov:used edge. Omitting it
        # (the default) adds nothing to the graph.
        n = len(self.acts)  # unique counter keeps retry IDs distinct
        aid = f"{kind}_{self.sid}_{n:02d}"
        self.acts.append(
            {
                "id": aid,
                "kind": kind,
                "label": label,
                "t0": t0,
                "t1": t1,
                "in_eid": in_eid,
                "out_eid": out_eid,
                "j": justification,
                "c": confidence_score,
                "e": energy_attrs or {},
                "x": extra_attrs or {},
            }
        )
        if used_mem:
            self.used_mem.append((f"yp:{aid}", used_mem))
        # None means "whatever this tracker was configured for"; an explicit argument
        # still wins, so a caller can silence one noisy step without touching the rest.
        if not (self.verbose if verbose is None else verbose):
            return
        ms = self._ms(t0, t1)
        mark = "  <- yp:justification SET" if justification else ""
        if confidence_score is not None:
            mark += f"  [conf={confidence_score:.2f}]"
        if energy_attrs and "yp:energy_kwh" in energy_attrs:
            kwh = energy_attrs["yp:energy_kwh"]
            source = energy_attrs.get("yp:energy_source", "?")
            mark += f"  [{kwh * 1e6:.2f} mWh, src={source}]"
        print(f"  [PROV] {kind:<28} ({ms} ms){mark}")

    @staticmethod
    def _ms(t0: str, t1: str) -> str:
        try:
            d = datetime.datetime.fromisoformat(t1) - datetime.datetime.fromisoformat(t0)
            return str(int(d.total_seconds() * 1000))
        except Exception:
            return "?"

    # ── the capture API: a decorator, a context manager, one explicit call ─────
    #
    # These four methods are the surface a developer writes against; everything above is
    # the buffer they write into. See yprov4agenticai/api.py for the design argument and
    # docs/INSTRUMENTATION.md §3 for the walkthrough.
    #
    # They add no record kind of their own. Anything expressible through them is
    # expressible through record_activity, which is what makes the layer safe to adopt
    # incrementally and what tests/test_capture_api.py asserts directly.

    def step(
        self,
        kind: str,
        label: str | None = None,
        *,
        produces: str | None = None,
    ) -> Callable[[Callable[..., _R]], Callable[..., _R]]:
        """Decorate a function so that calling it records a typed activity.

        Automatic: the activity's type, its label, its start and end instants, its
        attribution to this tracker's agent, and its position in the sequence. Also the
        failure case — a call that raises is recorded as having run and failed.

        Declared, never inferred: lineage. A function that needs to state what it read or
        wrote declares a ``prov`` parameter and receives its :class:`ActivityScope`::

            @tracker.step("ToolCallActivity")
            def run(payload: str, *, prov: ActivityScope) -> str:
                prov.consumes(task_entity)
                return tool.run(payload)

        Parameters
        ----------
        kind
            ``yp:`` activity type, without the prefix (e.g. ``"PlanningActivity"``).
        label
            ``prov:label``. Defaults to the function's qualified name, which is accurate
            but rarely what a reader of the graph wants to see.
        produces
            Entity type to register the return value as, for the common case of a step
            whose output *is* its return value. The value is stringified, so a heavy
            payload should go to the store and be declared with
            :meth:`ActivityScope.produces` instead.

        Notes
        -----
        The decorator binds to this tracker instance, so it applies to functions defined
        where the tracker is already in scope — a module-level pipeline, or a closure
        inside a session handler. Code whose tracker is created per request (every agent
        in this demonstrator) reads better with :meth:`activity`.
        """

        def decorate(function: Callable[..., _R]) -> Callable[..., _R]:
            wants_scope = SCOPE_PARAMETER in inspect.signature(function).parameters

            @functools.wraps(function)
            def wrapper(*args: Any, **kwargs: Any) -> _R:
                scope = ActivityScope(self, kind, label or function.__qualname__)
                if wants_scope:
                    kwargs[SCOPE_PARAMETER] = scope
                span = Span()
                try:
                    result = function(*args, **kwargs)
                except BaseException as error:
                    self._read_energy(scope)
                    scope.record(span.start_iso, span.end_iso(), error)
                    raise
                if produces is not None and result is not None:
                    scope.produces(produces, str(result))
                self._read_energy(scope)
                scope.record(span.start_iso, span.end_iso(), None)
                return result

            return wrapper

        return decorate

    def _read_energy(self, scope: ActivityScope) -> None:
        """Attribute the energy since the previous step boundary to this step.

        Read at every boundary rather than around one chosen step, which is what makes the
        attribution correct: ``stop_carbon_tracked_block`` returns the delta since it was
        last called, so skipping a step would silently fold that step's consumption into
        its successor's.
        """
        if self.energy is None:
            return
        scope.measured(self.energy.stop_carbon_tracked_block().to_attrs())

    @contextmanager
    def activity(self, kind: str, label: str | None = None) -> Iterator[ActivityScope]:
        """Record a typed activity around a block, and hand it a scope to declare with.

        The activity is written when the block exits, by either path::

            with tracker.activity("ToolCallActivity", "render the chart") as prov:
                prov.consumes(task)
                chart = render(payload)
                prov.produces("ToolResultEntity", chart.name, derived_from=task)

        A body that raises is recorded with ``yp:outcome`` and ``yp:error_type``, and the
        exception propagates untouched. The record then shows a step that ran and failed,
        rather than a hole where a step should be.
        """
        scope = ActivityScope(self, kind, label or kind)
        span = Span()
        try:
            yield scope
        except BaseException as error:
            self._read_energy(scope)
            scope.record(span.start_iso, span.end_iso(), error)
            raise
        self._read_energy(scope)
        scope.record(span.start_iso, span.end_iso(), None)

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
        """Record a decision: the verdict, and the reasoning that produced it.

        The one call in this API that a machine could not have written. Everything else
        here observes what happened; this states *why* it happened, which is available
        only at the moment of decision and only to the code making it.

        ``justification`` is mandatory and must not be blank. The alternative — a decision
        activity with an empty rationale — is indistinguishable in the record from a
        decision nobody could explain, so the API rejects it at the call site where the
        reasoning still exists.

        Parameters
        ----------
        verdict
            The outcome, recorded as ``yp:verdict`` (ACCEPT, REVISE, ESCALATE, …).
        justification
            Why that verdict, in terms of the evidence that produced it.
        confidence
            Optional score behind the verdict, recorded as ``yp:confidence_score``.
        rule, evidence
            The named rule that produced the verdict and the values it read. Recorded as
            ``yp:rule`` and ``yp:rule_inputs``, so the decision can be replayed from its
            record; the call raises if the rule, applied to the evidence, does not give
            the verdict (and escalation reason) being recorded.
        started_at
            When the deliberation began. Defaults to now, making the decision
            instantaneous in the record — right for a threshold comparison, wrong for a
            verdict that took work to reach, which should pass the instant that work
            started.

        Raises
        ------
        ValueError
            ``justification`` is empty or whitespace.
        """
        if not justification.strip():
            raise ValueError(
                f"verdict {verdict!r} was given no justification. A decision activity "
                f"with an empty rationale cannot be told apart from an unexplained "
                f"decision; state the evidence that produced the verdict."
            )
        attributes = dict(attributes or {})
        if (rule is None) != (evidence is None):
            raise ValueError("rule and evidence are recorded together or not at all")
        if rule is not None and evidence is not None:
            problems = check(rule, evidence, {**attributes, "yp:verdict": verdict})
            if problems:
                # The record would claim a verdict its own rule did not reach. Refused here,
                # where the caller can still see which branch it took.
                raise ValueError("decision disagrees with its rule: " + "; ".join(problems))
            attributes["yp:rule"] = rule.name
            attributes["yp:rule_inputs"] = canonical(evidence)
        # Routed through an ActivityScope like every other step, for two reasons. The
        # extent is measured rather than being one wall-clock reading taken twice (the
        # work of *reaching* the verdict happens in the caller; pass ``started_at`` to
        # include it). And a decision is a step, so an installed Layer-1 observer has to
        # see that it ran — denying it that would make the Strategy A baseline weaker than
        # a real callback handler, which is the one thing a baseline must never be. What
        # the observer does not receive is the verdict and the justification, because
        # those never crossed a node boundary.
        span = Span()
        scope = ActivityScope(self, kind, label or f"decide: {verdict}")
        scope.consumed = consumed
        scope.generated = generated
        scope.justification = justification
        scope.verdict = verdict
        scope.confidence = confidence
        scope.attributes = attributes
        # Read the energy here too. Skipping it would not merely omit a decision's own
        # consumption, it would fold that consumption into the next step's delta — and a
        # misattributed measurement is worse than a missing one.
        self._read_energy(scope)
        scope.record(started_at or span.start_iso, span.end_iso(), None)

    def emit(self, sink: Callable[[dict], Any] | None = None) -> dict:
        """Serialise to PROV-JSON and hand the document to a sink.

        Returns the document either way, so a caller that wants to inspect, archive or
        forward it can. The sink argument wins over the one given to the constructor,
        which is how a measurement run redirects output without reconfiguring the agent.

        A ``TRANSIENT`` tracker builds and returns the document but does not call the
        sink. The distinction is deliberately narrow and worth stating precisely: it
        suppresses *handing the record out*, not building it. A transient record is still
        serialised, still returned to the caller, and in this demonstrator still published
        onto the bus by the agent that made it — what it is not is written to the output
        volume. That is the layer the overhead study needs isolated, and it is the only
        thing the flag has ever claimed.
        """
        doc = self.to_prov_json()
        if self.persistence == PERSISTENCE_TRANSIENT:
            return doc
        destination = sink if sink is not None else self.sink
        if destination is not None:
            destination(doc)
        return doc

    # ── serialise/restore (the tracker can ride inside an agent State dict) ────
    def to_state(self) -> dict:
        return {
            "acts": self.acts,
            "ents": self.ents,
            "eo": self._ent_order,
            "derivations": self.derivations,
            "granularity": self.granularity,
            "persistence": self.persistence,
            "agent_label": self.agent_label,
            "principal": self.principal,
            # optional memory features (absent/empty on default runs)
            "mem_chain": self.mem_chain,
            "used_mem": self.used_mem,
        }

    @classmethod
    def from_state(cls, sid: str, d: dict) -> ProvTracker:
        t = cls(
            sid,
            granularity=d.get("granularity", "DATA"),
            persistence=d.get("persistence", "PERSISTENT"),
            agent_label=d.get("agent_label", "NL_Summarizer"),
            principal=d.get("principal"),
        )  # backward-compatible (None if absent)
        t.acts = list(d["acts"])
        t.ents = dict(d["ents"])
        t._ent_order = list(d["eo"])
        t.derivations = [tuple(pair) for pair in d.get("derivations", [])]
        t._derivation_pairs = set(t.derivations)  # rebuild the O(1) dedup mirror
        t.mem_chain = list(d.get("mem_chain", []))  # backward-compatible
        t._mem_ids = set(t.mem_chain)  # rebuild O(1) dedup mirror
        t.used_mem = [tuple(x) for x in d.get("used_mem", [])]
        return t

    # ── builds W3C PROV-JSON document ─────────────────────────────────────────
    def _informed_by(self) -> dict[str, dict[str, str]]:
        """Derive ``prov:wasInformedBy`` from the dataflow the activities declared.

        PROV defines the relation exactly: activity *a2* was informed by *a1* when *a1*
        generated an entity that *a2* used. That makes it derivable rather than something
        a caller has to assert, so it is computed here from the recorded in/out entities.

        The previous implementation instead linked every activity to the one recorded
        before it by index, which claimed a dataflow dependency between consecutive steps
        whether or not one existed, and produced exactly n-1 edges for any n activities —
        a count that measured how many steps ran, not how they were connected.
        """
        generated_by: dict[str, str] = {}
        for activity in self.acts:
            if activity["out_eid"]:
                generated_by.setdefault(activity["out_eid"], f"yp:{activity['id']}")

        edges: dict[str, dict[str, str]] = {}
        seen: set[tuple[str, str]] = set()
        for activity in self.acts:
            consumed = activity["in_eid"]
            if not consumed:
                continue
            informant = generated_by.get(consumed)
            informed = f"yp:{activity['id']}"
            if informant is None or informant == informed:
                continue
            if (informed, informant) in seen:
                continue
            seen.add((informed, informant))
            edges[f"_:i{len(edges)}"] = {
                "prov:informed": informed,
                "prov:informant": informant,
            }
        return edges

    def _bundle_descriptor(self, qn: Callable[[str], dict]) -> dict:
        """Describe the emitted document, inside the document, as a ``prov:Bundle``.

        A reader needs to know how the record was configured to interpret it: whether
        entities were recorded at all (granularity), whether payloads were persisted
        (persistence), and which library version wrote it.

        PROV already has an idiom for this. A bundle is a named set of provenance
        descriptions and is itself an entity, so describing it is an ordinary entity
        record that any conforming consumer reads. The earlier approach — a top-level
        ``_yprov4agents_meta`` key — put the same information somewhere the format does
        not allow, and a conforming parser rejects the whole document rather than
        skipping the key it does not recognise.
        """
        return {
            "prov:type": qn("prov:Bundle"),
            "prov:label": f"provenance record emitted by {self.agent_label}",
            "yp:granularity": self.granularity,
            "yp:persistence": self.persistence,
            "yp:emitted_by": __name__.split(".")[0],
            "yp:library_version": _library_version(),
        }

    def to_prov_json(self) -> dict:
        """Build a W3C PROV-JSON document honouring the granularity flag.

        DATA mode (default): full PROV — activities, entities, used,
        wasGeneratedBy, wasDerivedFrom, wasAssociatedWith, wasInformedBy.

        METADATA mode: typed activities only. No data entities, so used,
        wasGeneratedBy and wasDerivedFrom are omitted; wasAssociatedWith and
        wasInformedBy are activity-level and are retained.

        The document stays self-describing in both modes through a ``prov:Bundle``
        entity (see ``_bundle_descriptor``) rather than a bespoke top-level key. Only
        the containers PROV-JSON defines appear at the document root, because an
        unrecognised one makes a conforming parser reject the entire document.
        """

        def qn(name: str) -> dict:
            """Wrap a qualified name in the PROV-JSON typed-literal form."""
            return {"$": name, "type": "prov:QUALIFIED_NAME"}

        doc: dict = {
            "prefix": {
                "yp": self.YP,
                "prov": "http://www.w3.org/ns/prov#",
                "xsd": "http://www.w3.org/2001/XMLSchema#",
            },
            "agent": {
                f"yp:agent_{self.sid}": {
                    "prov:type": qn("yp:OperationalAgent"),
                    "prov:label": self.agent_label,
                }
            },
            "activity": {},
            # entity and relation sections are added below (keeps key order stable)
        }

        # ── Entity section + entity-side relations ───────────────────────────
        # The bundle descriptor is emitted in both modes: it is metadata about the
        # record, not one of the data entities METADATA mode is defined to omit.
        doc["entity"] = {f"yp:bundle_{self.sid}": self._bundle_descriptor(qn)}

        if self.granularity == "DATA":
            for eid, e in self.ents.items():
                node = {"prov:type": qn(f"yp:{e['t']}"), "prov:value": e["v"]}
                if "pid" in e:
                    node["yp:pid"] = e["pid"]  # lightweight-prov pointer
                if "hash" in e:
                    node["yp:contentHash"] = e["hash"]  # integrity (sha-256)
                doc["entity"][eid] = node
            # Only derivations a caller declared. Nothing is inferred from the order in
            # which entities happened to be registered.
            doc["wasDerivedFrom"] = {
                f"_:d{i}": {
                    "prov:generatedEntity": generated,
                    "prov:usedEntity": source,
                }
                for i, (source, generated) in enumerate(self.derivations)
            }
            # Memory revision chain (optional): m_k wasDerivedFrom m_{k-1}, typed
            # prov:Revision. PROV-JSON has no standalone wasRevisionOf record, so
            # this typed-derivation form is what round-trips. Empty mem_chain =>
            # nothing added (default output unchanged).
            for i in range(1, len(self.mem_chain)):
                doc["wasDerivedFrom"][f"_:rev{i}"] = {
                    "prov:generatedEntity": self.mem_chain[i],
                    "prov:usedEntity": self.mem_chain[i - 1],
                    "prov:type": qn("prov:Revision"),
                }

        # ── Activities and activity-level relations (both modes) ─────────────
        wasc, used, wgb, wib = {}, {}, {}, {}
        for i, a in enumerate(self.acts):
            aid = f"yp:{a['id']}"
            act = {
                "prov:type": qn(f"yp:{a['kind']}"),
                "prov:label": a["label"],
                "prov:startTime": a["t0"],
                "prov:endTime": a["t1"],
            }
            if a["j"]:
                act["yp:justification"] = a["j"]
            if a.get("c") is not None:
                act["yp:confidence_score"] = a["c"]
            for k, v in (a.get("e") or {}).items():
                act[k] = v
            for k, v in (a.get("x") or {}).items():
                act[k] = v
            doc["activity"][aid] = act
            wasc[f"_:a{i}"] = {"prov:activity": aid, "prov:agent": f"yp:agent_{self.sid}"}
            if self.granularity == "DATA":
                if a["in_eid"]:
                    used[f"_:u{i}"] = {"prov:activity": aid, "prov:entity": a["in_eid"]}
                if a["out_eid"]:
                    wgb[f"_:g{i}"] = {"prov:entity": a["out_eid"], "prov:activity": aid}

        # Memory used-edges (optional): each LLM step used the MemoryStateEntity of
        # its turn. Empty used_mem => nothing added (default output unchanged).
        if self.granularity == "DATA":
            for j, (aid, meid) in enumerate(self.used_mem):
                used[f"_:um{j}"] = {"prov:activity": aid, "prov:entity": meid}

        wib = self._informed_by()
        doc["wasAssociatedWith"] = wasc
        doc["wasInformedBy"] = wib
        if self.granularity == "DATA":
            doc["used"] = used
            doc["wasGeneratedBy"] = wgb

        # ── Optional delegation: who the operational agent acted for ─────────
        # Records "who" — the principal (person/agent/workflow) on whose
        # authority the operational agent acted — via the standard PROV relation
        # prov:actedOnBehalfOf (harmonised from yProv4WFs; no new predicate).
        # Empty by default => nothing added.
        if self.principal:
            principal_id = f"yp:principal_{self.sid}"
            doc["agent"][principal_id] = {
                "prov:type": qn(self.principal.get("type", "prov:Agent")),
                "prov:label": self.principal["label"],
            }
            doc["actedOnBehalfOf"] = {
                "_:aob0": {
                    "prov:delegate": f"yp:agent_{self.sid}",
                    "prov:responsible": principal_id,
                },
            }
        return doc
