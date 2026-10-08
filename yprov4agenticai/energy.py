"""
Agent runtime energy & emissions tracking — yProv4Agents
=========================================================

CONTEXT
-------
This module mirrors the energy-tracking API of yProv4ML (the sibling ML-training
producer in the yProv ecosystem): `carbon_init()` starts a session tracker and
`stop_carbon_tracked_block()` returns the *cumulative emissions since the last call*.
Adopting the same contract lets yProv4Agents present a single energy/emissions
surface across the yProv ecosystem, independent of the backend yProv4ML eventually
settles on (codecarbon today, a custom collector later). Reference implementation:
https://github.com/HPCI-Lab/yProv4ML

DESIGN
------
Two layers:

1. `EnergyBackend` — pluggable backend interface. The default `CodeCarbonBackend`
   wraps codecarbon's `EmissionsTracker` (matching yProv4ML's energy_utils.py).
   A `NullBackend` is used when codecarbon is absent or disabled (CI, laptops
   without the right kernel modules) so agents never crash on import.

2. `EnergyTracker` — exposes `carbon_init()` / `stop_carbon_tracked_block()` with
   identical semantics to yProv4ML. The "cumulative since last call" contract is
   what lets us slice energy *per activity* without nesting trackers.

INTEGRATION WITH ProvTracker
----------------------------
An agent drains energy at each node entry and captures the delta at the activity
boundary, attaching it to the activity as `yp:energy_kwh` / `yp:emissions_kg` /
`yp:duration_s` / `yp:energy_source` (see the reference agent in examples/mvp.py).

WHY DELTAS, NOT NESTED CONTEXTS
--------------------------------
codecarbon's `EmissionsTracker` does not nest cleanly (stop returns the *total*
since start). The workaround is the delta pattern: keep one tracker running for the
whole agent session and *subtract* on each read — one session-level tracker, many
activity-level slices.

PLATFORM NOTES
--------------
- Windows + Intel: codecarbon falls back to RAPL via Intel Power Gadget; only
  works if the gadget is installed. Otherwise the CPU energy is estimated from
  TDP, which is a *modelled* number, not a measured one. Flag this in the
  emitted attributes (`yp:energy_source = "measured" | "modelled"`).
- HPC / SLURM: codecarbon reads `/sys/class/powercap/intel-rapl/...` directly.
  Faster than the API path but requires read access to those files.
- Apple Silicon / ARM: codecarbon currently has no RAPL equivalent — falls back
  to TDP modelling; the polling loop (a thread sampling every N seconds) can add
  measurable overhead on these platforms.
- GPU: NVIDIA via pynvml works on most clusters; AMD via ROCm is partial.

A custom collector (Gabriele's plan) would replace `CodeCarbonBackend` with a
new `EnergyBackend` subclass — no changes required in the agent layer.
"""
from __future__ import annotations

import threading
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Optional


# ── Result type ───────────────────────────────────────────────────────────────

@dataclass
class EnergyDelta:
    """Energy and emissions accumulated since the previous call.

    Fields use the same units as yProv4ML / codecarbon:
      energy_kwh   — electrical energy consumed (kWh)
      emissions_kg — CO2-equivalent emissions (kg)
      duration_s   — wall-clock seconds covered by this delta
      source       — 'measured' (RAPL/pynvml) | 'modelled' (TDP fallback) | 'null'
    """
    energy_kwh:   float
    emissions_kg: float
    duration_s:   float
    source:       str = "measured"

    def to_attrs(self, prefix: str = "yp") -> dict:
        """Render as PROV activity attributes attached to the activity being recorded."""
        return {
            f"{prefix}:energy_kwh":   round(self.energy_kwh,   9),
            f"{prefix}:emissions_kg": round(self.emissions_kg, 9),
            f"{prefix}:duration_s":   round(self.duration_s,   3),
            f"{prefix}:energy_source": self.source,
        }


# ── Backend interface ─────────────────────────────────────────────────────────

