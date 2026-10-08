"""
ca1_storage.py — CA1: the StorageAgent (operational agent, deterministic).

Persists provenance to the store — but **only after a consumer has actually used the
record**. That ordering is the point, and it implements two corrections Sandro made on
2026-10-05:

  1. "The store should not really be on the bus" — CA1 writes to the store through the
     store's own API from its **backend** (`LightweightStore`), not by publishing to the
     bus. The bus carries the *receipt*, not the payload.
  2. Persist happens *after* consumption, not before — otherwise we archive records no
     one ever looked at, and lose the ordering guarantee an audit trail depends on.

How the ordering is achieved (no extra coordination service needed):

    producer.>              → CA1 buffers the original document, marks it PENDING
    agent.lifecycle.<role>.*→ a consumer finished with that session = one acknowledgement
    (quorum reached)        → CA1 persists: sha256 → yp:pid → store.put() → receipt

`PERSIST_AFTER` (env) names the consumer roles whose acknowledgement is required.
A `PERSIST_TIMEOUT_S` safety net persists anyway if a consumer never reports, so a
crashed agent cannot silently lose a record — the receipt records which case applied.

Decide★ verdicts: ACCEPT (round-trip integrity verified) / ESCALATE (I/O or hash mismatch).
"""

from __future__ import annotations

import asyncio
import json
import os
from functools import partial
from typing import Any

from yprov4agenticai import LightweightStore, ProvTracker

from ..base_agent import StreamingAgent, _merge_payload, stream_document
from ..envelope import ProvEventType, ProvRecordEnvelope
from ..net_bus import NetworkBus
from ..util import OUT_DIR, prov_counts, write_prov

# consumer roles whose use of a record must precede persistence
PERSIST_AFTER = [
    r.strip() for r in os.environ.get("PERSIST_AFTER", "cax,ca2,ca3").split(",") if r.strip()
]
PERSIST_TIMEOUT_S = float(os.environ.get("PERSIST_TIMEOUT_S", "25"))

#: Why a record was archived. Recorded on the decision so a reader can distinguish the
#: advertised ordering from the safety net without parsing the justification prose.
PERSIST_TRIGGER_QUORUM = "quorum"
PERSIST_TRIGGER_TIMEOUT = "timeout"


