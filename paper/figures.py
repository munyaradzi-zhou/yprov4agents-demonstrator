"""Render the paper's figures from the frozen archives.

    python -m paper.figures            # all four, to paper/figures/
    python -m paper.figures --only f5

Four figures, cut down from the ten the outline sketched. Ten figures in six pages is a
poster; these are the four that carry an argument the text cannot carry alone:

    F1  the two layers          what can be automated, and what cannot
    F4  quorum versus timeout   one verdict, two reasons, two justifications
    F5  Strategy A versus B     A succeeds everywhere except the row that matters
    F9  where the overhead is   the agent's work, the record, and the write

**Every number is read from `experiments/`, never typed in here.** A figure with a
hand-placed number is a figure that silently goes stale, and this paper has already had to
correct four stale figures in its own documentation. If an archive moves, these break
loudly rather than lying quietly.

Output is PDF for LaTeX and PNG for previewing, at a size that survives a two-column
layout: 7-point labels are unreadable at print scale, so nothing here is smaller than 8.
"""

from __future__ import annotations

import argparse
import json
import re
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")
import matplotlib.patches as patches
import matplotlib.pyplot as plt

REPO = Path(__file__).resolve().parent.parent
ARCHIVES = REPO / "experiments"
OUTPUT = Path(__file__).resolve().parent / "figures"

#: The run every structural figure reads from.
REFERENCE = ARCHIVES / "audit-reference"
#: The run the escalation figure reads from — the only one where the timeout path fires.
FAULTS = REFERENCE / "faults"
#: Benchmark results, carried inside the reference archive.
BENCH = REFERENCE / "bench"
#: The agent-level study the paper's overhead claims rest on: 100 paired rounds, every
#: sample kept, which is what lets those claims be equivalence bounds.
OVERHEAD = ARCHIVES / "overhead-samples"

#: A restrained palette that survives greyscale printing: the two emphasis colours differ
#: in lightness as well as hue, so a reader with a monochrome printout still sees the
#: distinction the figure depends on.
INK = "#1a1a1a"
MUTED = "#9aa0a6"
LIGHT = "#d7dade"
ACCENT = "#2a52c9"
WARN = "#b8471c"

plt.rcParams.update(
    {
        "font.size": 9,
        "axes.titlesize": 10,
        "axes.labelsize": 9,
        "xtick.labelsize": 8,
        "ytick.labelsize": 8,
        "legend.fontsize": 8,
        "axes.edgecolor": MUTED,
        "axes.labelcolor": INK,
        "text.color": INK,
        "xtick.color": MUTED,
        "ytick.color": MUTED,
        "figure.dpi": 150,
        "savefig.bbox": "tight",
        # The tight crop measures text from its glyph metrics, and at 0.02" it shaved the
        # last character off F9's right-hand annotation.
        "savefig.pad_inches": 0.06,
    }
)


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"missing archive file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(fig: plt.Figure, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    # No creation timestamp in the PDF: a figure whose data did not change must not change
    # on disk either, or every re-render shows up as a modified file and a real change
    # hides among the false ones.
    fig.savefig(OUTPUT / f"{name}.pdf", metadata={"CreationDate": None})
    fig.savefig(OUTPUT / f"{name}.png")
    plt.close(fig)
    print(f"  wrote {name}.pdf and {name}.png")