class EnergyBackend(ABC):
    """Pluggable backend. Implement this to swap codecarbon for a custom collector."""

    @abstractmethod
    def start(self, project_name: str) -> None: ...

    @abstractmethod
    def read_total(self) -> tuple[float, float, float]:
        """Return (cumulative_energy_kwh, cumulative_emissions_kg, elapsed_s).

        MUST be safe to call multiple times. Returning the running totals
        (not stopping the tracker) is what enables the delta pattern.
        """

    @abstractmethod
    def stop(self) -> None: ...

    @property
    @abstractmethod
    def source_label(self) -> str: ...


# ── codecarbon backend (mirrors yProv4ML energy_utils.py) ────────────────────

class CodeCarbonBackend(EnergyBackend):
    """Default backend — matches the call sequence in yProv4ML's `_carbon_init`."""

    def __init__(self, output_dir: Optional[str] = None,
                 measure_power_secs: int = 15) -> None:
        self._tracker = None
        self._output_dir = output_dir or "."
        self._measure_power_secs = measure_power_secs
        self._t_start: Optional[float] = None

    def start(self, project_name: str) -> None:
        try:
            from codecarbon import EmissionsTracker  # type: ignore
        except ImportError as exc:
            raise RuntimeError(
                "codecarbon is not installed. `pip install codecarbon` or use "
                "NullBackend explicitly via EnergyTracker(backend=NullBackend())."
            ) from exc

        # log_level=error to silence the per-N-seconds INFO chatter
        self._tracker = EmissionsTracker(
            project_name=project_name,
            output_dir=self._output_dir,
            measure_power_secs=self._measure_power_secs,
            log_level="error",
            save_to_file=False,        # we publish via PROV, not CSV
            save_to_api=False,         # never phone home from agent code
            allow_multiple_runs=True,  # the bus may instantiate multiple agents
        )
        self._tracker.start()
        import time; self._t_start = time.monotonic()

    def read_total(self) -> tuple[float, float, float]:
        if self._tracker is None or self._t_start is None:
            return (0.0, 0.0, 0.0)
        # codecarbon exposes the rolling totals as attributes; `flush()`
        # forces a refresh from the sampling thread.
        try:
            emissions_kg = float(self._tracker.flush() or 0.0)
        except Exception:
            emissions_kg = float(getattr(self._tracker, "_total_emissions", 0.0) or 0.0)
        energy_kwh = float(getattr(self._tracker, "_total_energy", 0.0) or 0.0)
        import time; elapsed = time.monotonic() - self._t_start
        return (energy_kwh, emissions_kg, elapsed)

    def stop(self) -> None:
        if self._tracker is not None:
            try:
                self._tracker.stop()
            except Exception as exc:
                warnings.warn(f"codecarbon stop raised: {exc}")
            self._tracker = None

    @property
    def source_label(self) -> str:
        # codecarbon decides per-component (CPU/GPU/RAM) whether the number is
        # measured or modelled. We tag at the level it actually verifies RAPL.
        # Self-validating: 'measured' ONLY if the RAPL counter is actually READABLE
        # (an integer can be read from energy_uj). Since CVE-2020-8694 the counter is
        # root-locked by default; if it is unreadable codecarbon silently falls back to
        # TDP, so checking existence alone would MISLABEL a modelled number as measured.
        try:
            uj = "/sys/class/powercap/intel-rapl/intel-rapl:0/energy_uj"
            with open(uj) as f:
                int(f.read().strip())
            return "measured"
        except (OSError, ValueError):
            return "modelled"


# ── Null backend ──────────────────────────────────────────────────────────────

class NullBackend(EnergyBackend):
    """Used when codecarbon is unavailable or explicitly disabled.

    Returns zeros but still threads through the PROV attribute machinery so
    `yp:energy_source = "null"` makes the absence auditable rather than silent.
    """

    def start(self, project_name: str) -> None:
        self._t_start = None
        import time; self._t_start = time.monotonic()

    def read_total(self) -> tuple[float, float, float]:
        if self._t_start is None:
            return (0.0, 0.0, 0.0)
        import time; return (0.0, 0.0, time.monotonic() - self._t_start)

    def stop(self) -> None:
        self._t_start = None

    @property
    def source_label(self) -> str:
        return "null"


