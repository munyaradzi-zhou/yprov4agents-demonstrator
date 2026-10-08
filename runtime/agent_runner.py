"""
agent_runner.py — the single container entrypoint.

One image, many roles: the `ROLE` env var selects what this container becomes — an
operational agent, a meta agent, or the producer. That is why the whole system is one
small image started many times, not six different images.

    ROLE=ca1      python -m runtime.agent_runner   # operational: persist (after consume)
    ROLE=cax      python -m runtime.agent_runner   # operational: NL summarise (LLM)
    ROLE=ca2      python -m runtime.agent_runner   # operational: visualise
    ROLE=ca3      python -m runtime.agent_runner   # operational: analytics
    ROLE=mc1      python -m runtime.agent_runner   # meta: compliance
    ROLE=mc3      python -m runtime.agent_runner   # meta: audit
    ROLE=producer python -m runtime.agent_runner   # replay real yProv4ML records
"""

from __future__ import annotations

import asyncio
import os

from .net_bus import NetworkBus

#: role → (module path, class name). Lazy imports keep a container from paying for
#: dependencies it does not use (e.g. only ca2 needs matplotlib).
REGISTRY = {
    "ca1": ("ca1_storage", "CA1Storage"),
    "cax": ("cax_summariser", "CAxSummariser"),
    "ca2": ("ca2_metrics", "CA2Metrics"),
    "ca3": ("ca3_analytics", "CA3Analytics"),
    "mc1": ("mc1_compliance", "MC1Compliance"),
    "mc3": ("mc3_audit", "MC3Audit"),
}


async def _run_agent(role: str) -> None:
    if role not in REGISTRY:
        raise SystemExit(f"unknown ROLE={role!r}; valid: {', '.join(REGISTRY)}, producer")
    module_name, class_name = REGISTRY[role]
    module = __import__(f"runtime.agents.{module_name}", fromlist=[class_name])
    agent_cls = getattr(module, class_name)

    bus = NetworkBus(os.environ.get("NATS_URL", "nats://broker:4222"), name=role)
    await bus.connect()
    agent = agent_cls(bus)
    await agent.start()
    while True:  # stay up, reacting to the stream
        await asyncio.sleep(3600)


def main() -> None:
    role = os.environ.get("ROLE", "").strip().lower()
    if role == "producer":
        from .producer import main as producer_main

        asyncio.run(producer_main())
        return
    asyncio.run(_run_agent(role))


if __name__ == "__main__":
    main()
