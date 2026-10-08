"""
util.py — small shared helpers for the runtime agents.

Keeps the agents focused on their logic: where to make the yProv4AgenticAI core
importable, the shared clock, writing emitted PROV-JSON to the output volume, and the one
place a tracker is constructed — so the instrumentation mode a run was asked for is honoured
by every agent or by none.

The model call lives in ``runtime/llm.py``; it is a bigger subject than a helper.
"""

from __future__ import annotations

import json
import os
import re
import sys
from functools import partial
from pathlib import Path
from typing import Any, NamedTuple

# Make the canonical core importable whether we run from the repo (dev) or the
# image (where /app is the repo root on PYTHONPATH).
_REPO_ROOT = Path(__file__).resolve().parent.parent
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

# One clock for the whole system: the library defines the timestamp format it writes into
# prov:startTime, and CA3 reads those values back as a timeline. A second definition here
# would be a second opinion about when things happened. Imported after the path setup
# above, which is what makes the package importable from a source checkout.
from yprov4agenticai import (  # noqa: E402
    GRANULARITIES,
    PERSISTENCES,
    CodeCarbonBackend,
    EnergyTracker,
    NullBackend,
    ProvTracker,
)
from yprov4agenticai import now_iso as now_iso  # noqa: E402

OUT_DIR = Path(os.environ.get("OUT_DIR", "/out"))

#: Environment variables that select the instrumentation mode for a whole run. They exist
#: so the overhead study can sweep the modes without editing six agents, and so the modes
#: get exercised by the real pipeline rather than only by a unit test (review finding F4:
#: METADATA had never been executed by an agent, and TRANSIENT had no implementation).
GRANULARITY_ENV = "PROV_GRANULARITY"
PERSISTENCE_ENV = "PROV_PERSISTENCE"
ENERGY_ENV = "PROV_ENERGY"
#: Grid carbon intensity is an *input* to an emissions figure, not something to discover:
#: a run that resolves it over the network cannot be repeated, and on Windows the lookup is
#: a documented crash site. State the country instead (3-letter ISO code).
ENERGY_COUNTRY_ENV = "PROV_ENERGY_COUNTRY"


def write_prov(role: str, session_id: str, doc: dict[str, Any]) -> Path | None:
    """Write an emitted PROV-JSON doc to the shared output volume, best-effort."""
    try:
        OUT_DIR.mkdir(parents=True, exist_ok=True)
        # sanitise: session ids can contain ':' (e.g. "example_0::cax"), which is a
        # reserved char on Windows hosts where /out is bind-mounted. Keep it portable.
        safe = re.sub(r"[^A-Za-z0-9._-]", "_", session_id)
        path = OUT_DIR / f"{safe}__{role}_prov.json"
        path.write_text(json.dumps(doc, indent=2, ensure_ascii=False), encoding="utf-8")
        return path
    except Exception as exc:
        print(f"[{role}] could not write output: {exc}", flush=True)
        return None


class ProvMode(NamedTuple):
    """The instrumentation settings a run was asked for."""

    granularity: str
    persistence: str


def prov_mode() -> ProvMode:
    """The granularity and persistence this run was configured for.

    Read from the environment once per tracker rather than baked into each agent, so a
    sweep over the modes is a change of environment and not a change of code. An unknown
    value fails here, at start-up, rather than inside ``ProvTracker`` once a session is
    already in flight.
    """
    granularity = os.environ.get(GRANULARITY_ENV, "DATA").strip().upper()
    persistence = os.environ.get(PERSISTENCE_ENV, "PERSISTENT").strip().upper()
    if granularity not in GRANULARITIES:
        raise SystemExit(
            f"{GRANULARITY_ENV} must be one of {GRANULARITIES}, got {granularity!r}"
        )
    if persistence not in PERSISTENCES:
        raise SystemExit(
            f"{PERSISTENCE_ENV} must be one of {PERSISTENCES}, got {persistence!r}"
        )
    return ProvMode(granularity=granularity, persistence=persistence)


def energy_tracker() -> EnergyTracker | None:
    """An energy tracker for this run, or None when energy accounting is off.

    Off by default, and that default is a measurement decision rather than laziness. A
    CodeCarbon backend samples hardware counters on its own schedule, which would
    contaminate every timing figure the overhead study reports.

    ``PROV_ENERGY=codecarbon`` for a run whose purpose is energy — with
    ``PROV_ENERGY_COUNTRY`` set, so the grid intensity behind the emissions figure is a
    stated parameter rather than a network lookup. ``PROV_ENERGY=null`` exercises the same
    code path and has the record state ``yp:energy_source="null"``: an audited zero rather
    than a silent absence, which is what the pipeline runs in CI.

    The irony this closes is worth naming: CA3 reports "no energy or emission streams" as a
    completeness gap in the graphs it analyses, and until Phase 4 it was right about the
    agents that emitted it (review finding F3).
    """
    backend = os.environ.get(ENERGY_ENV, "off").strip().lower()
    if backend in {"", "off", "0", "false", "no"}:
        return None
    if backend == "null":
        tracker = EnergyTracker(backend=NullBackend())
    else:
        tracker = EnergyTracker(
            backend=CodeCarbonBackend(
                country_iso_code=os.environ.get(ENERGY_COUNTRY_ENV) or None
            )
        )
    tracker.carbon_init(project_name="yprov4agents")
    return tracker


def agent_tracker(
    role: str,
    session_id: str,
    agent_label: str,
    sid: str | None = None,
    energy: EnergyTracker | None = None,
) -> ProvTracker:
    """The tracker an agent emits its own lifecycle into.

    One construction site for all six agents. Before this, each built its own with the
    same four arguments spelled out, which is how a mode flag ends up honoured by five
    agents and forgotten by the sixth.

    The sink writes the finished document to the shared output volume, and the mode comes
    from the environment — so ``PROV_PERSISTENCE=TRANSIENT`` means no agent writes, without
    any agent knowing about the flag.
    """
    mode = prov_mode()
    return ProvTracker(
        sid=sid or session_id,
        granularity=mode.granularity,
        persistence=mode.persistence,
        agent_label=agent_label,
        sink=partial(write_prov, role, session_id),
        verbose=False,
        energy=energy,
    )


def prov_counts(doc: dict[str, Any]) -> dict[str, int]:
    """Count activities, entities, and lineage relations in a PROV-JSON doc."""
    rel_keys = (
        "used",
        "wasGeneratedBy",
        "wasInformedBy",
        "wasDerivedFrom",
        "wasAssociatedWith",
        "actedOnBehalfOf",
    )
    return {
        "activities": len(doc.get("activity", {}) or {}),
        "entities": len(doc.get("entity", {}) or {}),
        "relations": sum(len(doc.get(k, {}) or {}) for k in rel_keys),
    }
