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
import json
import os
import re
from collections.abc import Iterable
from pathlib import Path

from .base_agent import stream_document
from .envelope import ProvRecordEnvelope
from .net_bus import Bus, NetworkBus

DATA_DIR = os.environ.get("DATA_DIR", "/data")
EXPECTED = [
    r.strip()
    for r in os.environ.get("EXPECTED_AGENTS", "ca1,cax,ca2,ca3,mc1,mc3").split(",")
    if r.strip()
]
STREAM_DELAY_MS = float(os.environ.get("STREAM_DELAY_MS", "150"))


def _find_docs() -> list[Path]:
    return sorted(Path(DATA_DIR).rglob("*.json"))


#: Characters a session may contain. A session becomes a token of a NATS subject
#: (``producer.yprov4ml.<session>``), where ``.`` separates tokens and ``*``/``>`` are
#: wildcards, so anything else is replaced rather than allowed to split or widen a subject.
_SESSION_UNSAFE = re.compile(r"[^A-Za-z0-9_-]")


def sessions_for(paths: Iterable[Path], root: Path) -> list[tuple[Path, str]]:
    """Name each input document's session, uniquely. Used by every replay path.

    A document's session is its folder's name, because yProv4ML writes one run into one
    folder — unless that folder holds more than one document. yProv4ML writes one file per
    rank in a multi-GPU run (``…_GR0``, ``…_GR1``), and under the folder rule those shared a
    session: downstream agents overwrote one another's records, and the storage agent
    persisted the first and silently ignored the rest. Such documents are named
    ``<folder>__<file stem>``; a document directly under ``root`` is named by its stem.

    The rule used to be written twice, slightly differently, in the containerised producer
    and in ``run_local.py``. It is written once, here.

    Raises
    ------
    ValueError
        Two documents would still share a session after sanitising — which would be the
        silent overwrite this function exists to prevent, so it is refused instead.
    """
    paths = sorted(paths)
    per_folder: dict[Path, int] = {}
    for path in paths:
        per_folder[path.parent] = per_folder.get(path.parent, 0) + 1

    named: list[tuple[Path, str]] = []
    for path in paths:
        if path.parent == root:
            raw = path.stem
        elif per_folder[path.parent] == 1:
            raw = path.parent.name
        else:
            raw = f"{path.parent.name}__{path.stem}"
        named.append((path, _SESSION_UNSAFE.sub("_", raw)))

    seen: dict[str, Path] = {}
    for path, session in named:
        if session in seen:
            raise ValueError(
                f"two input documents would share session {session!r}: "
                f"{seen[session]} and {path}"
            )
        seen[session] = path
    return named


async def _wait_for_agents(bus: Bus, timeout: float = 30.0) -> None:
    seen: set[str] = set()
    want = set(EXPECTED)
    done = asyncio.Event()

    async def on_ready(subject: str, _env: ProvRecordEnvelope) -> None:
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

    for path, session in sessions_for(docs, Path(DATA_DIR)):
        try:
            doc = json.loads(path.read_text(encoding="utf-8"))
        except Exception as exc:
            print(f"[producer] skip {path.name}: {exc}", flush=True)
            continue
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
