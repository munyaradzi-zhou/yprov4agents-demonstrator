"""The energy study's arithmetic, on windows whose right answers can be worked out by hand."""

from __future__ import annotations

import pytest

from eval.bench_energy import ENERGY_CONDITIONS, _round_order, summarise


def _window(cpu_j: float, duration_s: float, sessions: int) -> dict[str, float]:
    return {"cpu_j": cpu_j, "ram_j": 10.0 * duration_s, "duration_s": duration_s,
            "sessions": sessions}  # fmt: skip


def _study(b_cpu: list[float]) -> dict[str, list[dict[str, float]]]:
    """Five rounds: idle at 5 W; OFF, A and B-TRANS at 15 J a session over 10 sessions."""
    rounds = len(b_cpu)
    work = [150.0, 152.0, 148.0, 151.0, 149.0][:rounds]
    return {
        "IDLE": [_window(50.0, 10.0, 0) for _ in range(rounds)],
        "OFF": [_window(cpu, 10.0, 10) for cpu in work],
        "A": [_window(cpu, 10.0, 10) for cpu in work],
        "B-TRANS": [_window(cpu, 10.0, 10) for cpu in work],
        "B": [_window(cpu, 10.0, 10) for cpu in b_cpu],
    }


def test_energy_above_idle_subtracts_what_the_machine_draws_anyway() -> None:
    summary = summarise(_study([150.0, 152.0, 148.0, 151.0, 149.0]))
    off = summary["conditions"]["OFF"]
    assert summary["idle_cpu_w"] == 5.0
    assert summary["ram_w"] == 10.0
    assert off["cpu_j_per_session"] == 15.0
    assert off["cpu_j_per_session_above_idle"] == 10.0  # 15 J less 5 W x 1 s


def test_a_difference_inside_the_spread_is_not_a_result() -> None:
    verdict = summarise(_study([151.0, 150.0, 149.0, 152.0, 148.0]))["conditions"]["B"]
    assert not verdict["vs_off"]["resolvable"]


def test_a_difference_well_outside_the_spread_is() -> None:
    verdict = summarise(_study([200.0, 202.0, 198.0, 201.0, 199.0]))["conditions"]["B"]
    assert verdict["vs_off"]["resolvable"]
    assert verdict["vs_off"]["pct"] == pytest.approx(50.0)
    # Gross per session: 20 J against 15 J, idle power included.
    assert verdict["vs_off_gross"]["pct"] == pytest.approx(100.0 * 5 / 15)


def test_too_few_rounds_give_no_verdict_rather_than_an_infinite_one() -> None:
    """One round has a spread of zero; that must not read as infinite resolution."""
    verdict = summarise(_study([300.0]))["conditions"]["B"]["vs_off"]
    assert verdict["margin"] is None
    assert not verdict["resolvable"]


def test_every_condition_leads_a_round_in_turn() -> None:
    leaders = [_round_order(index)[0] for index in range(len(ENERGY_CONDITIONS))]
    assert sorted(leaders) == sorted(ENERGY_CONDITIONS)
    assert all(sorted(_round_order(i)) == sorted(ENERGY_CONDITIONS) for i in range(7))


def test_the_report_labels_each_component_and_never_sums_them() -> None:
    from eval.bench_report import render_energy

    windows = _study([150.0, 152.0, 148.0, 151.0, 149.0])
    text = "\n".join(
        render_energy(
            {
                "rounds": 5,
                "sessions_per_window": 10,
                "windows": windows,
                "summary": summarise(windows),
                "source": {
                    "codecarbon": "3.3.1",
                    "sampling_period_s": 1.0,
                    "cpu_interface": "windows_emi",
                    "cpu_source": "measured",
                    "cpu_scope": "the whole processor package",
                    "ram_interface": "codecarbon RAM model",
                    "ram_source": "modelled",
                    "ram_power_w": 10.0,
                },
            }
        )
    )
    assert "| CPU | `windows_emi` | **measured** |" in text
    assert "| RAM | codecarbon RAM model | **modelled** |" in text
    assert "cannot resolve" in text
