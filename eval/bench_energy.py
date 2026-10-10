"""E7 — what instrumentation costs in energy, at the granularity energy can be measured.

The per-step energy figures of Phase 4 were a negative result: a sampler that ticks every
second or slower cannot attribute energy to steps that last milliseconds (paper §6.3). This
study is the positive half of that result. It measures where energy *can* be measured — over
windows of many whole sessions — and asks the question E6 asked of time: does recording
provenance change what the work costs?

    python -m eval.bench_energy --rounds 8 --window-s 20 --country ITA

**The unit of work** is one session: all six agents processing one real yProv4ML record, the
same work Level 2 of E6 times agent by agent. **A window** is a fixed number of sessions under
one condition, sized so that it spans many sampling periods. Energy is read from the backend's
cumulative counters at the window's two edges, each read forcing a fresh sample, so a window's
energy is exactly what accrued between them.

**Five conditions**, interleaved round-robin with the starting condition rotated each round:
OFF, A, B-TRANS and B as in E6, plus IDLE — the same wall-clock duration with no work at all,
which is what the machine draws regardless. Energy above IDLE is the energy of the work.

**Measured and modelled are kept apart.** codecarbon reports CPU and RAM separately, and on a
given host one may come from a hardware counter while the other is a model. Each component is
published with the interface it came from and a label derived from that interface, never one
label for the sum. A sum of a measured and a modelled quantity is neither.
"""

from __future__ import annotations

import argparse
import math
import statistics
import tempfile
import time
import warnings
from collections.abc import Callable
from dataclasses import asdict
from pathlib import Path
from typing import Any

from eval.bench_overhead import CONDITIONS, _producer_record, _redirect_output, condition
from eval.bench_support import (
    RESOLVE_MULTIPLE,
    capture_environment,
    dispersion,
    python_is_optimised,
    write_results,
)

#: Every condition a round contains. IDLE first in the definition only; the order a round
#: runs them in is rotated (see :func:`_round_order`).
ENERGY_CONDITIONS = ("IDLE", *CONDITIONS)

#: codecarbon's names for the interfaces that read a hardware counter. Anything else it
#: selects is a model of power rather than an observation of it.
HARDWARE_CPU_MODES = frozenset({"intel_rapl", "intel_power_gadget", "windows_emi"})

JOULES_PER_KWH = 3.6e6

#: Fewer rounds than this and a dispersion is not an estimate of anything, so no difference
#: is judged at all — a spread of zero from one sample would otherwise make every difference
#: look infinitely resolved.
MIN_ROUNDS_FOR_A_VERDICT = 3

#: Sessions timed to size a window, after two discarded ones. The first sessions of a
#: process run cold — imports, caches, the chart backend — and sizing from three of them
#: made a pilot's work windows 12 s against a 20 s target.
CALIBRATION_SESSIONS = 10


# ── the backend's counters ─────────────────────────────────────────────────────────
class EnergyMeter:
    """Cumulative CPU and RAM energy from one codecarbon tracker, read on demand.

    One tracker for the whole study, not one per window: starting codecarbon probes the
    hardware for a second or more, and doing that between windows would make the probe part
    of whichever window followed it.

    codecarbon does not publish its per-component totals, so they are read from the
    tracker's attributes; the version is recorded with the results, and a missing attribute
    fails loudly rather than reading as zero.
    """

    def __init__(self, country_iso_code: str, period_s: float) -> None:
        from codecarbon import OfflineEmissionsTracker

        self.period_s = period_s
        self._tracker = OfflineEmissionsTracker(
            country_iso_code=country_iso_code,
            measure_power_secs=period_s,
            log_level="error",
            save_to_file=False,
            save_to_api=False,
            allow_multiple_runs=True,
        )

    def __enter__(self) -> EnergyMeter:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._tracker.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        with warnings.catch_warnings():
            warnings.simplefilter("ignore")
            self._tracker.stop()

    def read_joules(self) -> tuple[float, float]:
        """(CPU, RAM) energy so far, after forcing a fresh sample.

        The forced sample matters: until codecarbon's first tick the running totals read
        zero, which is how an earlier four-second calibration reported a working hardware
        counter as returning nothing (docs/phases/PHASE_7.md).
        """
        self._tracker._measure_power_and_energy()
        cpu = float(self._tracker._total_cpu_energy.kWh) * JOULES_PER_KWH
        ram = float(self._tracker._total_ram_energy.kWh) * JOULES_PER_KWH
        return cpu, ram

    def describe(self) -> dict[str, Any]:
        """Which interface each component came from, and whether that is a measurement."""
        import codecarbon

        modes = [getattr(device, "_mode", None) for device in self._tracker._hardware]
        cpu_mode = next((mode for mode in modes if isinstance(mode, str)), None)
        return {
            "codecarbon": codecarbon.__version__,
            "sampling_period_s": self.period_s,
            "cpu_interface": cpu_mode,
            "cpu_source": "measured" if cpu_mode in HARDWARE_CPU_MODES else "modelled",
            "cpu_scope": (
                "the whole processor package, every process on the machine"
                if cpu_mode in HARDWARE_CPU_MODES
                else "a function of CPU load and the processor's TDP"
            ),
            "ram_interface": "codecarbon RAM model",
            "ram_source": "modelled",
            "ram_power_w": float(self._tracker._ram_power.W),
            "country_iso_code": self._tracker._country_iso_code,
        }