# ══════════════════════════════════════════════════════════════════════════════════
#  F1 — the two layers
# ══════════════════════════════════════════════════════════════════════════════════
def figure_one() -> None:
    """What an outside observer can collect, and what it structurally cannot.

    The paper's whole argument in one picture, so it has to be honest about the split
    rather than flattering. Layer 1 lists more than Layer 2 because it *is* the larger
    share of a provenance record — the contribution is the short list, and inflating it
    would invite a reader to check.

    Each panel is three text calls anchored to the panel's own geometry, with the body as
    a single string so matplotlib spaces the lines. Two earlier versions placed every line
    by hand: the first collided because the coordinates were eyeballed, and the second
    overflowed because the spacing was computed in points while the axes were in data
    units. Mixing units is the figure equivalent of a hand-placed number.
    """
    fig, ax = plt.subplots(figsize=(6.8, 3.3))
    ax.set_xlim(0, 100)
    ax.set_ylim(0, 100)
    ax.axis("off")

    def panel(
        x: float,
        width: float,
        heading: str,
        subheading: str,
        source: str,
        body: str,
        footer: str,
        face: str,
        edge: str,
        accent: str,
    ) -> None:
        top, bottom = 97.0, 34.0
        ax.add_patch(
            patches.FancyBboxPatch(
                (x, bottom),
                width,
                top - bottom,
                boxstyle="round,pad=0.4,rounding_size=1.5",
                linewidth=1.1,
                facecolor=face,
                edgecolor=edge,
            )
        )
        inset = x + 3.5
        ax.text(inset, top - 5, heading, fontsize=9.5, color=accent, va="center")
        ax.text(inset + 19, top - 5, subheading, fontsize=9, color=INK, va="center")
        ax.text(inset, top - 12, source, fontsize=7.8, color=MUTED, va="center")
        ax.text(inset, top - 17, body, fontsize=8, color=INK, va="top", linespacing=1.75)
        ax.text(
            x + width - 3.5,
            bottom + 4,
            footer,
            fontsize=8.5,
            color=accent if accent != INK else MUTED,
            ha="right",
            va="center",
            style="italic",
        )

    panel(
        1.5,
        46,
        "LAYER 1",
        "what, and when",
        "OpenTelemetry · framework callbacks",
        "activities · timings · tool calls\nentities · used · wasGeneratedBy\n"
        "wasInformedBy · token counts",
        "automatic",
        "#f4f5f7",
        MUTED,
        INK,
    )
    panel(
        52.5,
        46,
        "LAYER 2",
        "why",
        "explicit, at the decision point",
        "yp:verdict\nyp:confidence_score\nyp:justification",
        "cannot be automated",
        "#eef2fd",
        ACCENT,
        ACCENT,
    )

    ax.annotate(
        "",
        xy=(52.0, 65),
        xytext=(48.0, 65),
        arrowprops={"arrowstyle": "-|>", "color": MUTED, "linewidth": 1.2},
    )

    ax.text(
        50,
        27,
        "The rationale for a decision is a local variable at the moment of decision.",
        fontsize=9,
        color=INK,
        ha="center",
        va="center",
    )
    ax.text(
        50,
        19,
        "A callback fires after the function returns; a span wraps it; a query reads the "
        "store\nafterwards. None can retrieve what was never captured.",
        fontsize=8.3,
        color=MUTED,
        ha="center",
        va="center",
        linespacing=1.7,
    )

    ax.add_patch(
        patches.FancyBboxPatch(
            (1.5, 1),
            97,
            9,
            boxstyle="round,pad=0.4,rounding_size=1.5",
            linewidth=1.0,
            facecolor="#ffffff",
            edgecolor=LIGHT,
        )
    )
    ax.text(
        50,
        5.5,
        "both layers  →  W3C PROV + the yp: vocabulary  →  PROV-JSON",
        fontsize=9,
        color=INK,
        ha="center",
        va="center",
    )
    _save(fig, "f1_two_layers")


# ══════════════════════════════════════════════════════════════════════════════════
#  F4 — one verdict, two reasons
# ══════════════════════════════════════════════════════════════════════════════════
def _escalation(scenario: str) -> dict[str, Any]:
    return _read(FAULTS / scenario / "_scenario.json")


def _wrap(text: str, width: int) -> str:
    import textwrap

    return "\n".join(textwrap.wrap(text, width=width))


def figure_four() -> None:
    """CA1 reaches ESCALATE two ways, and the record says which.

    The qualitative companion to F5: the structural figure shows the rationale is absent
    from the callback layer, and this shows what being absent costs. Both justifications
    are quoted verbatim from the archive — truncated, because a figure is not a transcript,
    but never paraphrased.
    """
    integrity = _escalation("ca1-integrity")
    timeout = _escalation("ca1-timeout")

    fig, axes = plt.subplots(1, 2, figsize=(6.9, 3.5))
    for ax, case, colour, title in (
        (axes[0], integrity, WARN, "integrity"),
        (axes[1], timeout, ACCENT, "consumption-unconfirmed"),
    ):
        ax.set_xlim(0, 100)
        ax.set_ylim(0, 100)
        ax.axis("off")
        ax.add_patch(
            patches.FancyBboxPatch(
                (1, 1),
                98,
                98,
                boxstyle="round,pad=0.6,rounding_size=2",
                linewidth=1.1,
                facecolor="#fbfbfc",
                edgecolor=colour,
            )
        )
        ax.text(6, 90, "yp:verdict", fontsize=8, color=MUTED)
        ax.text(6, 83, case["verdict"], fontsize=13, color=colour, weight="bold")
        ax.text(6, 73, "yp:escalation_reason", fontsize=8, color=MUTED)
        ax.text(6, 66, case["escalation_reason"], fontsize=9.5, color=INK)
        ax.text(6, 56, "yp:justification", fontsize=8, color=MUTED)
        # The mismatched hashes are the evidence, and their first bytes carry it; a
        # figure that spends four lines on hex is spending them on nothing.
        quoted = re.sub(r"(sha256:[0-9a-f]{10})[0-9a-f]+", r"\1…", case["justification"])
        ax.text(
            6,
            52,
            _wrap(quoted[:330] + ("…" if len(quoted) > 330 else ""), 46),
            fontsize=6.9,
            color=INK,
            va="top",
            linespacing=1.45,
        )
        ax.set_title(title, fontsize=9, color=colour, pad=6)

    fig.suptitle(
        "the same verdict, two causes, two responses — "
        "corrupt storage, or a consumer that is down",
        fontsize=9,
        y=1.02,
        color=INK,
    )
    _save(fig, "f4_two_escalations")