class CA1Storage(StreamingAgent):
    """Persist-after-consume storage agent. Store lives behind it, not on the bus."""

    input_subject = "producer.>"
    output_prefix = "agent.lifecycle"

    def __init__(self, bus: NetworkBus) -> None:
        super().__init__(role="ca1", bus=bus)
        self.store = LightweightStore(root=str(OUT_DIR / "store"))
        # session_id -> reassembled original producer document, awaiting consumers
        self._pending: dict[str, dict[str, Any]] = {}
        # session_id -> set of consumer roles that have acknowledged
        self._acks: dict[str, set[str]] = {}
        # sessions already persisted (never persist twice)
        self._done: set[str] = set()
        # session_id -> its live timeout guard. Held for two reasons: a task referenced
        # only by `create_task`'s return value can be garbage-collected before it runs,
        # which would silently remove the safety net; and the guard has to be cancellable
        # once quorum is reached, or it keeps racing a decision that has already been made.
        self._guards: dict[str, asyncio.Task[None]] = {}

    # ── startup: two subscriptions, not one ────────────────────────────────────
    async def start(self) -> None:
        await self.bus.subscribe("producer.>", self._on_producer)
        await self.bus.subscribe("agent.lifecycle.>", self._on_consumer_ack)
        ready = ProvRecordEnvelope(
            session_id="_system",
            sequence_no=0,
            producer_id=self.role,
            event_type=ProvEventType.HEARTBEAT.value,
            payload={"role": self.role},
        )
        await self.bus.publish(f"system.ready.{self.role}", ready)
        print(
            f"[{self.role}] ready — persisting after {PERSIST_AFTER} "
            f"(timeout {PERSIST_TIMEOUT_S}s)",
            flush=True,
        )

    # ── inbound: the original record to hold ───────────────────────────────────
    async def _on_producer(self, subject: str, env: ProvRecordEnvelope) -> None:
        sid = env.session_id
        if env.event_type == ProvEventType.STREAM_START.value:
            self._pending[sid] = {}
            _merge_payload(self._pending[sid], env.payload)
            return
        buf = self._pending.setdefault(sid, {})
        if env.event_type == ProvEventType.RECORD_CHUNK.value:
            _merge_payload(buf, env.payload)
        elif env.event_type == ProvEventType.STREAM_END.value:
            print(
                f"[{self.role}] holding '{sid}' — awaiting consumers "
                f"{sorted(set(PERSIST_AFTER) - self._acks.get(sid, set()))}",
                flush=True,
            )
            self._guards[sid] = asyncio.create_task(self._timeout_guard(sid))

    # ── inbound: a consumer finished → one acknowledgement ─────────────────────
    async def _on_consumer_ack(self, subject: str, env: ProvRecordEnvelope) -> None:
        if env.event_type != ProvEventType.STREAM_END.value:
            return
        # subject looks like: agent.lifecycle.<role>.<session>
        parts = subject.split(".")
        if len(parts) < 4:
            return
        role, sid = parts[2], parts[3]
        if role == self.role:  # never acknowledge ourselves
            return
        if sid in self._done or sid not in self._pending:
            return
        self._acks.setdefault(sid, set()).add(role)
        waiting = set(PERSIST_AFTER) - self._acks[sid]
        print(
            f"[{self.role}] ack from '{role}' for '{sid}' "
            f"({len(self._acks[sid])}/{len(PERSIST_AFTER)})"
            + (f", waiting on {sorted(waiting)}" if waiting else ", quorum reached"),
            flush=True,
        )
        if not waiting:
            # Stand the guard down before persisting. It is a fallback for a consumer that
            # never reports, not a competitor to one that just did; leaving it running
            # meant the decision could still be taken by whichever fired first.
            self._cancel_guard(sid)
            await self._persist(
                sid,
                trigger=PERSIST_TRIGGER_QUORUM,
                reason="all expected consumers acknowledged",
            )

    def _cancel_guard(self, sid: str) -> None:
        guard = self._guards.pop(sid, None)
        if guard is not None and not guard.done():
            guard.cancel()

    async def _timeout_guard(self, sid: str) -> None:
        """Safety net: never silently drop a record because a consumer died."""
        await asyncio.sleep(PERSIST_TIMEOUT_S)
        self._guards.pop(sid, None)
        if sid in self._done or sid not in self._pending:
            return
        missing = sorted(set(PERSIST_AFTER) - self._acks.get(sid, set()))
        await self._persist(
            sid,
            trigger=PERSIST_TRIGGER_TIMEOUT,
            reason=f"timeout after {PERSIST_TIMEOUT_S}s; no acknowledgement from {missing}",
        )

    # ── the actual persist + reflexive PROV ────────────────────────────────────
    async def _persist(self, sid: str, *, trigger: str, reason: str) -> None:
        if sid in self._done:
            return
        self._done.add(sid)
        doc = self._pending.pop(sid, {})
        acked = sorted(self._acks.get(sid, set()))
        # Session bookkeeping is finished with; holding it would grow without bound.
        self._acks.pop(sid, None)
        loop = asyncio.get_event_loop()
        prov, log = await loop.run_in_executor(
            None, self._do_persist, sid, doc, acked, trigger, reason
        )
        print(f"[{self.role}] {log}", flush=True)
        await stream_document(
            self.bus,
            f"agent.lifecycle.{self.role}.{sid}",
            session_id=f"{sid}::{self.role}",
            producer_id=self.role,
            doc=prov,
        )

    def _verify(self, pid: str) -> tuple[bool, str]:
        """Read the payload back out of the store and re-hash it.

        A real round trip: a rooted store reads the file rather than handing back the copy
        ``put`` just cached, so this check can actually fail — which is the only reason an
        ACCEPT verdict means anything.
        """
        try:
            self.store.resolve(pid, verify=True)
        except Exception as exc:
            return False, f"integrity check FAILED: {exc}"
        return True, "round-trip read verified; sha-256 matched"

    @staticmethod
    def _justify(
        verdict: str,
        trigger: str,
        reason: str,
        acked: list[str],
        missing: list[str],
        counts: dict[str, int],
        payload_chars: int,
        integrity: str,
    ) -> str:
        """State which ordering actually held, and what the integrity check established.

        Persisting at quorum is the property the architecture advertises: the record was
        archived only after its consumers had used it, which is the ordering an audit
        trail depends on. A timeout persist keeps the record safe but forfeits that
        ordering, and reporting it as a plain ACCEPT made the two indistinguishable
        without reading this prose.
        """
        if trigger == PERSIST_TRIGGER_QUORUM:
            ordering = (
                f"Persisted at quorum: {reason}; acknowledged by [{', '.join(acked)}], "
                f"the full set required."
            )
        else:
            ordering = (
                f"Persisted WITHOUT confirmed consumption: {reason}. Acknowledged by "
                f"[{', '.join(acked) or 'none'}]; still waiting on "
                f"[{', '.join(missing)}] when the record was archived anyway to avoid "
                f"losing it. The persist-after-consume ordering was not satisfied, so "
                f"this record is archived but its consumption is unconfirmed."
            )
        return (
            f"{ordering} Record carries {counts['activities']} activities / "
            f"{counts['entities']} entities / {counts['relations']} relations, "
            f"{payload_chars} bytes. Integrity: {integrity}. Verdict {verdict}."
        )

    def _do_persist(
        self, sid: str, doc: dict[str, Any], acked: list, trigger: str, reason: str
    ) -> tuple[dict[str, Any], str]:
        tracker = ProvTracker(
            sid=sid,
            agent_label="StorageAgent",
            sink=partial(write_prov, self.role, sid),
            verbose=False,
        )

        # S1 Receive — what is being held, and how big it is.
        with tracker.activity("ReceiveTaskActivity", "CA1: receive record from bus") as prov:
            counts = prov_counts(doc)
            task = prov.produces(
                "TaskEntity", f"provenance record '{sid}' held for persistence"
            )

        # S2 Plan — who was required to consume this, and who actually did.
        with tracker.activity(
            "PlanningActivity", "CA1: await consumers, plan store path"
        ) as prov:
            prov.consumes(task)
            missing = sorted(set(PERSIST_AFTER) - set(acked))
            prov.note(
                **{
                    "yp:persist_policy": "after-consume",
                    "yp:required_consumers": ",".join(PERSIST_AFTER),
                    "yp:acknowledged_by": ",".join(acked) or "(none)",
                }
            )

        # S3 Act — write through the store's own API (backend, NOT the bus).
        with tracker.activity("PersistActivity", "CA1: write to store (backend API)") as prov:
            prov.consumes(task)
            payload = json.dumps(doc, ensure_ascii=False, sort_keys=True)
            pointer = self.store.put(payload, kind="provenance-record")
            stored = prov.produces(
                "ToolResultEntity",
                f"stored provenance record for {sid}",
                pid=pointer["yp:pid"],
                content_hash=pointer["yp:contentHash"],
                derived_from=task,
            )
            prov.note(
                **{
                    "yp:store": "LightweightStore",
                    "yp:size_bytes": len(payload.encode("utf-8")),
                }
            )

        # S4 Observe — read it back and re-hash it. This block touches the disk twice,
        # and the record now shows what that costs.
        with tracker.activity(
            "ObservationActivity", "CA1: verify round-trip integrity"
        ) as prov:
            prov.consumes(stored)
            integrity_ok, integrity = self._verify(pointer["yp:pid"])
            self.store.save()  # refresh the derived manifest index

        # S5 Decide — two independent conditions, so the verdict names which one failed.
        if not integrity_ok:
            verdict, escalation = "ESCALATE", "integrity"
        elif trigger != PERSIST_TRIGGER_QUORUM:
            verdict, escalation = "ESCALATE", "consumption-unconfirmed"
        else:
            verdict, escalation = "ACCEPT", ""
        tracker.decide(
            verdict,
            self._justify(
                verdict, trigger, reason, acked, missing, counts, len(payload), integrity
            ),
            label=f"CA1: {verdict.lower()} the stored record",
            consumed=stored,
            attributes={
                "yp:persist_trigger": trigger,
                "yp:consumers_required": len(PERSIST_AFTER),
                "yp:consumers_acknowledged": len(acked),
                **({"yp:escalation_reason": escalation} if escalation else {}),
            },
        )

        # S6 Respond — the receipt goes on the bus; the payload stays in the store.
        with tracker.activity("ResponseActivity", "CA1: emit storage receipt") as prov:
            prov.consumes(stored)
            prov.produces(
                "ResponseEntity",
                f"storage receipt {pointer['yp:pid']} ({pointer['yp:contentHash'][:19]}...)",
                derived_from=stored,
            )

        return tracker.emit(), (
            f"persisted '{sid}' -> {pointer['yp:pid']} [{verdict}] "
            f"via {trigger}, acked by {acked or '(none)'}"
        )
