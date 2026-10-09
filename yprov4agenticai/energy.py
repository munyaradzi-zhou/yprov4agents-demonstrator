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
Whether a figure is MEASURED or MODELLED is a platform fact, not a config option,
and `yp:energy_source` carries the answer per record. Verified on this project's
development host rather than assumed:

- Windows: usually NO readable counter, so figures are modelled. RAPL is a Linux
  sysfs interface and is absent; Intel Power Gadget is discontinued and absent;
  the Windows Energy Meter Interface is reported *available* by codecarbon yet
  returns a processor energy delta of 0.0 under a saturating load, because it
  needs OEM firmware most laptops do not ship. codecarbon then estimates CPU
  power as `TDP * (0.1 + 0.9 * load**3)` and RAM power as a flat function of
  installed capacity — on this host a constant 10 W, which was 41% of the total
  in a four-second calibration. Real values need a Linux boot with readable RAPL.
- Linux / HPC / SLURM: codecarbon reads `/sys/class/powercap/intel-rapl/...`
  directly, which is the measured path — but since CVE-2020-8694 the counter is
  root-locked by default, and when it is unreadable codecarbon falls back to
  modelling *silently*. Readability, not existence, is what distinguishes the two.
- Apple Silicon / ARM: no RAPL equivalent, so modelled; the polling thread can
  add measurable overhead on these platforms.
- GPU: NVIDIA via pynvml works on most clusters; AMD via ROCm is partial.

