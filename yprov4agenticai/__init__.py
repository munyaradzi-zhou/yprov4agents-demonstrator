"""
yProv4AgenticAI — a W3C PROV-compliant library for provenance in the agentic AI lifecycle
=========================================================================================
The instrumentation core (Strategy B): drive a :class:`ProvTracker` explicitly from
inside an agent's own steps to emit typed W3C PROV — typed activities, a decision
``yp:justification``, per-activity energy, and prompt provenance — output that a
framework-level callback handler cannot reach and that no agent framework emits natively.

Framework-agnostic by construction: the tracker, prompts, and energy modules have no
dependency on any agent runtime, so the same instrumentation runs unchanged on
LangGraph, CrewAI, AutoGen, or a bare daemon (see the ``examples/`` reference agent).

Typical use::

    from yprov4agenticai import ProvTracker, EnergyTracker
    from yprov4agenticai.prompts import PROMPTS

    t = ProvTracker(sid="run01", granularity="DATA")
    eid = t.register_entity("TaskEntity", "...")
    t.record_activity("ReceiveTaskActivity", "receive", t0, t1, out_eid=eid)
    doc = t.to_prov_json()          # standard W3C PROV-JSON
"""

from __future__ import annotations

from . import prompts
from .energy import (
    CodeCarbonBackend,
    EnergyBackend,
    EnergyDelta,
    EnergyTracker,
    NullBackend,
    session_energy_summary,
)
from .store import LightweightStore, ptr_kwargs
from .tracker import YP_NAMESPACE, ProvTracker

__all__ = [
    "YP_NAMESPACE",
    "CodeCarbonBackend",
    "EnergyBackend",
    "EnergyDelta",
    "EnergyTracker",
    "LightweightStore",
    "NullBackend",
    "ProvTracker",
    "prompts",
    "ptr_kwargs",
    "session_energy_summary",
]


def _installed_version() -> str:
    """Read the version from installed distribution metadata.

    Declared once, in pyproject.toml. Duplicating it here as a literal lets the two drift,
    which is how a container ends up reporting a different version from the source tree it
    was built from.
    """
    from importlib.metadata import PackageNotFoundError, version

    try:
        return version("yprov4agents-demonstrator")
    except PackageNotFoundError:  # running from a source tree without an install
        return "0.0.0+unknown"


__version__ = _installed_version()
