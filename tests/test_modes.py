"""The instrumentation modes, exercised through an agent rather than only through the tracker.

Review finding F4: `granularity="METADATA"` had never been executed by anything except a
unit test, and `persistence="TRANSIENT"` had no implementation at all — it was stored,
reported in the record's own bundle descriptor, and did nothing. A configuration flag that
does not configure anything is worse than a missing feature, because the record then asserts
something untrue about itself.

The overhead study sweeps these modes (`docs/E6_OVERHEAD.md` §4.2), so each one needs to be
reachable the way that study will reach it: an environment variable, honoured by every agent
or by none.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pytest

from runtime.agents.ca2_metrics import CA2Metrics
from runtime.util import (
    ENERGY_ENV,
    GRANULARITY_ENV,
    INSTRUMENTATION_ENV,
    PERSISTENCE_ENV,
    prov_mode,
)
from yprov4agenticai import ProvTracker

REL_KEYS = ("used", "wasGeneratedBy", "wasDerivedFrom")


@pytest.fixture
def ca2(headless_agent: Callable[..., CA2Metrics]) -> CA2Metrics:
    return headless_agent(CA2Metrics)


def _producer_document() -> dict[str, Any]:
    return {
        "prefix": {"prov": "http://www.w3.org/ns/prov#"},
        "activity": {"a": {"prov:label": "step"}},
        "entity": {"e": {"prov:value": "x"}},
    }


# ── the mode is read from the environment, once ───────────────────────────────────
def test_the_default_mode_is_full_persistent_and_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Provenance on, complete, and written, unless a deployment says otherwise."""
    for variable in (GRANULARITY_ENV, PERSISTENCE_ENV, INSTRUMENTATION_ENV):
        monkeypatch.delenv(variable, raising=False)
    assert prov_mode() == ("DATA", "PERSISTENT", True)