# ── the work ──────────────────────────────────────────────────────────────────────
def _session(root: Path) -> Callable[[], None]:
    """One session of all six agents on one real record, as a callable."""
    from runtime.agents.ca1_storage import CA1Storage
    from runtime.agents.ca2_metrics import CA2Metrics
    from runtime.agents.ca3_analytics import CA3Analytics
    from runtime.agents.cax_summariser import CAxSummariser
    from runtime.agents.mc1_compliance import MC1Compliance
    from runtime.agents.mc3_audit import MC3Audit

    _redirect_output(root)
    document = _producer_record()
    # No bus: `process()` and `_do_persist()` return what they would publish, and nothing
    # here publishes it, exactly as in E6 Level 2.
    no_bus: Any = None
    consumers = [
        (CA3Analytics(no_bus), "producer.yprov4ml.bench"),
        (CA2Metrics(no_bus), "producer.yprov4ml.bench"),
        (CAxSummariser(no_bus), "producer.yprov4ml.bench"),
        (MC1Compliance(no_bus), "agent.lifecycle.cax.bench"),
        (MC3Audit(no_bus), "agent.lifecycle.cax.bench"),
    ]
    storage = CA1Storage(no_bus)

    def run() -> None:
        for agent, subject in consumers:
            agent.process("bench", document, subject)
        storage._do_persist("bench", document, ["ca2", "ca3", "cax"], "quorum", "benchmark")

    return run


def _round_order(index: int) -> tuple[str, ...]:
    """The conditions of one round, rotated so no condition always follows the same one.

    Plain round-robin spreads drift across conditions; rotation also spreads the after-effect
    of the previous window — a processor still hot from four busy windows, or just cooled by
    an idle one — which plain round-robin hands to the same condition every round.
    """
    shift = index % len(ENERGY_CONDITIONS)
    return ENERGY_CONDITIONS[shift:] + ENERGY_CONDITIONS[:shift]


def _window(
    meter: EnergyMeter, name: str, session: Callable[[], None], sessions: int, idle_s: float
) -> dict[str, float]:
    """One window: energy and time between two forced samples."""
    cpu_start, ram_start = meter.read_joules()
    started = time.perf_counter()
    if name == "IDLE":
        time.sleep(idle_s)
    else:
        with condition(name):
            for _ in range(sessions):
                session()
    elapsed = time.perf_counter() - started
    cpu_end, ram_end = meter.read_joules()
    return {
        "cpu_j": cpu_end - cpu_start,
        "ram_j": ram_end - ram_start,
        "duration_s": elapsed,
        "sessions": 0 if name == "IDLE" else sessions,
    }


# ── the study ─────────────────────────────────────────────────────────────────────
def level_energy(
    out_dir: Path,
    rounds: int = 12,
    window_s: float = 20.0,
    period_s: float = 1.0,
    country_iso_code: str = "ITA",
) -> dict[str, Any]:
    """Interleave the five conditions over ``rounds`` rounds and summarise per session."""
    with tempfile.TemporaryDirectory() as temporary:
        session = _session(Path(temporary))

        # Warm up, then size a window from the uninstrumented session time, so every
        # condition does the same work and a slower condition simply takes longer.
        with condition("OFF"):
            for _ in range(2):
                session()
            started = time.perf_counter()
            for _ in range(CALIBRATION_SESSIONS):
                session()
        per_session_s = (time.perf_counter() - started) / CALIBRATION_SESSIONS
        sessions = max(1, math.ceil(window_s / per_session_s))
        print(
            f"[energy] {per_session_s * 1e3:.0f} ms per session; {sessions} sessions per "
            f"window; {rounds} rounds of {len(ENERGY_CONDITIONS)} windows",
            flush=True,
        )

        windows: dict[str, list[dict[str, float]]] = {name: [] for name in ENERGY_CONDITIONS}
        warmup: dict[str, dict[str, float]] = {}
        with EnergyMeter(country_iso_code, period_s) as meter:
            # One whole round first, recorded and excluded. In the pilot the first window
            # after the meter started read 12 W idle against 2-6 W for every later one.
            for name in ENERGY_CONDITIONS:
                warmup[name] = _window(meter, name, session, sessions, sessions * per_session_s)
            for index in range(rounds):
                for name in _round_order(index):
                    result = _window(meter, name, session, sessions, sessions * per_session_s)
                    windows[name].append(result)
                    print(
                        f"[energy] round {index + 1}/{rounds} {name:<8} "
                        f"{result['duration_s']:6.1f} s  cpu {result['cpu_j']:7.1f} J  "
                        f"ram {result['ram_j']:6.1f} J",
                        flush=True,
                    )
            source = meter.describe()

    payload = {
        "level": "energy",
        "environment": asdict(
            capture_environment(interpreter_clean=python_is_optimised(), energy=source)
        ),
        "source": source,
        "rounds": rounds,
        "sessions_per_window": sessions,
        "records_per_session": 1,
        "windows": windows,
        "warmup_round_excluded": warmup,
        "summary": summarise(windows),
    }
    write_results(out_dir / "energy.json", payload)
    return payload


