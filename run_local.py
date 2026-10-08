"""
run_local.py — run the whole demonstrator in ONE process, with no Docker and no broker.

Same agents, same instrumentation, same emitted PROV as the containerised run; the only
thing swapped out is the transport. `LocalBus` implements the exact surface the agents
use (`connect` / `publish` / `subscribe` / `close`) with in-process delivery and NATS
subject-wildcard matching, so no agent code changes.

Use it when you want the demo in five seconds (reviewing, debugging, a laptop with no
Docker); use `deploy/docker-compose.yml` when you want to show the real distributed
topology — six containers around a NATS broker.

    python run_local.py                      # all six agents + producer
    python run_local.py --out-dir out        # where artifacts land (default: ./out)
    python run_local.py --agents cax,mc1     # a subset
"""
from __future__ import annotations

import argparse
import asyncio
import fnmatch
import os
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
if str(HERE) not in sys.path:
    sys.path.insert(0, str(HERE))

# Windows consoles default to cp1252, which cannot encode the box-drawing and arrow
# characters the agents log. Never let a print() kill a run.
for _stream in (sys.stdout, sys.stderr):
    try:
        _stream.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass


# ── a NATS-compatible in-process bus ───────────────────────────────────────────
def subject_matches(pattern: str, subject: str) -> bool:
    """NATS subject matching: '*' = exactly one token, '>' = one or more trailing tokens."""
    p, s = pattern.split("."), subject.split(".")
    for i, tok in enumerate(p):
        if tok == ">":
            return len(s) > i
        if i >= len(s):
            return False
        if tok != "*" and tok != s[i]:
            return False
    return len(p) == len(s)


class LocalBus:
    """Drop-in replacement for NetworkBus with in-process delivery."""

    def __init__(self, url: str = "local://", name: str = "local"):
        self.url, self.name = url, name
        self._subs: list = []

    async def connect(self, **_kw) -> None:
        return None

    async def subscribe(self, subject: str, handler) -> None:
        self._subs.append((subject, handler))

    async def publish(self, subject: str, envelope) -> None:
        # deliver to every matching subscriber; copy the list so handlers may subscribe
        for pattern, handler in list(self._subs):
            if subject_matches(pattern, subject):
                await handler(subject, envelope)

    async def close(self) -> None:
        return None


async def main() -> None:
    ap = argparse.ArgumentParser(description="Run the demonstrator in one process.")
    ap.add_argument("--out-dir", default="out", help="where artifacts land (default: ./out)")
    ap.add_argument("--data-dir", default="data/yprov4ml", help="yProv4ML records to replay")
    ap.add_argument("--agents", default="ca1,cax,ca2,ca3,mc1,mc3",
                    help="comma-separated roles to start")
    ap.add_argument("--persist-timeout", default="30",
                    help="CA1 seconds to wait for consumer acks (default: 30)")
    args = ap.parse_args()

    # these are read at import time by the agent modules, so set them first
    os.environ["OUT_DIR"] = str(Path(args.out_dir).resolve())
    os.environ["DATA_DIR"] = str(Path(args.data_dir).resolve())
    os.environ.setdefault("PERSIST_AFTER", "cax,ca2,ca3")
    os.environ["PERSIST_TIMEOUT_S"] = str(args.persist_timeout)
    os.environ["STREAM_DELAY_MS"] = os.environ.get("STREAM_DELAY_MS", "0")

    # load a .env sitting next to this file, so LLM mode works without exporting vars
    env_file = HERE / ".env"
    if env_file.is_file():
        for line in env_file.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                os.environ.setdefault(k.strip(), v.strip())

    from runtime.agent_runner import REGISTRY

    roles = [r.strip() for r in args.agents.split(",") if r.strip()]
    bus = LocalBus()

    print(f"[local] starting {len(roles)} agent(s): {', '.join(roles)}")
    print(f"[local] out={os.environ['OUT_DIR']}  data={os.environ['DATA_DIR']}\n")

    for role in roles:
        if role not in REGISTRY:
            raise SystemExit(f"unknown role {role!r}; valid: {', '.join(REGISTRY)}")
        module_name, class_name = REGISTRY[role]
        module = __import__(f"runtime.agents.{module_name}", fromlist=[class_name])
        agent = getattr(module, class_name)(bus)
        await agent.start()

    # replay the records exactly as the containerised producer does
    import json
    from runtime.base_agent import stream_document

    data_dir = Path(os.environ["DATA_DIR"])
    docs = sorted(data_dir.glob("**/*.json"))
    if not docs:
        raise SystemExit(f"no .json provenance records under {data_dir}")
    print(f"\n[local] replaying {len(docs)} yProv4ML document(s)\n")

    for path in docs:
        session = path.parent.name if path.parent != data_dir else path.stem
        doc = json.loads(path.read_text(encoding="utf-8"))
        await stream_document(bus, f"producer.yprov4ml.{session}",
                              session_id=session, producer_id="yprov4ml-replay", doc=doc)
        print(f"[local] streamed {path.name} as '{session}'")

    # let CA1's timeout guard fire for any session still awaiting acknowledgements
    wait = float(args.persist_timeout) + 2
    print(f"\n[local] settling for {wait:.0f}s (CA1 persist window)...")
    await asyncio.sleep(wait)
    print(f"\n[local] done — artifacts in {os.environ['OUT_DIR']}")


if __name__ == "__main__":
    asyncio.run(main())