def test_instrumentation_can_be_turned_off_entirely(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The off switch is a deployment decision, and condition OFF of the overhead study."""
    monkeypatch.setenv(INSTRUMENTATION_ENV, "off")
    assert prov_mode().enabled is False


def test_a_disabled_agent_emits_nothing_and_still_runs(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv(INSTRUMENTATION_ENV, "off")
    document, log_line = ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    assert document == {}, "no record is built"
    assert not list(tmp_path.glob("*_prov.json")), "and none is written"
    assert "visualised" in log_line, "but the agent did its actual work"


def test_an_unknown_mode_fails_at_start_up(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail where the mistake was made, not inside a session already in flight."""
    monkeypatch.setenv(GRANULARITY_ENV, "SUMMARY")
    with pytest.raises(SystemExit, match="PROV_GRANULARITY"):
        prov_mode()


def test_the_tracker_rejects_a_persistence_it_cannot_honour() -> None:
    with pytest.raises(ValueError, match="persistence"):
        ProvTracker(sid="s", persistence="MAYBE")


# ── TRANSIENT ─────────────────────────────────────────────────────────────────────
def test_a_transient_tracker_builds_the_record_but_does_not_hand_it_over() -> None:
    """The flag suppresses the sink, not the document. Precisely that, and nothing more."""
    written: list[dict[str, Any]] = []
    tracker = ProvTracker(sid="s", persistence="TRANSIENT", sink=written.append, verbose=False)
    with tracker.activity("ReceiveTaskActivity") as prov:
        prov.produces("TaskEntity", "the task")

    document = tracker.emit()
    assert written == [], "a transient record must not reach the sink"
    assert len(document["activity"]) == 1, "but it is still built, and still returned"
    assert document["entity"], "and still complete"


def test_a_transient_agent_writes_nothing_to_disk(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """End to end: no agent knows about the flag, and no agent writes."""
    monkeypatch.setenv(PERSISTENCE_ENV, "TRANSIENT")
    document, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    assert document["activity"], "the record is still produced and still published"
    assert not list(tmp_path.glob("*_prov.json")), "and nothing was written"


def test_a_persistent_agent_does_write(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The control for the test above: without the flag, the file appears."""
    monkeypatch.setenv(PERSISTENCE_ENV, "PERSISTENT")
    ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    written = list(tmp_path.glob("*_prov.json"))
    assert len(written) == 1
    assert json.loads(written[0].read_text(encoding="utf-8"))["activity"]


def test_the_record_states_the_mode_it_was_built_under(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A reader cannot interpret a record without knowing how it was configured."""
    monkeypatch.setenv(PERSISTENCE_ENV, "TRANSIENT")
    document, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    bundle = next(
        entity
        for entity in document["entity"].values()
        if entity.get("prov:type", {}).get("$") == "prov:Bundle"
    )
    assert bundle["yp:persistence"] == "TRANSIENT"
    assert bundle["yp:granularity"] == "DATA"


# ── METADATA ──────────────────────────────────────────────────────────────────────
def test_metadata_mode_drops_entities_and_their_relations_through_an_agent(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The mode the overhead study measures, exercised by the agent that will be measured."""
    monkeypatch.setenv(GRANULARITY_ENV, "DATA")
    full, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")
    monkeypatch.setenv(GRANULARITY_ENV, "METADATA")
    lean, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    assert len(lean["activity"]) == len(full["activity"]), "the steps are unchanged"
    # Only the bundle descriptor survives: it is metadata about the record, not one of the
    # data entities the mode is defined to omit.
    assert len(lean["entity"]) == 1
    assert all(not lean.get(key) for key in REL_KEYS)
    assert lean["wasAssociatedWith"], "activity-level relations are retained"


def test_metadata_mode_is_smaller_and_by_how_much(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The output-size result E6c reports, measured rather than asserted."""
    monkeypatch.setenv(GRANULARITY_ENV, "DATA")
    full, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")
    monkeypatch.setenv(GRANULARITY_ENV, "METADATA")
    lean, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    full_bytes = len(json.dumps(full).encode("utf-8"))
    lean_bytes = len(json.dumps(lean).encode("utf-8"))
    assert lean_bytes < full_bytes
    assert lean_bytes / full_bytes < 0.9, "the saving should be material, not marginal"


def test_a_metadata_record_still_carries_its_justification(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The point of the lean mode is to drop data, never to drop reasoning.

    If METADATA shed justifications it would be a different product, not a cheaper one —
    and the overhead study's mode comparison would stop being a comparison.
    """
    monkeypatch.setenv(GRANULARITY_ENV, "METADATA")
    document, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    decisions = [a for a in document["activity"].values() if a.get("yp:verdict")]
    assert len(decisions) == 1
    assert decisions[0]["yp:justification"].strip()


# ── energy ────────────────────────────────────────────────────────────────────────
def test_energy_accounting_is_off_by_default(monkeypatch: pytest.MonkeyPatch) -> None:
    """Off because a sampling backend would contaminate every timing figure E6 reports."""
    from runtime.util import energy_tracker

    monkeypatch.delenv(ENERGY_ENV, raising=False)
    assert energy_tracker() is None


def test_the_null_backend_makes_an_absence_auditable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A measured zero labelled `null` is not the same claim as no measurement at all."""
    from runtime.util import energy_tracker

    monkeypatch.setenv(ENERGY_ENV, "null")
    tracker = energy_tracker()
    assert tracker is not None
    attributes = tracker.stop_carbon_tracked_block().to_attrs()
    assert attributes["yp:energy_source"] == "null"
    assert attributes["yp:energy_kwh"] == 0.0


def test_an_energy_figure_states_whether_it_can_be_attributed_to_the_step() -> None:
    """A sampling backend cannot attribute energy to a step shorter than its period.

    Measured on this demonstrator with codecarbon's 15-second default: a step of 0.000 s
    was handed 46.9 µWh while the rendering step that actually did the work was handed
    4.6 µWh. The figure is kept rather than dropped and the attribution denied instead.

    Attributability is independent of whether the watts were observed or estimated —
    `source` carries that, and the two can fail together. See
    `test_a_modelled_figure_is_never_labelled_measured`.
    """
    from yprov4agenticai import EnergyDelta

    coarse = EnergyDelta(1e-6, 1e-7, duration_s=0.002, sampling_period_s=15.0)
    fine = EnergyDelta(1e-6, 1e-7, duration_s=30.0, sampling_period_s=15.0)
    unsampled = EnergyDelta(0.0, 0.0, duration_s=0.002)

    assert not coarse.attributable
    assert fine.attributable
    assert unsampled.attributable, "a backend that does not sample has nothing to disclose"

    attrs = coarse.to_attrs()
    assert attrs["yp:energy_sampling_period_s"] == 15.0
    assert attrs["yp:energy_attributable"] is False
    assert "yp:energy_sampling_period_s" not in unsampled.to_attrs()


def test_a_modelled_figure_is_never_labelled_measured() -> None:
    """`source_label` must require a counter that is *delivering*, not merely selected.

    This has been wrong in both directions. It first probed only the Linux RAPL sysfs
    path, which reports "modelled" on every non-Linux host even where a real counter is
    in use. Trusting codecarbon's selected mode instead is the opposite trap: on the
    development host it selects `windows_emi`, and that interface returns a processor
    energy delta of 0.0 under a saturating load because it needs OEM firmware the machine
    does not have. A mode name alone would have labelled a fallback as an instrument
    reading, which is the error that quietly corrupts a published figure.
    """
    from yprov4agenticai.energy import CodeCarbonBackend

    backend = CodeCarbonBackend(country_iso_code="GBR")

    class _Hardware:
        def __init__(self, mode: str | None) -> None:
            self._mode = mode

    class _Tracker:
        def __init__(self, mode: str | None, energy: float) -> None:
            self._hardware = [_Hardware(mode)]
            self._total_energy = energy

    cases = [
        ("cpu_load", 1e-6, "modelled", "a load model is not an observation"),
        ("constant", 1e-6, "modelled", "a TDP constant is not an observation"),
        ("windows_emi", 0.0, "modelled", "an interface returning zero is not delivering"),
        ("intel_rapl", 0.0, "modelled", "RAPL present but unreadable falls back silently"),
        ("intel_rapl", 1e-6, "measured", "a readable counter with a reading is measured"),
        ("windows_emi", 1e-6, "measured", "EMI backed by real firmware is measured"),
    ]
    for mode, energy, expected, why in cases:
        backend._tracker = _Tracker(mode, energy)
        assert backend.source_label == expected, f"{mode}/{energy}: {why}"

    backend._tracker = None
    assert backend.source_label == "modelled", "no tracker means nothing was measured"


def test_the_first_step_is_not_charged_for_the_agent_idling(
    ca2: CA2Metrics, monkeypatch: pytest.MonkeyPatch
) -> None:
    """An energy tracker outlives any one record, so its first delta is not this record's.

    Before the baseline reset, 28.3 seconds of waiting between messages was attributed to
    "receive producer record" — the longest window in the graph, on the step that did the
    least.
    """
    monkeypatch.setenv(ENERGY_ENV, "null")
    from runtime.util import energy_tracker

    ca2.energy = energy_tracker()
    document, _ = ca2.process("s", _producer_document(), "producer.yprov4ml.s")

    windows = [
        a["yp:duration_s"] for a in document["activity"].values() if "yp:duration_s" in a
    ]
    assert len(windows) == len(document["activity"]), "every step carries a delta"
    assert windows[0] < 0.5, "and the first one is not the gap since the last message"
