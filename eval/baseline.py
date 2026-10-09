"""Strategy A: the best W3C PROV a framework callback handler can produce.

This is the comparator the paper's central claim is measured against, and it is built to be
*generous*. The temptation is to write a weak baseline and win; the result would be
worthless, and a reviewer who has written a LangChain callback handler would know it
immediately.

So this logger reconstructs everything a handler can genuinely reconstruct:

* one ``prov:Activity`` per node, with its label and real start and end instants
* a ``prov:Agent`` per chain, and ``wasAssociatedWith`` for every activity
* entities for every distinct payload that crossed a node boundary
* ``used`` and ``wasGeneratedBy`` from the inputs and outputs it was handed
* ``wasInformedBy``, derived from dataflow exactly as the explicit tracker derives it
* ``wasDerivedFrom``, by the only rule available to it (see :meth:`_derivations`)
* typed activities, when configured with a label-to-type map (see :data:`DEFAULT_TYPE_MAP`)
* failures, because ``on_chain_error`` fires

Three things it cannot do, and the third is the point of the paper:

1. **It has no stable identity for a payload.** A handler is given values, not references,
   so entity identity has to be minted from content. Two steps that emit equal payloads
   collapse to one entity; one payload that changes becomes two. ``yp:pid`` and
   ``yp:contentHash`` — the pointers-not-payloads design — have no analogue here at all.
2. **It cannot distinguish generation from derivation.** It sees a node read X and wrote Y.
   Whether Y was *derived from* X or merely produced alongside it is not observable, so the
   rule has to be assumed. :func:`compare` measures how far that assumption overshoots.
3. **It cannot see a decision's rationale.** Not because of an implementation shortcut:
   the justification was never a node input or output. It was a local variable at the
   moment of decision, which is the whole argument. :func:`unreachable_rationale` tests
   this rather than asserting it — it searches everything the handler observed for the
   justification text the explicit record contains.

**What this baseline is, precisely: an upper bound.** The event stream it consumes is
derived from the declarations the explicit instrumentation made — the entity each step said
it read and the entity each step said it wrote. A real callback handler does not get that.
It is handed the node's whole input payload and has to guess which part of it mattered, so
it over-reports ``used`` and cannot tell a load-bearing input from an incidental one. By
feeding this handler B's own declarations, the comparison concedes to A every structural
fact B had to state, which is the most generous input possible.

That is deliberate, and it is what makes the conclusion safe: the claim is not *a callback
handler recovers less structure*, which would depend on how good the handler is. The claim
is that **even granted every structural fact explicit instrumentation declares, the
rationale is still unreachable** — and a bound beats a measurement of one implementation.

Used by two experiments, deliberately the same artifact in both:

    E3  the capability comparison          (this module's `compare`)
    E6  the `A` instrumentation condition  (docs/E6_OVERHEAD.md §4.1)

    python -m eval.baseline --prov-dir out --baseline-dir out/baseline
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
from collections import defaultdict
from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

from yprov4agenticai.observer import StepEvent

PROV_NS = "http://www.w3.org/ns/prov#"
#: Namespace for the types a *mapped* handler assigns. Reusing the project's own namespace
#: is the generous reading: it lets A's output be compared type-for-type with B's.
YP_NS = "http://hpci-lab.org/yProv4Agents#"

#: Characters of the content hash used to identify a payload. A handler has nothing better.
_ID_CHARS = 12

#: Label patterns a hand-written mapping uses to assign activity types, tried in order.
#:
#: Every entry here is a cost that belongs to condition A and should be reported with it.
#: The types are not free: someone reads the agents, writes the rules, and maintains them
#: as labels change. They also only work because this project's labels are descriptive —
#: a graph whose nodes are called ``node_1``..``node_6`` yields nothing. Explicit
#: instrumentation declares the type instead, which is why B needs no map.
#:
#: The decision rule is the interesting one: identifying the decision step means
#: enumerating the verdict vocabulary in advance. It can be done, and it is done here, but
#: note what it means — the map has to know the system's possible conclusions to find the
#: step that concludes.
DEFAULT_TYPE_MAP: tuple[tuple[str, str], ...] = (
    (r"receive", "ReceiveTaskActivity"),
    (r"\b(plan|scope|choose|select|await)\b", "PlanningActivity"),
    (
        r"\b(accept|revise|escalate|reject|pass|fail|anomaly|ok|complete|partial|trail)\b",
        "DecisionActivity",
    ),
    (r"\b(check|verify|confirm|validate|observe)\b", "ObservationActivity"),
    (r"\b(audit)\b", "AuditActivity"),
    (r"\b(compliance)\b", "ComplianceCheckActivity"),
    (r"\b(mine|analys|analyz)\b", "AnalysisActivity"),
    (r"\b(store|persist|write to)\b", "PersistActivity"),
    (r"\b(respond|publish|emit|report)\b", "ResponseActivity"),
    (r"\b(render|write|call|run|tool)\b", "ToolCallActivity"),
)


#: Verdict tokens a handler can look for in a node's label. Whether this finds anything is
#: a property of the labels, not of the observation channel: these agents interpolate the
#: verdict into the step label, so it is there to be scraped. A graph whose decision node is
#: called ``decide`` yields nothing. Measured rather than assumed — see :func:`compare`.
VERDICT_VOCABULARY = (
    "ACCEPT",
    "REVISE",
    "ESCALATE",
    "REJECT",
    "PASS",
    "FAIL",
    "ANOMALY",
    "OK",
    "COMPLETE",
    "PARTIAL",
)


def _digest(payload: str) -> str:
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()


#: Where the observation stream is written. This file, not the reconstructed PROV, is the
#: primary artifact of condition A: it is what was observed, and every reconstruction is
#: derived from it.
EVENTS_FILE = "_events.json"


def write_events(path: Path, events: Iterable[StepEvent]) -> int:
    """Persist an observation stream. Returns how many events were written."""
    payload = [asdict(event) for event in events]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    return len(payload)


def read_events(path: Path) -> list[StepEvent]:
    """Load an observation stream written by :func:`write_events`."""
    return [
        StepEvent(
            **{
                **record,
                "inputs": tuple(record.get("inputs", ())),
                "outputs": tuple(record.get("outputs", ())),
            }
        )
        for record in json.loads(path.read_text(encoding="utf-8"))
    ]


@dataclass
class _Chain:
    """Everything one callback handler observed about one run of one chain."""

    session: str
    agent: str
    events: list[StepEvent] = field(default_factory=list)


class CallbackLogger:
    """A framework-style callback handler that reconstructs PROV from node boundaries.

    Parameters
    ----------
    type_map
        Label patterns to activity types. ``None`` is the zero-configuration handler: every
        activity is an untyped ``prov:Activity``, which is what a handler attached to an
        unfamiliar graph produces. Pass :data:`DEFAULT_TYPE_MAP` for the configured case.
    assume_derivation
        Whether to assert ``wasDerivedFrom`` between a node's inputs and its outputs. True
        is the generous setting and the one reported; see :func:`compare` for what it
        costs in precision.
    """

    def __init__(
        self,
        type_map: Iterable[tuple[str, str]] | None = None,
        assume_derivation: bool = True,
    ) -> None:
        self._rules = [(re.compile(p, re.I), t) for p, t in (type_map or ())]
        self.assume_derivation = assume_derivation
        self._chains: dict[tuple[str, str], _Chain] = {}

    # ── the handler surface ───────────────────────────────────────────────────────
    def record(self, event: StepEvent) -> None:
        """Receive one finished node. This is the whole input to Strategy A."""
        key = (event.session, event.agent)
        chain = self._chains.get(key)
        if chain is None:
            chain = self._chains[key] = _Chain(session=event.session, agent=event.agent)
        chain.events.append(event)

    __call__ = record

    # ── reconstruction ────────────────────────────────────────────────────────────
    def _type_of(self, label: str) -> str | None:
        for pattern, activity_type in self._rules:
            if pattern.search(label):
                return activity_type
        return None

    @staticmethod
    def _scrape_verdict(label: str) -> str | None:
        """Recover a verdict from a node label, if the label happens to contain one."""
        for token in VERDICT_VOCABULARY:
            if re.search(rf"\b{token}\b", label, re.I):
                return token
        return None

    @staticmethod
    def _entity_id(payload: str) -> str:
        """Identify a payload by its content, which is all a handler can do."""
        return f"cb:entity_{_digest(payload)[:_ID_CHARS]}"

    def _derivations(self, event: StepEvent, activity_id: str) -> list[tuple[str, str]]:
        """Derivations this node implies, under the only rule available to a handler.

        The handler sees that a node read X and wrote Y. It cannot see whether Y was built
        from X or merely produced during the same call, so to report derivations at all it
        has to assume the first. The explicit instrumentation does not have to guess: the
        step states which of the things it read the output actually came from.
        """
        if not self.assume_derivation:
            return []
        return [
            (self._entity_id(source), self._entity_id(generated))
            for generated in event.outputs
            for source in event.inputs
        ]

    def to_prov_json(self, session: str, agent: str) -> dict[str, Any]:
        """Build the PROV-JSON document Strategy A can produce for one chain run."""
        chain = self._chains[(session, agent)]
        agent_id = f"cb:agent_{_digest(agent)[:_ID_CHARS]}"

        doc: dict[str, Any] = {
            "prefix": {"cb": "urn:yprov4agents:callback-baseline#", "prov": PROV_NS},
            "agent": {agent_id: {"prov:label": agent}},
            "activity": {},
            "entity": {},
        }
        if self._rules:
            doc["prefix"]["yp"] = YP_NS

        used: dict[str, dict[str, str]] = {}
        generated: dict[str, dict[str, str]] = {}
        associated: dict[str, dict[str, str]] = {}
        derived: dict[str, dict[str, str]] = {}
        #: payload id -> the activity that emitted it, for wasInformedBy
        producer: dict[str, str] = {}
        derivation_pairs: list[tuple[str, str]] = []

        for index, event in enumerate(chain.events):
            activity_id = f"cb:activity_{index:02d}"
            activity: dict[str, Any] = {
                "prov:label": event.name,
                "prov:startTime": event.started_at,
                "prov:endTime": event.ended_at,
            }
            activity_type = self._type_of(event.name)
            if activity_type:
                activity["prov:type"] = {
                    "$": f"yp:{activity_type}",
                    "type": "prov:QUALIFIED_NAME",
                }
                # A decision's verdict is sometimes recoverable — not from the decision,
                # but from the string the developer chose to label it with. Taking it is
                # the generous reading and it is reported as what it is: scraped text.
                verdict = (
                    self._scrape_verdict(event.name)
                    if activity_type == "DecisionActivity"
                    else None
                )
                if verdict:
                    activity["yp:verdict"] = verdict
                    activity["cb:verdict_source"] = "scraped-from-label"
            if event.error:
                activity["cb:error_type"] = event.error
            activity.update({f"cb:{k}": v for k, v in event.metadata.items()})
            doc["activity"][activity_id] = activity
            associated[f"_:a{index}"] = {
                "prov:activity": activity_id,
                "prov:agent": agent_id,
            }

            for payload in event.inputs:
                entity_id = self._entity_id(payload)
                # No type: a handler sees a payload, not what the payload is *for*.
                doc["entity"].setdefault(entity_id, {"prov:value": payload})
                used[f"_:u{len(used)}"] = {
                    "prov:activity": activity_id,
                    "prov:entity": entity_id,
                }
            for payload in event.outputs:
                entity_id = self._entity_id(payload)
                doc["entity"].setdefault(entity_id, {"prov:value": payload})
                generated[f"_:g{len(generated)}"] = {
                    "prov:entity": entity_id,
                    "prov:activity": activity_id,
                }
                producer.setdefault(entity_id, activity_id)

            derivation_pairs.extend(self._derivations(event, activity_id))

        for position, (source, target) in enumerate(dict.fromkeys(derivation_pairs)):
            derived[f"_:d{position}"] = {
                "prov:generatedEntity": target,
                "prov:usedEntity": source,
            }

        # wasInformedBy by the same rule the explicit tracker uses: a2 was informed by a1
        # when a1 generated something a2 used. Genuinely derivable from what was observed.
        informed: dict[str, dict[str, str]] = {}
        seen: set[tuple[str, str]] = set()
        for index, event in enumerate(chain.events):
            consumer = f"cb:activity_{index:02d}"
            for payload in event.inputs:
                informant = producer.get(self._entity_id(payload))
                if informant is None or informant == consumer:
                    continue
                if (consumer, informant) in seen:
                    continue
                seen.add((consumer, informant))
                informed[f"_:i{len(informed)}"] = {
                    "prov:informed": consumer,
                    "prov:informant": informant,
                }

        doc["used"] = used
        doc["wasGeneratedBy"] = generated
        doc["wasAssociatedWith"] = associated
        doc["wasInformedBy"] = informed
        doc["wasDerivedFrom"] = derived
        return doc

    def documents(self) -> dict[str, dict[str, Any]]:
        """Every chain run this handler observed, keyed as ``<session>__<agent>``."""
        return {
            f"{session}__{agent}": self.to_prov_json(session, agent)
            for session, agent in sorted(self._chains)
        }

    def events(self) -> list[StepEvent]:
        """Every step this handler observed, in the order it observed them."""
        return [event for chain in self._chains.values() for event in chain.events]

    def observed_payloads(self) -> list[str]:
        """Everything the handler could read — the entire visible world of Strategy A.

        Node labels included: a label is a string the handler is handed, so anything
        written into one is fair game for it to scrape.
        """
        payloads: list[str] = []
        for chain in self._chains.values():
            for event in chain.events:
                payloads.extend(event.inputs)
                payloads.extend(event.outputs)
                payloads.append(event.name)
        return payloads

    @classmethod
    def from_events(
        cls,
        events: Iterable[StepEvent],
        type_map: Iterable[tuple[str, str]] | None = None,
        assume_derivation: bool = True,
    ) -> CallbackLogger:
        """Rebuild a handler from a recorded event stream.

        The observation stream is the artifact of condition A; a reconstruction is a
        *function* of it. Keeping the two separate is what lets the same recorded run be
        reconstructed under several handler configurations — with and without a type map,
        say — without running the agents again and hoping they behaved identically.
        """
        logger = cls(type_map=type_map, assume_derivation=assume_derivation)
        for event in events:
            logger.record(event)
        return logger

    def write(self, out_dir: Path) -> int:
        """Write the observation stream and the PROV it reconstructs. Returns documents."""
        out_dir.mkdir(parents=True, exist_ok=True)
        documents = self.documents()
        for name, doc in documents.items():
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", name)
            (out_dir / f"{safe}__A_prov.json").write_text(
                json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8"
            )
        write_events(out_dir / EVENTS_FILE, self.events())
        return len(documents)


# ── measuring the difference ──────────────────────────────────────────────────────
REL_KEYS = (
    "used",
    "wasGeneratedBy",
    "wasInformedBy",
    "wasDerivedFrom",
    "wasAssociatedWith",
)


def _load(run_dir: Path, pattern: str) -> list[dict[str, Any]]:
    return [
        json.loads(path.read_text(encoding="utf-8")) for path in sorted(run_dir.glob(pattern))
    ]


#: Every signal the E3 table reports. Seeded explicitly so a metric that happens to be zero
#: is present and zero rather than absent — a consumer that has to guess whether a missing
#: key means "none" or "not measured" will eventually guess wrong.
SIGNALS = (
    "documents",
    "activities",
    "typed_activities",
    "entities",
    "typed_entities",
    "pointers",
    *REL_KEYS,
    "relations",
    "verdicts",
    "justifications",
)


def _totals(documents: list[dict[str, Any]]) -> dict[str, int]:
    totals: dict[str, int] = defaultdict(int, dict.fromkeys(SIGNALS, 0))
    for doc in documents:
        totals["documents"] += 1
        totals["activities"] += len(doc.get("activity") or {})
        totals["entities"] += len(doc.get("entity") or {})
        for key in REL_KEYS:
            totals[key] += len(doc.get(key) or {})
        for activity in (doc.get("activity") or {}).values():
            if activity.get("prov:type"):
                totals["typed_activities"] += 1
            if str(activity.get("yp:justification", "")).strip():
                totals["justifications"] += 1
            if activity.get("yp:verdict"):
                totals["verdicts"] += 1
        for entity in (doc.get("entity") or {}).values():
            if entity.get("prov:type"):
                totals["typed_entities"] += 1
            if entity.get("yp:pid"):
                totals["pointers"] += 1
    totals["relations"] = sum(totals[key] for key in REL_KEYS)
    return dict(totals)


def unreachable_rationale(
    explicit: list[dict[str, Any]], payloads: list[str]
) -> tuple[int, int, list[str]]:
    """Search the handler's visible world for what the explicit record holds.

    Returns ``(justifications_found, verdicts_found, the justification texts searched)``.

    This is the paper's central claim turned into a test. "A callback cannot capture the
    rationale" is only worth asserting if someone has looked: every justification in the
    explicit record is searched for across every string the handler was handed — payloads
    and node labels alike. A hit would mean the rationale *was* observable, and the claim
    would need narrowing.

    Verdicts are searched for separately, and for a reason. A verdict is a short token from
    a fixed vocabulary, and these agents interpolate it into the step label, so a handler
    can often recover it. That is a real capability and is reported as one. It also sharpens
    the argument rather than weakening it: what cannot be recovered is not the conclusion
    but the reasoning, which was never a value the step passed anywhere.
    """
    wanted: list[str] = []
    verdicts: list[str] = []
    for doc in explicit:
        for activity in (doc.get("activity") or {}).values():
            text = str(activity.get("yp:justification", "")).strip()
            if text:
                wanted.append(text)
            if activity.get("yp:verdict"):
                verdicts.append(str(activity["yp:verdict"]))
    haystack = "\n".join(payloads)
    found = sum(1 for text in wanted if text in haystack)
    verdicts_found = sum(
        1 for verdict in verdicts if re.search(rf"\b{re.escape(verdict)}\b", haystack, re.I)
    )
    return found, verdicts_found, wanted


#: The agents' role names, as they appear inside justifications ("acknowledged by [ca2,
#: ca3, cax]"). A role name is a fact a justification cites, and one a handler sees in
#: every topic and label.
AGENT_ROLES = ("ca1", "ca2", "ca3", "cax", "mc1", "mc3")
#: Roles whose decisions judge an input; the rest judge another agent's record.
OPERATIONAL_ROLES = ("ca1", "ca2", "ca3", "cax")

#: What counts as a checkable fact inside a justification, tried in order. A single-digit
#: integer is deliberately not one: "9" occurs somewhere in almost any haystack, so finding
#: it says nothing about whether the handler saw *that* 9.
_FACT = re.compile(
    r"(?<!\w)'(?P<quoted>[\w/.-]{2,})'"
    r"|\b(?P<hash>[0-9a-f]{8,})\b"
    r"|\b(?P<file>[\w-]+\.(?:png|svg|json|csv|txt))\b"
    rf"|\b(?P<role>{'|'.join(AGENT_ROLES)})\b"
    # Not part of a name ("sha-256", "example_0"); a unit may follow ("5.049s", "64.7%").
    r"|(?<![\w.-])(?P<number>\d+\.\d+|\d{2,})(?!\.?\d)"
)


def justification_facts(text: str) -> list[str]:
    """The checkable facts a justification cites: figures, hashes, names, files, roles."""
    facts: list[str] = []
    for match in _FACT.finditer(text):
        fact = next(value for value in match.groupdict().values() if value)
        if fact not in facts:
            facts.append(fact)
    return facts


def _visible(fact: str, haystack: str) -> bool:
    """Whether a fact occurs in the haystack as a whole token, not inside a longer one.

    A figure may carry a unit in the haystack as in the justification ("5.049s"), so a
    number is bounded only against further digits.
    """
    tail = r"(?!\.?\d)" if fact[0].isdigit() else r"(?!\w)"
    return re.search(rf"(?<![\w.]){re.escape(fact)}{tail}", haystack) is not None


@dataclass
class FactRecovery:
    """How much of one agent's justifications a callback handler could have seen."""

    decisions: int = 0
    verbatim: int = 0
    facts: int = 0
    facts_visible: int = 0
    #: Justifications citing at least one fact, every one of which the handler saw. For
    #: these, the only thing Strategy A is missing is the rule that combined them.
    all_facts_visible: int = 0


