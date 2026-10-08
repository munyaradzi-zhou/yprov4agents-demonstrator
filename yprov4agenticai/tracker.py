"""
tracker.py — ProvTracker: accumulate W3C PROV records, serialise to PROV-JSON
=============================================================================
The core of yProv4AgenticAI. A framework-agnostic, dependency-free buffer that an
agent drives explicitly (Strategy B): ``register_entity`` names the typed entity a
step produced, ``record_activity`` names the typed ``yp:`` activity with its lineage,
optional energy delta, optional confidence, the decision ``justification``, and any
extra attributes (e.g. prompt provenance). ``to_prov_json`` emits a standard W3C
PROV-JSON document.

No external PROV library is required — the document is built directly. Granularity
(DATA vs METADATA), persistence label, and the agent label are per-instance config
that round-trips through ``to_state``/``from_state`` so the tracker can be carried
inside an agent's serialisable state (e.g. a LangGraph ``State`` dict).
"""

from __future__ import annotations

import datetime
from collections.abc import Callable

YP_NAMESPACE = "http://hpci-lab.org/yProv4Agents#"


def _library_version() -> str:
    """Version of the library that wrote a record, stamped into its bundle descriptor.

    Read from installed distribution metadata so it cannot drift from pyproject.toml.
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
    persistence  : free label recorded in the self-documenting meta block.
    agent_label  : prov:label for the single (operational) agent node.
    principal    : optional {"label": str, "type": qname} — the agent or person on
                   whose authority the operational agent acted. When set, emits a
                   second prov:Agent node and one prov:actedOnBehalfOf edge
                   (harmonised from yProv4WFs; standard PROV relation, no new
                   predicate). Default None => no principal node/edge is emitted
                   (the default output stays byte-identical).
    """

    YP = YP_NAMESPACE

    def __init__(
        self,
        sid: str,
        granularity: str = "DATA",
        persistence: str = "PERSISTENT",
        agent_label: str = "NL_Summarizer",
        principal: dict | None = None,
    ) -> None:
        assert granularity in ("DATA", "METADATA"), f"bad granularity={granularity!r}"
        self.sid = sid
        self.granularity = granularity
        self.persistence = persistence
        self.agent_label = agent_label
        # Optional delegation: the principal the operational agent acted for.
        # None (default) => no principal agent node and no prov:actedOnBehalfOf
        # edge, so to_prov_json is byte-identical to the output without a principal.
        self.principal = principal
        self.acts: list = []
        self.ents: dict = {}  # eid -> {t: kind, v: value, [pid, hash]}
        self._ent_order: list = []
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
    ) -> str:
        # pid / content_hash are OPTIONAL (lightweight-prov, pointers-not-payloads):
        # when the heavy payload lives in a data store, the entity carries only
        # yp:pid (pointer) + yp:contentHash (sha-256), not the payload. Omitting
        # them (the default) leaves the emitted node byte-identical to before.
        eid = f"yp:{kind}_{self.sid}{suffix}"
        # METADATA mode: skip entity storage entirely. The ID is still returned
        # so callers' downstream string references don't break (filtered at
        # serialisation time — see to_prov_json).
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
        verbose: bool = True,
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
        if not verbose:
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

    # ── serialise/restore (the tracker can ride inside an agent State dict) ────
    def to_state(self) -> dict:
        return {
            "acts": self.acts,
            "ents": self.ents,
            "eo": self._ent_order,
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
        t.mem_chain = list(d.get("mem_chain", []))  # backward-compatible
        t._mem_ids = set(t.mem_chain)  # rebuild O(1) dedup mirror
        t.used_mem = [tuple(x) for x in d.get("used_mem", [])]
        return t

    # ── builds W3C PROV-JSON document ─────────────────────────────────────────
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
            doc["wasDerivedFrom"] = {
                f"_:d{i}": {
                    "prov:generatedEntity": self._ent_order[i],
                    "prov:usedEntity": self._ent_order[i - 1],
                }
                for i in range(1, len(self._ent_order))
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
            if i > 0:
                wib[f"_:i{i}"] = {
                    "prov:informed": aid,
                    "prov:informant": f"yp:{self.acts[i - 1]['id']}",
                }

        # Memory used-edges (optional): each LLM step used the MemoryStateEntity of
        # its turn. Empty used_mem => nothing added (default output unchanged).
        if self.granularity == "DATA":
            for j, (aid, meid) in enumerate(self.used_mem):
                used[f"_:um{j}"] = {"prov:activity": aid, "prov:entity": meid}

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