def _per_session_net(windows: dict[str, list[dict[str, float]]], name: str) -> list[float]:
    """CPU joules per session above what the machine drew idle over the same time.

    Each window is paired with the IDLE window of its own round, so a background process
    or a thermal state that lasted a round is subtracted from that round only.
    """
    idle = windows["IDLE"]
    values = []
    for window, rest in zip(windows[name], idle, strict=True):
        idle_power = rest["cpu_j"] / rest["duration_s"]
        above_idle = window["cpu_j"] - idle_power * window["duration_s"]
        values.append(above_idle / window["sessions"])
    return values


def summarise(windows: dict[str, list[dict[str, float]]]) -> dict[str, Any]:
    """Medians, dispersions and the B-versus-OFF verdicts, from the stored windows."""
    idle = windows["IDLE"]
    summary: dict[str, Any] = {
        "idle_cpu_w": statistics.median(w["cpu_j"] / w["duration_s"] for w in idle),
        "ram_w": statistics.median(w["ram_j"] / w["duration_s"] for w in idle),
        "conditions": {},
    }
    for name in CONDITIONS:
        rows = windows[name]
        gross = [w["cpu_j"] / w["sessions"] for w in rows]
        net = _per_session_net(windows, name)
        ram = [w["ram_j"] / w["sessions"] for w in rows]
        summary["conditions"][name] = {
            "cpu_j_per_session": statistics.median(gross),
            "cpu_j_per_session_dispersion": dispersion(gross),
            "cpu_j_per_session_above_idle": statistics.median(net),
            "cpu_j_per_session_above_idle_dispersion": dispersion(net),
            "ram_j_per_session": statistics.median(ram),
            "seconds_per_session": statistics.median(
                w["duration_s"] / w["sessions"] for w in rows
            ),
            "cpu_w": statistics.median(w["cpu_j"] / w["duration_s"] for w in rows),
        }

    # Two comparisons, because they answer different questions. Above idle: the energy of
    # the work alone — but the idle power it subtracts wanders with background load, which
    # adds its own spread. Gross: what a session costs the machine, including the idle
    # power drawn for as long as the session takes — so a slower condition pays for its
    # extra time, which is a real cost and not noise.
    def per_session(name: str) -> list[float]:
        return [w["cpu_j"] / w["sessions"] for w in windows[name]]

    for name in ("A", "B-TRANS", "B"):
        summary["conditions"][name]["vs_off"] = _verdict(
            _per_session_net(windows, "OFF"), _per_session_net(windows, name)
        )
        summary["conditions"][name]["vs_off_gross"] = _verdict(
            per_session("OFF"), per_session(name)
        )
    return summary


def _verdict(baseline: list[float], measured: list[float]) -> dict[str, Any]:
    """The difference of two sets of per-session values, judged by the 3x rule."""
    delta = statistics.median(measured) - statistics.median(baseline)
    noise = max(dispersion(baseline), dispersion(measured))
    judged = len(measured) >= MIN_ROUNDS_FOR_A_VERDICT and noise > 0
    margin = abs(delta) / noise if judged else None
    return {
        "delta_cpu_j_per_session": delta,
        "pct": 100.0 * delta / statistics.median(baseline),
        "noise_j": noise,
        "margin": margin,
        "resolvable": margin is not None and margin > RESOLVE_MULTIPLE,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m eval.bench_energy",
        description="E7: the energy of instrumentation, measured over whole sessions.",
    )
    parser.add_argument("--out-dir", default="out/bench")
    parser.add_argument("--rounds", type=int, default=12)
    parser.add_argument("--window-s", type=float, default=20.0)
    parser.add_argument(
        "--period-s", type=float, default=1.0, help="codecarbon's sampling period"
    )
    parser.add_argument(
        "--country",
        default="ITA",
        help="3-letter ISO code: grid intensity is a stated input, never a network lookup",
    )
    args = parser.parse_args(argv)
    payload = level_energy(
        Path(args.out_dir), args.rounds, args.window_s, args.period_s, args.country
    )
    for name, row in payload["summary"]["conditions"].items():
        verdict = row.get("vs_off")
        if verdict is None:
            tail = ""
        elif verdict["margin"] is None:
            tail = f"  vs OFF {verdict['pct']:+.1f}% (too few rounds to judge)"
        else:
            tail = f"  vs OFF {verdict['pct']:+.1f}% ({verdict['margin']:.1f}×)"
        print(
            f"  {name:<8} {row['cpu_j_per_session_above_idle']:7.3f} J/session above idle "
            f"({row['cpu_w']:.1f} W){tail}"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