def role_of(path: Path) -> str:
    """The emitting agent's role, from a record's file name (``…__<role>_prov.json``)."""
    return path.name.removesuffix("_prov.json").split("__")[-1]


def fact_recovery(
    explicit: Iterable[tuple[Path, dict[str, Any]]], payloads: list[str]
) -> dict[str, FactRecovery]:
    """Recoverability of the rationale at the level of facts, per emitting agent.

    :func:`unreachable_rationale` asks whether a justification appears *verbatim* anywhere
    the handler looked. That is the right question about prose, and the wrong one to rest a
    claim on alone: a sentence assembled at run time will almost never survive intact, so a
    verbatim zero is close to guaranteed (docs/AUDIT.md [2]). This asks the harder question
    — of the facts each justification cites, how many did the handler see somewhere?

    It over-counts visibility, and is reported as an over-count. A role name in the stream
    is not knowledge that the role acknowledged; a number seen in a payload is not knowledge
    that it was compared with a threshold. What a high figure here establishes is narrower
    and still the point: the inputs to a decision can be observable while the rule that
    combined them is not.
    """
    haystack = "\n".join(payloads)
    recovery: dict[str, FactRecovery] = defaultdict(FactRecovery)
    for path, doc in explicit:
        for activity in (doc.get("activity") or {}).values():
            text = str(activity.get("yp:justification", "")).strip()
            if not text:
                continue
            tally = recovery[role_of(path)]
            tally.decisions += 1
            tally.verbatim += text in haystack
            facts = justification_facts(text)
            seen = sum(_visible(fact, haystack) for fact in facts)
            tally.facts += len(facts)
            tally.facts_visible += seen
            tally.all_facts_visible += bool(facts) and seen == len(facts)
    order = {role: index for index, role in enumerate(AGENT_ROLES)}
    return dict(sorted(recovery.items(), key=lambda item: (order.get(item[0], 99), item[0])))


