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
from typing import Any

from yprov4agenticai import LightweightStore, ProvTracker, ptr_kwargs

from ..base_agent import StreamingAgent, _merge_payload, stream_document
from ..envelope import ProvEventType, ProvRecordEnvelope
from ..net_bus import NetworkBus
from ..util import OUT_DIR, now_iso, prov_counts, write_prov

# consumer roles whose use of a record must precede persistence
PERSIST_AFTER = [
    r.strip() for r in os.environ.get("PERSIST_AFTER", "cax,ca2,ca3").split(",") if r.strip()
]
PERSIST_TIMEOUT_S = float(os.environ.get("PERSIST_TIMEOUT_S", "25"))


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
        # Live timeout guards, held so the event loop keeps a strong reference. A task
        # referenced only by `create_task`'s return value can be garbage-collected before
        # it runs, which would drop the safety net that is supposed to make a record
        # unloseable. See todo.md H4 on why this guard races the consumers at all.
        self._guards: set[asyncio.Task[None]] = set()

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
            guard = asyncio.create_task(self._timeout_guard(sid))
            self._guards.add(guard)
            guard.add_done_callback(self._guards.discard)

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
            await self._persist(sid, reason="all expected consumers acknowledged")

    async def _timeout_guard(self, sid: str) -> None:
        """Safety net: never silently drop a record because a consumer died."""
        await asyncio.sleep(PERSIST_TIMEOUT_S)
        if sid in self._done or sid not in self._pending:
            return
        missing = sorted(set(PERSIST_AFTER) - self._acks.get(sid, set()))
        await self._persist(
            sid, reason=f"timeout after {PERSIST_TIMEOUT_S}s; no acknowledgement from {missing}"
        )

    # ── the actual persist + reflexive PROV ────────────────────────────────────
    async def _persist(self, sid: str, *, reason: str) -> None:
        if sid in self._done:
            return
        self._done.add(sid)
        doc = self._pending.pop(sid, {})
        acked = sorted(self._acks.get(sid, set()))
        loop = asyncio.get_event_loop()
        prov, log = await loop.run_in_executor(None, self._do_persist, sid, doc, acked, reason)
        print(f"[{self.role}] {log}", flush=True)
        await stream_document(
            self.bus,
            f"agent.lifecycle.{self.role}.{sid}",
            session_id=f"{sid}::{self.role}",
            producer_id=self.role,
            doc=prov,
        )

    def _do_persist(
        self, sid: str, doc: dict[str, Any], acked: list, reason: str
    ) -> tuple[dict[str, Any], str]:
        c = prov_counts(doc)
        payload = json.dumps(doc, ensure_ascii=False, sort_keys=True)

        # S3 Act — write through the store's own API (backend, NOT the bus)
        ptr = self.store.put(payload, kind="provenance-record")

        # S4 Observe — round-trip integrity read (this is what makes the verdict real)
        try:
            self.store.resolve(ptr["yp:pid"], verify=True)
            verdict, integrity = "ACCEPT", "round-trip read verified; sha-256 matched"
        except Exception as exc:
            verdict, integrity = "ESCALATE", f"integrity check FAILED: {exc}"
        self.store.save()  # refresh the manifest on disk

        # ── reflexive PROV: CA1's own 6-step lifecycle ─────────────────────────
        t = ProvTracker(sid=sid, agent_label="StorageAgent")
        t0 = now_iso()
        task = t.register_entity(
            "TaskEntity", f"provenance record '{sid}' held for persistence"
        )
        t.record_activity(
            "ReceiveTaskActivity",
            "CA1: receive record from bus",
            t0,
            now_iso(),
            out_eid=task,
            verbose=False,
        )
        t.record_activity(
            "PlanningActivity",
            "CA1: await consumers, plan store path",
            now_iso(),
            now_iso(),
            in_eid=task,
            extra_attrs={
                "yp:persist_policy": "after-consume",
                "yp:required_consumers": ",".join(PERSIST_AFTER),
                "yp:acknowledged_by": ",".join(acked) or "(none)",
            },
            verbose=False,
        )
        stored = t.register_entity(
            "ToolResultEntity", f"stored provenance record for {sid}", **ptr_kwargs(ptr)
        )
        t.record_activity(
            "PersistActivity",
            "CA1: write to store (backend API)",
            now_iso(),
            now_iso(),
            in_eid=task,
            out_eid=stored,
            extra_attrs={
                "yp:store": "LightweightStore",
                "yp:size_bytes": len(payload.encode("utf-8")),
            },
            verbose=False,
        )
        t.record_activity(
            "ObservationActivity",
            "CA1: verify round-trip integrity",
            now_iso(),
            now_iso(),
            in_eid=stored,
            verbose=False,
        )
        t.record_activity(
            "DecisionActivity",
            f"CA1: {verdict.lower()} the stored record",
            now_iso(),
            now_iso(),
            in_eid=stored,
            justification=(
                f"Persisted after consumption ({reason}); acknowledged by "
                f"[{', '.join(acked) or 'none'}]. Record carries "
                f"{c['activities']} activities / "
                f"{c['entities']} entities / {c['relations']} relations, {len(payload)} bytes. "
                f"Integrity: {integrity}. Verdict {verdict}."
            ),
            extra_attrs={"yp:verdict": verdict},
            verbose=False,
        )
        receipt = t.register_entity(
            "ResponseEntity",
            f"storage receipt {ptr['yp:pid']} ({ptr['yp:contentHash'][:19]}...)",
        )
        t.record_activity(
            "ResponseActivity",
            "CA1: emit storage receipt",
            now_iso(),
            now_iso(),
            in_eid=stored,
            out_eid=receipt,
            verbose=False,
        )

        prov = t.to_prov_json()
        write_prov(self.role, sid, prov)
        return prov, (
            f"persisted '{sid}' -> {ptr['yp:pid']} [{verdict}] after {acked or 'timeout'}"
        )
