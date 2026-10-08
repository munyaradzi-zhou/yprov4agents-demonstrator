"""
net_bus.py — NetworkBus: the streaming provenance bus over NATS.

This is the one load-bearing new component of the container system. It gives every
agent the same "publish to a topic / subscribe to a topic" surface as the in-process
bus, but delivered over the network by a NATS broker — which is what lets the agents
live in separate containers (and separate machines).

The envelope (`ProvRecordEnvelope`) already knows how to serialise itself, so this
adapter is thin: publish = send envelope bytes on a subject; subscribe = decode
incoming bytes back into an envelope and hand it to the agent's handler.

Topics use NATS subject syntax directly:
    producer.yprov4ml.<session>     a producer streaming a yProv4ML document
    agent.lifecycle.<role>.<session>  an operational agent's own PROV
    governance.<role>.<session>     a meta agent's governance verdict
    system.ready.<role>             a readiness ping (startup handshake)
NATS wildcards: `*` = one token, `>` = the rest. e.g. `producer.>`, `agent.lifecycle.>`.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from .envelope import ProvRecordEnvelope

# `nats-py` is imported lazily inside connect(): the local (no-broker) runner in
# run_local.py substitutes its own bus, so the demonstrator must be importable on a
# machine that has never installed a NATS client.
NATSClient = Any

# A handler receives (subject, envelope) and may be async.
Handler = Callable[[str, ProvRecordEnvelope], Awaitable[None]]


class NetworkBus:
    """Broker-backed pub/sub with the same shape as the in-process ProvenanceBus."""

    def __init__(self, url: str = "nats://localhost:4222", name: str = "yprov-agent"):
        self.url = url
        self.name = name
        self._nc: Any = None

    async def connect(self, *, retries: int = 30, delay: float = 1.0) -> None:
        """Connect to the broker, retrying while it comes up (compose starts them together)."""
        import nats  # lazy: only the broker-backed path needs it

        last: Exception | None = None
        for attempt in range(1, retries + 1):
            try:
                self._nc = await nats.connect(self.url, name=self.name)
                print(f"[bus] connected to NATS at {self.url}", flush=True)
                return
            except Exception as exc:
                last = exc
                print(f"[bus] NATS not ready ({attempt}/{retries}): {exc}", flush=True)
                await asyncio.sleep(delay)
        raise RuntimeError(f"could not connect to NATS at {self.url}: {last}")

    async def publish(self, subject: str, envelope: ProvRecordEnvelope) -> None:
        assert self._nc is not None, "connect() first"
        await self._nc.publish(subject, envelope.to_bytes())
        await self._nc.flush()

    async def subscribe(self, subject: str, handler: Handler) -> None:
        """Subscribe `handler(subject, envelope)` to a NATS subject pattern."""
        assert self._nc is not None, "connect() first"

        async def _cb(msg: Any) -> None:
            try:
                env = ProvRecordEnvelope.from_bytes(msg.data)
            except Exception as exc:
                print(f"[bus] dropped un-decodable message on {msg.subject}: {exc}", flush=True)
                return
            await handler(msg.subject, env)

        await self._nc.subscribe(subject, cb=_cb)
        print(f"[bus] subscribed to '{subject}'", flush=True)

    async def close(self) -> None:
        if self._nc is not None:
            await self._nc.drain()