def entity_identity_collisions(
    explicit: list[dict[str, Any]], callback: list[dict[str, Any]]
) -> int:
    """How many entities Strategy A loses by identifying payloads from their content.

    A handler is handed a payload, never a reference to one, so two distinct entities
    carrying equal values are indistinguishable and collapse into one. Counted against the
    explicit record's *data* entities — its ``prov:Bundle`` descriptor has no analogue in a
    callback reconstruction and is excluded.
    """
    declared = sum(
        1
        for doc in explicit
        for entity in (doc.get("entity") or {}).values()
        if "Bundle" not in json.dumps(entity.get("prov:type", ""))
    )
    observed = sum(len(doc.get("entity") or {}) for doc in callback)
    return max(declared - observed, 0)


def valid_prov_json(documents: list[dict[str, Any]]) -> int | None:
    """How many documents the reference ``prov`` parser accepts. ``None`` if unavailable.

    Judged by an independent implementation rather than by this project's code, for both
    conditions. The old E3 table claimed Strategy A produced no W3C PROV-JSON; that was the
    fourth of its four wrong zeros. A callback reconstruction serialises to conformant
    PROV-JSON perfectly well, and conceding it is free — the claim that matters is about
    what the document can *say*, not about the syntax it says it in.
    """
    try:
        from prov.model import ProvDocument
    except ImportError:  # pragma: no cover - the parser is an optional extra
        return None
    accepted = 0
    for doc in documents:
        try:
            ProvDocument.deserialize(content=json.dumps(doc), format="json")
            accepted += 1
        except Exception:
            continue
    return accepted


