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

from .tracker import ProvTracker, YP_NAMESPACE
from .energy import (
    EnergyTracker, EnergyDelta, EnergyBackend,
    CodeCarbonBackend, NullBackend, session_energy_summary,
)
from .store import LightweightStore, ptr_kwargs
from . import prompts

__all__ = [
    "ProvTracker", "YP_NAMESPACE",
    "EnergyTracker", "EnergyDelta", "EnergyBackend",
    "CodeCarbonBackend", "NullBackend", "session_energy_summary",
    "LightweightStore", "ptr_kwargs",
    "prompts",
]

__version__ = "0.1.0"