# ── EnergyTracker — Gabriele's API surface ────────────────────────────────────

class EnergyTracker:
    """Mirrors the yProv4ML API: `carbon_init()` + `stop_carbon_tracked_block()`.

    Contract identical to `yprov4ml.utils.energy_utils`:
      - `carbon_init(project_name)` starts a single backend tracker for the
        whole agent session.
      - `stop_carbon_tracked_block()` returns the *cumulative since the last
        call to stop_carbon_tracked_block* (or since `carbon_init` for the
        first call). It does NOT stop the underlying tracker.
      - `close()` actually stops the backend (for graceful shutdown).

    Thread-safe at the read level — multiple agents on the same process can
    each instantiate their own `EnergyTracker`, but a single tracker should
    not be shared across threads without external locking (codecarbon is
    process-wide in its CPU readings).
    """

    def __init__(self, backend: Optional[EnergyBackend] = None) -> None:
        if backend is None:
            try:
                import codecarbon  # noqa: F401
                backend = CodeCarbonBackend()
            except ImportError:
                warnings.warn(
                    "codecarbon not installed — energy tracking disabled. "
                    "Activities will be annotated with yp:energy_source='null'."
                )
                backend = NullBackend()
        self._backend = backend
        self._lock = threading.Lock()
        self._prev_energy_kwh   = 0.0
        self._prev_emissions_kg = 0.0
        self._prev_elapsed_s    = 0.0
        self._started           = False

    # ── public API matching yProv4ML ──────────────────────────────────────────

    def carbon_init(self, project_name: str = "yprov4agents") -> None:
        """Start the underlying tracker. Idempotent — repeat calls are no-ops."""
        if self._started:
            return
        self._backend.start(project_name)
        self._started = True

    def stop_carbon_tracked_block(self) -> EnergyDelta:
        """Return cumulative emissions since the last call to this method.

        Matches yProv4ML semantics exactly: the underlying tracker keeps
        running; only the *delta* is returned each time.
        """
        if not self._started:
            return EnergyDelta(0.0, 0.0, 0.0, source="null")

        with self._lock:
            total_e, total_co2, elapsed = self._backend.read_total()
            d_energy    = max(0.0, total_e   - self._prev_energy_kwh)
            d_emissions = max(0.0, total_co2 - self._prev_emissions_kg)
            d_elapsed   = max(0.0, elapsed   - self._prev_elapsed_s)
            self._prev_energy_kwh   = total_e
            self._prev_emissions_kg = total_co2
            self._prev_elapsed_s    = elapsed

        return EnergyDelta(
            energy_kwh   = d_energy,
            emissions_kg = d_emissions,
            duration_s   = d_elapsed,
            source       = self._backend.source_label,
        )

    def close(self) -> None:
        """Stop the underlying tracker. Call once at agent session end."""
        if not self._started:
            return
        self._backend.stop()
        self._started = False


# ── Session-level summary ─────────────────────────────────────────────────────

def session_energy_summary(per_activity_deltas: list[EnergyDelta]) -> dict:
    """Aggregate per-activity deltas into a session-level summary.

    Returns a dict ready to attach to an `yp:AgentSessionActivity` closing record.
    """
    total_e   = sum(d.energy_kwh   for d in per_activity_deltas)
    total_co2 = sum(d.emissions_kg for d in per_activity_deltas)
    total_s   = sum(d.duration_s   for d in per_activity_deltas)
    sources   = {d.source for d in per_activity_deltas}
    return {
        "yp:total_energy_kwh":   round(total_e,   9),
        "yp:total_emissions_kg": round(total_co2, 9),
        "yp:total_duration_s":   round(total_s,   3),
        "yp:energy_source":      "mixed" if len(sources) > 1 else next(iter(sources), "null"),
        "yp:n_activities":       len(per_activity_deltas),
    }