def compare(prov_dir: Path, baseline_dir: Path) -> dict[str, Any]:
    """Measure Strategy A against Strategy B over one run of the same agents.

    Two reconstructions of A are reported from the same observation stream: the
    zero-configuration handler any framework gives you, and the same handler with a
    hand-written label-to-type map. The gap between them is what the types cost — a cost
    that belongs to condition A rather than being free.
    """
    named = [
        (path, json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(prov_dir.glob("*_prov.json"))
    ]
    explicit = [doc for _, doc in named]
    events_path = baseline_dir / EVENTS_FILE
    if not events_path.is_file():
        raise SystemExit(
            f"no observation stream at {events_path} — produce one with "
            f"`python run_local.py --baseline`"
        )
    events = read_events(events_path)

    unmapped = CallbackLogger.from_events(events)
    mapped = CallbackLogger.from_events(events, type_map=DEFAULT_TYPE_MAP)
    unmapped_docs = list(unmapped.documents().values())
    mapped_docs = list(mapped.documents().values())

    payloads = mapped.observed_payloads()
    justifications, verdicts, wanted = unreachable_rationale(explicit, payloads)
    conformant_a, conformant_b = (
        valid_prov_json(mapped_docs),
        valid_prov_json(explicit),
    )
    return {
        "A0": _totals(unmapped_docs),
        "A": _totals(mapped_docs),
        "B": _totals(explicit),
        "conformant_A": conformant_a,
        "conformant_B": conformant_b,
        "events": len(events),
        "rationale_searched": len(wanted),
        "rationale_found": justifications,
        "verdicts_found": verdicts,
        "payloads_searched": len(payloads),
        "payload_chars": sum(len(payload) for payload in payloads),
        "facts": {
            role: asdict(tally) for role, tally in fact_recovery(named, payloads).items()
        },
        "entity_collisions": entity_identity_collisions(explicit, mapped_docs),
        "type_rules": len(DEFAULT_TYPE_MAP),
    }


def render(result: dict[str, Any]) -> list[str]:
    """Render the E3 table and the notes a reader needs in order to interpret it."""
    a0, a, b = result["A0"], result["A"], result["B"]

    def row(label: str, key: str) -> str:
        return f"| {label} | {a0.get(key, 0)} | {a.get(key, 0)} | {b.get(key, 0)} |"

    # Conceded openly: both conditions serialise to standard PROV-JSON, judged by the
    # reference parser. Syntax was never the differentiator.
    conformance = (
        f"| Accepted by the reference `prov` parser | {result['conformant_A']} | "
        f"{result['conformant_A']} | {result['conformant_B']} |"
        if result["conformant_B"] is not None
        else "| Accepted by the reference `prov` parser | *parser not installed* | "
        "*parser not installed* | *parser not installed* |"
    )

    collisions = (
        f", with {result['entity_collisions']} distinct entities collapsing into shared "
        f"ones because their values were equal"
        if result["entity_collisions"]
        else ", with no collisions in this run"
    )

    return [
        "## E3 — Strategy A (callbacks) vs Strategy B (explicit instrumentation)",
        "",
        f"Same agents, same input, **same run**. Strategy A is a callback handler fed "
        f"exactly what a framework hands one: {result['events']} step events carrying the "
        f"node name, timings, input and output payloads, and errors "
        f"(`yprov4agenticai.observer.StepEvent`). Both A columns are reconstructed from "
        f"that single recorded stream.",
        "",
        "| Signal | A, zero config | A, with type map | B |",
        "|---|--:|--:|--:|",
        row("Documents emitted", "documents"),
        row("Activities", "activities"),
        row("…of them typed", "typed_activities"),
        row("Entities", "entities"),
        row("…of them typed", "typed_entities"),
        row("…carrying a resolvable pointer (`yp:pid`)", "pointers"),
        row("`used`", "used"),
        row("`wasGeneratedBy`", "wasGeneratedBy"),
        row("`wasInformedBy`", "wasInformedBy"),
        row("`wasDerivedFrom`", "wasDerivedFrom"),
        row("`wasAssociatedWith`", "wasAssociatedWith"),
        row("**Relations, total**", "relations"),
        row("Decisions carrying `yp:verdict`", "verdicts"),
        row("Decisions carrying **`yp:justification`**", "justifications"),
        conformance,
        "",
        "### How to read this",
        "",
        "**A is an upper bound, not a measurement of a particular handler.** The event "
        "stream it reads is built from the declarations the explicit instrumentation made: "
        "the entity each step said it read, the entity each step said it wrote. A real "
        "callback handler is handed a node's whole input payload and must guess which part "
        "mattered — it would over-report `used` and could not tell a load-bearing input "
        "from an incidental one. Conceding A every structural fact B declared is what makes "
        "the last row safe: the claim is not that callbacks recover less structure, it is "
        "that the rationale is unreachable *even at the ceiling*.",
        "",
        f"**Structure is recoverable, and the baseline recovers it.** A sees every step, "
        f"times it, attributes it, and reconstructs {a.get('relations', 0)} relations "
        f"against B's {b.get('relations', 0)}. Any claim that callback instrumentation "
        f"yields nothing is false — three of the four zeros this table used to carry were "
        f"wrong.",
        "",
        f"**Types are not free.** With no configuration A types "
        f"{a0.get('typed_activities', 0)} activities; with {result['type_rules']} "
        f"hand-written label patterns it types {a.get('typed_activities', 0)}. That gap is "
        f"the map, and the map is a per-system artifact someone writes and maintains. It "
        f"works here only because these labels are descriptive. B declares the type at the "
        f"call site and needs no map.",
        "",
        f"**Entity identity is the quiet loss.** A is handed payloads, not references, so "
        f"it mints identity from content: {a.get('entities', 0)} entities against B's "
        f"{b.get('entities', 0)}{collisions}. None are typed, and none carry a resolvable "
        f"pointer — `yp:pid`/`yp:contentHash` has no analogue in a reconstruction that "
        f"never saw where a payload was stored, so the pointers-not-payloads design is not "
        f"available to A at all.",
        "",
        f"**Verdicts are often recoverable; justifications verbatim, never.** Searching "
        f"everything the handler could read — {result['payloads_searched']} strings, "
        f"{result['payload_chars']:,} characters — recovers {result['verdicts_found']} of "
        f"{result['rationale_searched']} verdicts and {result['rationale_found']} of "
        f"{result['rationale_searched']} justifications *verbatim*. The verdicts come back "
        f"only because these agents interpolate them into node labels: a graph whose "
        f"decision node is named `decide` would yield none. The verbatim zero is close to "
        f"guaranteed for any sentence assembled at run time, so it is reported as what it "
        f"is and not rested on. The table below asks the harder question.",
        "",
        *render_facts(result["facts"]),
    ]


def render_facts(facts: dict[str, dict[str, int]]) -> list[str]:
    """The fact-level table (E3b), with operational and meta agents kept apart."""

    def pct(part: int, whole: int) -> str:
        return f"{100 * part / whole:.0f}%" if whole else "—"

    def row(label: str, tally: dict[str, int]) -> str:
        return (
            f"| {label} | {tally['decisions']} | {tally['verbatim']} | {tally['facts']} | "
            f"{tally['facts_visible']} ({pct(tally['facts_visible'], tally['facts'])}) | "
            f"{tally['all_facts_visible']} |"
        )

    def total(roles: Iterable[str]) -> dict[str, int]:
        summed: dict[str, int] = defaultdict(int)
        for role in roles:
            for key, value in facts.get(role, {}).items():
                summed[key] += value
        return dict(summed) or asdict(FactRecovery())

    operational = [role for role in facts if role in OPERATIONAL_ROLES]
    meta = [role for role in facts if role not in OPERATIONAL_ROLES]
    ops, rest = total(operational), total(meta)
    return [
        "### E3b — The rationale, fact by fact",
        "",
        "Each justification is split into the checkable facts it cites — multi-digit "
        "figures, hashes, quoted names, files and agent roles — and each fact is searched "
        "for, as a whole token, in everything the handler could read. This over-counts: a "
        "role name in the stream is not knowledge that the role acknowledged.",
        "",
        "| Agent | Decisions | Verbatim | Facts cited | Facts visible to A | "
        "All facts visible |",
        "|---|--:|--:|--:|--:|--:|",
        *(row(role, facts[role]) for role in operational),
        row("**operational**", ops),
        *(row(role, facts[role]) for role in meta),
        row("**meta**", rest),
        "",
        f"**The inputs to a decision can be observable; the rule that combined them is "
        f"not.** Across the {ops['decisions']} operational decisions, "
        f"{pct(ops['facts_visible'], ops['facts'])} of the facts the justifications cite "
        f"were somewhere in the callback stream, and for {ops['all_facts_visible']} of "
        f"{ops['decisions']} every cited fact was. What A lacks for those is which facts "
        f"were combined, against what threshold, to reach the verdict — the decision rule, "
        f"which was never a value the step passed anywhere.",
        "",
        f"**The {rest['decisions']} meta-agent justifications are restatements**, and are "
        f"reported apart for that reason: they describe structure A already reconstructs "
        f"in full ({pct(rest['facts_visible'], rest['facts'])} of their facts visible). "
        f"Counting them would inflate any headline built on the operational agents.",
        "",
    ]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.baseline",
        description="Compare callback-level instrumentation with explicit instrumentation.",
    )
    parser.add_argument("--prov-dir", default="out", help="Strategy B's emitted provenance")
    parser.add_argument(
        "--baseline-dir",
        default="out/baseline",
        help="Strategy A's reconstruction, written by run_local.py --baseline",
    )
    parser.add_argument("--markdown", help="also write the table to this file")
    args = parser.parse_args(argv)

    prov_dir, baseline_dir = Path(args.prov_dir), Path(args.baseline_dir)
    for path in (prov_dir, baseline_dir):
        if not path.is_dir():
            raise SystemExit(
                f"no such directory: {path} — generate both with "
                f"`python run_local.py --baseline`"
            )

    lines = render(compare(prov_dir, baseline_dir))
    print("\n".join(lines))
    if args.markdown:
        Path(args.markdown).write_text("\n".join(lines) + "\n", encoding="utf-8")
        print(f"[baseline] wrote {args.markdown}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