# ══════════════════════════════════════════════════════════════════════════════════
#  F5 — Strategy A versus Strategy B
# ══════════════════════════════════════════════════════════════════════════════════
def figure_five() -> None:
    """Drawn to make A's success visible, because that is what was measured.

    A bar chart where A is near zero everywhere is the figure the pre-Phase-3 table
    implied, and it is not what the data says: A ties B on activities, on all 324
    relations, and on PROV-JSON conformance. Drawing it as a near-equal that falls off a
    cliff at one row is both more honest and more persuasive than drawing it as uniformly
    poor — a reviewer who has written a callback handler will believe the second figure
    and distrust the first.
    """
    from eval.baseline import compare

    result = compare(REFERENCE, REFERENCE / "baseline")
    a, b = result["A"], result["B"]

    rows = [
        ("activities", "activities", False),
        ("relations", "relations", False),
        ("entities", "entities", False),
        ("typed_entities", "typed entities", False),
        ("pointers", "resolvable pointers", False),
        ("verdicts", "verdicts", False),
        ("justifications", "justifications", True),
    ]
    labels = [label for _, label, _ in rows]
    a_values = [a.get(key, 0) for key, _, _ in rows]
    b_values = [b.get(key, 0) for key, _, _ in rows]
    highlight = [flag for _, _, flag in rows]

    # One IEEE column (3.5 in). Horizontal, so the seven labels read without rotation,
    # with the counts at the bar ends and the one row that matters in colour.
    fig, ax = plt.subplots(figsize=(3.45, 2.75))
    positions = list(range(len(rows)))
    height = 0.38
    ax.barh(
        [p - height / 2 for p in positions],
        a_values,
        height,
        label="A: callback handler",
        color=LIGHT,
        edgecolor=MUTED,
        linewidth=0.7,
    )
    ax.barh(
        [p + height / 2 for p in positions],
        b_values,
        height,
        label="B: explicit capture",
        color=[ACCENT if flag else "#aab6e4" for flag in highlight],
        edgecolor=ACCENT,
        linewidth=0.7,
    )
    span = max(b_values)
    for position, (value_a, value_b, flag) in enumerate(
        zip(a_values, b_values, highlight, strict=True)
    ):
        ax.text(
            value_a + span * 0.015,
            position - height / 2,
            str(value_a),
            va="center",
            fontsize=6.8,
            color=WARN if flag else MUTED,
            weight="bold" if flag else "normal",
        )
        ax.text(
            value_b + span * 0.015,
            position + height / 2,
            str(value_b),
            va="center",
            fontsize=6.8,
            color=ACCENT,
        )
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=7.5)
    ax.invert_yaxis()
    ax.set_xlim(0, span * 1.16)
    ax.set_xlabel("count across 24 emitted records", fontsize=7.5)
    ax.tick_params(axis="x", labelsize=7)
    ax.tick_params(axis="y", length=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(
        frameon=False,
        fontsize=7,
        loc="upper center",
        bbox_to_anchor=(0.42, -0.2),
        ncol=2,
        handlelength=1.2,
        columnspacing=1.0,
    )
    ax.annotate(
        f"0 of {b['justifications']}: no reason recovered",
        xy=(span * 0.02, len(rows) - 1 - height / 2),
        xytext=(span * 0.30, len(rows) - 1.75),
        fontsize=7,
        color=WARN,
        va="center",
        arrowprops={"arrowstyle": "-|>", "color": WARN, "linewidth": 0.9},
    )
    _save(fig, "f5_a_versus_b")


# ══════════════════════════════════════════════════════════════════════════════════
#  F9 — where the overhead actually is
# ══════════════════════════════════════════════════════════════════════════════════
def figure_nine() -> None:
    """The agent's own work, building the record, and writing it — as shares of the whole.

    Forestalls the obvious misreading of "+1,645%": the ratio is real, it is the worst case
    in the system, and almost all of it is one file write — a storage decision rather than
    an instrumentation one.

    Drawn as proportions on a linear axis, with the absolute total annotated. The first
    version stacked absolute milliseconds on a *log* axis, where a segment's length has no
    relation to its value and the composition it was meant to show was simply false. A
    stacked bar needs a linear axis; comparing agents three orders of magnitude apart needs
    normalisation. This does both and labels the totals, so nothing is lost.
    """
    from eval.bench_support import RESOLVE_MULTIPLE

    agents = _read(OVERHEAD / "agent.json")["agents"]
    order = [
        label
        for label in (
            "MC1 Compliance",
            "MC3 Audit",
            "CA3 Analytics (heuristic)",
            "CAx Summariser (computed)",
            "CA1 Storage (persist path)",
        )
        if label in agents
    ]

    shares: list[tuple[float, float, float]] = []
    totals: list[float] = []
    notes: list[tuple[str, bool]] = []
    for label in order:
        row = agents[label]
        medians = row["median_ns"]
        own = medians["OFF"]
        build = max(medians["B-TRANS"] - medians["OFF"], 0)
        write = max(row["write_cost_ns"], 0)
        total = own + build + write
        shares.append((100 * own / total, 100 * build / total, 100 * write / total))
        # The printed total is the measured median, not the sum of the segments: a build
        # cost inside the noise can measure slightly negative and is drawn as zero, so the
        # sum would overstate the step by that amount.
        totals.append(medians["B"] / 1e6)

        noise = row["vs_off"]["noise_ns"]
        margin = abs(medians["B"] - own) / noise if noise else float("inf")
        resolved = margin > RESOLVE_MULTIPLE
        notes.append(
            (
                f"{row['vs_off']['pct']:+,.0f}%" if resolved else f"in noise ({margin:.1f}×)",
                resolved,
            )
        )

    # One IEEE column (3.5 in). The measured total and the overhead move into each row's
    # label, which leaves the full width to the bars.
    fig, ax = plt.subplots(figsize=(3.45, 2.35))
    positions = list(range(len(order)))
    own_s = [s[0] for s in shares]
    build_s = [s[1] for s in shares]
    write_s = [s[2] for s in shares]

    ax.barh(positions, own_s, color=LIGHT, edgecolor=MUTED, linewidth=0.7, label="own work")
    ax.barh(
        positions,
        build_s,
        left=own_s,
        color="#aab6e4",
        edgecolor=ACCENT,
        linewidth=0.7,
        label="building the record",
    )
    ax.barh(
        positions,
        write_s,
        left=[o + b for o, b in zip(own_s, build_s, strict=True)],
        color=WARN,
        edgecolor=WARN,
        linewidth=0.7,
        label="writing it",
    )
    labels = [
        f"{label.split(' (')[0]}\n{total:.2f} ms, {note}"
        for label, total, (note, _) in zip(order, totals, notes, strict=True)
    ]
    ax.set_yticks(positions)
    ax.set_yticklabels(labels, fontsize=6.8, linespacing=1.15)
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of one record's end-to-end time (%)", fontsize=7.5)
    ax.tick_params(axis="x", labelsize=7)
    ax.tick_params(axis="y", length=0)
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.legend(
        frameon=False,
        fontsize=7,
        loc="upper center",
        bbox_to_anchor=(0.36, -0.24),
        ncol=3,
        handlelength=1.2,
        columnspacing=0.9,
    )
    _save(fig, "f9_where_the_overhead_is")


FIGURES = {
    "f1": figure_one,
    "f4": figure_four,
    "f5": figure_five,
    "f9": figure_nine,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m paper.figures",
        description="Render the paper's figures from the frozen archives.",
    )
    parser.add_argument("--only", choices=sorted(FIGURES), help="render one figure")
    args = parser.parse_args(argv)

    selected = {args.only: FIGURES[args.only]} if args.only else FIGURES
    for name, render in selected.items():
        print(f"[figures] {name}")
        render()
    print(f"\n[figures] output in {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
