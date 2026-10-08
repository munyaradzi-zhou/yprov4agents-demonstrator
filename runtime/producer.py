"""
producer.py — replays REAL yProv4ML PROV-JSON records onto the bus.

This answers "how do we simulate yProv4ML pushing to the bus?": we don't fake it —
we take the real records yProv4ML already wrote to disk (example_0, example_2) and
stream each one onto the bus as a chunked provenance stream, exactly as a live
producer would. Point DATA_DIR at a folder of `.json` PROV documents to replay them.

It first waits for the agents to announce readiness (NATS core pub/sub is not
persistent — subscribers must be up before we publish), then streams each document.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
from pathlib import Path

from .base_agent import stream_document
from .envelope import ProvRecordEnvelope
from .net_bus import NetworkBus

DATA_DIR = os.environ.get("DATA_DIR", "/data")
EXPECTED = [
    r.strip()
    for r in os.environ.get("EXPECTED_AGENTS", "ca1,cax,ca2,ca3,mc1,mc3").split(",")
    if r.strip()
]
STREAM_DELAY_MS = float(os.environ.get("STREAM_DELAY_MS", "150"))


def _find_docs() -> list[Path]:
    files = sorted(glob.glob(os.path.join(DATA_DIR, "**", "*.json"), recursive=True))
    return [Path(f) for f in files]


def _session_for(path: Path) -> str:
    # prefer the parent folder name (example_0), else the file stem
    parent = path.parent.name
    return parent if parent and parent not in (".", "data") else path.stem


async def _wait_for_agents(bus: NetworkBus, timeout: float = 30.0) -> None:
    seen: set[str] = set()
    want = set(EXPECTED)
    done = asyncio.Event()

    async def on_ready(subject: str, env: ProvRecordEnvelope) -> None:
        role = subject.split(".")[-1]
        if role not in seen:
            seen.add(role)
            print(f"[producer] agent ready: {role}  ({len(seen)}/{len(want)})", flush=True)
        if want.issubset(seen):
            done.set()

    await bus.subscribe("system.ready.>", on_ready)
    try:
        await asyncio.wait_for(done.wait(), timeout=timeout)
        print("[producer] all expected agents ready", flush=True)
    except TimeoutError:
        print(f"[producer] timeout waiting for {want - seen}; streaming anyway", flush=True)


async def main() -> None:
    bus = NetworkBus(os.environ.get("NATS_URL", "nats://broker:4222"), name="producer")
    await bus.connect()
    await _wait_for_agents(bus)

    docs = _find_docs()
    if not docs:
        print(f"[producer] no .json documents under {DATA_DIR} — nothing to replay", flush=True)
        return
    print(f"[producer] replaying {len(docs)} yProv4ML document(s) from {DATA_DIR}", flush=True)

    for path in docs:
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[producer] skip {path.name}: {exc}", flush=True)
            continue
        session = _session_for(path)
        subject = f"producer.yprov4ml.{session}"
        n = await stream_document(
            bus,
            subject,
            session_id=session,
            producer_id="yprov4ml-replay",
            doc=doc,
            delay_ms=STREAM_DELAY_MS,
        )
        print(f"[producer] streamed {path.name} as '{session}' ({n} envelopes)", flush=True)
        await asyncio.sleep(0.5)

    # give the agents a moment to finish reacting, then leave the container idling
    await asyncio.sleep(3.0)
    print("[producer] done. (Agents keep running; Ctrl-C / compose down to stop.)", flush=True)
    while True:
        await asyncio.sleep(3600)


if __name__ == "__main__":
    asyncio.run(main())