A custom collector (Gabriele's plan) would replace `CodeCarbonBackend` with a
new `EnergyBackend` subclass — no changes required in the agent layer.
"""

from __future__ import annotations

import threading
import warnings
from abc import ABC, abstractmethod
from dataclasses import dataclass
from typing import Any

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

    energy_kwh: float
    emissions_kg: float
    duration_s: float
    source: str = "measured"
    sampling_period_s: float | None = None

    @property
    def attributable(self) -> bool:
        """Whether this delta can honestly be attributed to the step it is recorded on.

        It cannot, when the backend samples less often than the step lasts. A backend with
        a 15-second period reading across a 2-millisecond step returns whatever its
        background thread happened to accumulate, so the figure lands on whichever step
        read after a tick rather than on the step that spent the energy. Measured on this
        demonstrator: a step of 0.000 s was handed 46.9 µWh while the rendering step that
        actually did the work was handed 4.6 µWh.

        The figure is still worth recording rather than dropping, so it is kept and the
        attribution is denied instead. Note that this property is about attribution only
        and says nothing about whether the underlying watts were observed or estimated —
        `source` carries that, and on a host without a readable counter the answer is
        "modelled". A number can fail both tests at once, and the record reports both.
        """
        if self.sampling_period_s is None:
            return True
        return self.duration_s >= self.sampling_period_s

    def to_attrs(self, prefix: str = "yp") -> dict:
        """Render as PROV activity attributes attached to the activity being recorded."""
        attrs = {
            f"{prefix}:energy_kwh": round(self.energy_kwh, 9),
            f"{prefix}:emissions_kg": round(self.emissions_kg, 9),
            f"{prefix}:duration_s": round(self.duration_s, 3),
            f"{prefix}:energy_source": self.source,
        }
        if self.sampling_period_s is not None:
            attrs[f"{prefix}:energy_sampling_period_s"] = self.sampling_period_s
            attrs[f"{prefix}:energy_attributable"] = self.attributable
        return attrs


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
    def sampling_period_s(self) -> float | None:
        """How often this backend refreshes its totals, if it samples at all.

        Recorded with every delta, because it bounds what the delta can be attributed to.
        """
        return None

    @property
    @abstractmethod
    def source_label(self) -> str: ...


# ── codecarbon backend (mirrors yProv4ML energy_utils.py) ────────────────────


class CodeCarbonBackend(EnergyBackend):
    """Default backend — matches the call sequence in yProv4ML's `_carbon_init`."""

    def __init__(
        self,
        output_dir: str | None = None,
        measure_power_secs: int = 15,
        country_iso_code: str | None = None,
    ) -> None:
        # Typed as Any because codecarbon is an optional dependency: annotating the
        # concrete class here would make importing this module require the package.
        self._tracker: Any = None
        self._output_dir = output_dir or "."
        self._measure_power_secs = measure_power_secs
        self._country_iso_code = country_iso_code
        self._t_start: float | None = None

    def start(self, project_name: str) -> None:
        """Start sampling. Offline when a country code is given, which it should be.

        codecarbon's default tracker resolves the local grid's carbon intensity over the
        network at start-up. Two reasons not to let it:

        *Reproducibility.* Grid intensity is an input to the emissions figure, so a run
        that discovers it rather than stating it cannot be repeated — the same workload on
        the same machine yields a different number in a different place, or after the
        upstream dataset is revised. ``country_iso_code`` makes it a declared parameter.

        *It is also where this crashes.* On a Windows host with a conflicting OpenSSL in
        the load path, the start-up lookup aborts the process with
        ``OPENSSL_Uplink … no OPENSSL_Applink`` before any agent runs. An energy backend
        is not allowed to take the pipeline down with it.
        """
        try:
            from codecarbon import EmissionsTracker, OfflineEmissionsTracker
        except ImportError as exc:
            raise RuntimeError(
                "codecarbon is not installed. Install the `energy` extra, or use "
                "NullBackend explicitly via EnergyTracker(backend=NullBackend())."
            ) from exc

        # log_level=error to silence the per-N-seconds INFO chatter
        options: dict[str, Any] = {
            "project_name": project_name,
            "output_dir": self._output_dir,
            "measure_power_secs": self._measure_power_secs,
            "log_level": "error",
            "save_to_file": False,  # we publish via PROV, not CSV
            "save_to_api": False,  # never phone home from agent code
            "allow_multiple_runs": True,  # the bus may instantiate multiple agents
        }
        if self._country_iso_code:
            self._tracker = OfflineEmissionsTracker(
                country_iso_code=self._country_iso_code, **options
            )
        else:
            warnings.warn(
                "No country_iso_code given, so codecarbon will resolve grid intensity "
                "over the network. The emissions figure is then not reproducible, and "
                "the lookup is a documented crash site on Windows. Set "
                "PROV_ENERGY_COUNTRY to a 3-letter ISO code.",
                stacklevel=2,
            )
            self._tracker = EmissionsTracker(**options)
        self._tracker.start()
        import time

        self._t_start = time.monotonic()

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
        import time

        elapsed = time.monotonic() - self._t_start
        return (energy_kwh, emissions_kg, elapsed)

    def stop(self) -> None:
        if self._tracker is not None:
            try:
                self._tracker.stop()
            except Exception as exc:
                warnings.warn(f"codecarbon stop raised: {exc}", stacklevel=2)
            self._tracker = None

    @property
    def sampling_period_s(self) -> float | None:
        return float(self._measure_power_secs)

    @property
    def grid_intensity_source(self) -> str:
        """Where the carbon intensity figure came from: a declared country, or a lookup."""
        return self._country_iso_code or "network-lookup"

    #: codecarbon's own name for the hardware interfaces that read a real counter.
    #: Anything else it selects ("cpu_load", "constant") is a model of power, not an
    #: observation of it.
    _HARDWARE_MODES = frozenset({"intel_rapl", "intel_power_gadget", "windows_emi"})

    @property
    def source_label(self) -> str:
        """``"measured"`` only when a hardware counter is both selected and delivering.

        Two traps, and we have been caught by both. Probing only the Linux RAPL path
        (``/sys/class/powercap/...``) reports "modelled" on every non-Linux host even
        when a real counter is in use — the mistake this method used to make. Trusting
        the backend's selected mode instead reports "measured" whenever the interface
        merely *loads*: on the development host codecarbon selects ``windows_emi`` and
        that interface returns a processor energy delta of 0.0 under a saturating load,
        because it needs OEM firmware the machine does not provide.

        So the label requires both: a hardware mode, and a non-zero cumulative energy
        reading to show the counter is actually delivering. An idle host could in
        principle read zero and be downgraded to "modelled"; that is the safe direction
        to be wrong in, because overclaiming a measurement is the error that corrupts a
        result and underclaiming one only costs a footnote.
        """
        mode = self._cpu_mode()
        if mode is None or mode not in self._HARDWARE_MODES:
            return "modelled"
        if float(getattr(self._tracker, "_total_energy", 0.0) or 0.0) <= 0.0:
            return "modelled"
        return "measured"

    def _cpu_mode(self) -> str | None:
        """The interface codecarbon chose for the CPU, asked rather than inferred."""
        for hardware in getattr(self._tracker, "_hardware", ()) or ():
            mode = getattr(hardware, "_mode", None)
            if isinstance(mode, str):
                return mode
        return None


# ── Null backend ──────────────────────────────────────────────────────────────


class NullBackend(EnergyBackend):
    """Used when codecarbon is unavailable or explicitly disabled.

    Returns zeros but still threads through the PROV attribute machinery so
    `yp:energy_source = "null"` makes the absence auditable rather than silent.
    """

    def start(self, project_name: str) -> None:
        self._t_start = None
        import time

        self._t_start = time.monotonic()

    def read_total(self) -> tuple[float, float, float]:
        if self._t_start is None:
            return (0.0, 0.0, 0.0)
        import time

        return (0.0, 0.0, time.monotonic() - self._t_start)

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

    def __init__(self, backend: EnergyBackend | None = None) -> None:
        if backend is None:
            try:
                import codecarbon  # noqa: F401

                backend = CodeCarbonBackend()
            except ImportError:
                warnings.warn(
                    "codecarbon not installed — energy tracking disabled. "
                    "Activities will be annotated with yp:energy_source='null'.",
                    stacklevel=2,
                )
                backend = NullBackend()
        self._backend = backend
        self._lock = threading.Lock()
        self._prev_energy_kwh = 0.0
        self._prev_emissions_kg = 0.0
        self._prev_elapsed_s = 0.0
        self._started = False

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
            d_energy = max(0.0, total_e - self._prev_energy_kwh)
            d_emissions = max(0.0, total_co2 - self._prev_emissions_kg)
            d_elapsed = max(0.0, elapsed - self._prev_elapsed_s)
            self._prev_energy_kwh = total_e
            self._prev_emissions_kg = total_co2
            self._prev_elapsed_s = elapsed

        return EnergyDelta(
            energy_kwh=d_energy,
            emissions_kg=d_emissions,
            duration_s=d_elapsed,
            source=self._backend.source_label,
            sampling_period_s=self._backend.sampling_period_s,
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
    total_e = sum(d.energy_kwh for d in per_activity_deltas)
    total_co2 = sum(d.emissions_kg for d in per_activity_deltas)
    total_s = sum(d.duration_s for d in per_activity_deltas)
    sources = {d.source for d in per_activity_deltas}
    return {
        "yp:total_energy_kwh": round(total_e, 9),
        "yp:total_emissions_kg": round(total_co2, 9),
        "yp:total_duration_s": round(total_s, 3),
        "yp:energy_source": "mixed" if len(sources) > 1 else next(iter(sources), "null"),
        "yp:n_activities": len(per_activity_deltas),
    }
