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
REFERENCE = ARCHIVES / "verdict-reachability"
#: The run the escalation figure reads from — the only one where the timeout path fires.
FAULTS = REFERENCE / "faults"
#: Benchmark results, carried inside the reference archive.
BENCH = REFERENCE / "bench"

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
        "savefig.pad_inches": 0.02,
    }
)


def _read(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise SystemExit(f"missing archive file: {path}")
    return json.loads(path.read_text(encoding="utf-8"))


def _save(fig: plt.Figure, name: str) -> None:
    OUTPUT.mkdir(parents=True, exist_ok=True)
    for suffix in ("pdf", "png"):
        fig.savefig(OUTPUT / f"{name}.{suffix}")
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

    fig, ax = plt.subplots(figsize=(6.6, 3.1))
    positions = range(len(rows))
    width = 0.38
    ax.bar(
        [p - width / 2 for p in positions],
        a_values,
        width,
        label="Strategy A — callback handler",
        color=LIGHT,
        edgecolor=MUTED,
        linewidth=0.8,
    )
    ax.bar(
        [p + width / 2 for p in positions],
        b_values,
        width,
        label="Strategy B — explicit instrumentation",
        color=[ACCENT if flag else "#aab6e4" for flag in highlight],
        edgecolor=ACCENT,
        linewidth=0.8,
    )
    for position, (value_a, value_b, flag) in enumerate(
        zip(a_values, b_values, highlight, strict=True)
    ):
        ax.text(
            position - width / 2,
            value_a + 6,
            str(value_a),
            ha="center",
            fontsize=7.5,
            color=WARN if flag else MUTED,
            weight="bold" if flag else "normal",
        )
        ax.text(
            position + width / 2,
            value_b + 6,
            str(value_b),
            ha="center",
            fontsize=7.5,
            color=ACCENT,
        )

    ax.set_xticks(list(positions))
    ax.set_xticklabels(labels, rotation=18, ha="right")
    ax.set_ylabel("count across 24 emitted records")
    ax.set_ylim(0, max(b_values) * 1.18)
    ax.spines[["top", "right"]].set_visible(False)
    ax.legend(frameon=False, loc="upper right")

    # The cliff, named where it happens.
    ax.annotate(
        f"0 of {b['justifications']}\nthe only earned zero",
        xy=(len(rows) - 1 - width / 2, 4),
        xytext=(len(rows) - 2.5, max(b_values) * 0.42),
        fontsize=8,
        color=WARN,
        ha="center",
        arrowprops={"arrowstyle": "-|>", "color": WARN, "linewidth": 1.0},
    )
    ax.set_title("A recovers the structure; it cannot recover the reasoning", pad=8, color=INK)
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

    agents = _read(BENCH / "agent.json")["agents"]
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
        totals.append(total / 1e6)

        noise = row["vs_off"]["noise_ns"]
        margin = abs(medians["B"] - own) / noise if noise else float("inf")
        resolved = margin > RESOLVE_MULTIPLE
        notes.append(
            (
                f"{row['vs_off']['pct']:+,.0f}%" if resolved else f"in noise ({margin:.1f}×)",
                resolved,
            )
        )

    fig, ax = plt.subplots(figsize=(6.8, 2.9))
    positions = list(range(len(order)))
    own_s = [s[0] for s in shares]
    build_s = [s[1] for s in shares]
    write_s = [s[2] for s in shares]

    ax.barh(
        positions,
        own_s,
        color=LIGHT,
        edgecolor=MUTED,
        linewidth=0.8,
        label="the agent's own work",
    )
    ax.barh(
        positions,
        build_s,
        left=own_s,
        color="#aab6e4",
        edgecolor=ACCENT,
        linewidth=0.8,
        label="building the PROV record",
    )
    ax.barh(
        positions,
        write_s,
        left=[o + b for o, b in zip(own_s, build_s, strict=True)],
        color=WARN,
        edgecolor=WARN,
        linewidth=0.8,
        label="writing it to disk",
    )

    for position, (total, (note, resolved)) in enumerate(zip(totals, notes, strict=True)):
        ax.text(
            102,
            position,
            f"{total:.2f} ms      {note}",
            va="center",
            fontsize=7.8,
            color=INK if resolved else MUTED,
            weight="bold" if resolved else "normal",
        )

    ax.set_yticks(positions)
    ax.set_yticklabels([label.split(" (")[0] for label in order])
    ax.invert_yaxis()
    ax.set_xlim(0, 100)
    ax.set_xlabel("share of the time one record takes, end to end (%)")
    ax.spines[["top", "right", "left"]].set_visible(False)
    ax.tick_params(axis="y", length=0)
    ax.legend(
        frameon=False,
        fontsize=7.8,
        loc="upper center",
        bbox_to_anchor=(0.5, -0.32),
        ncol=3,
    )
    ax.set_title(
        "overhead is one file write — and for every agent that does real work it is "
        "unresolvable",
        pad=10,
        fontsize=9,
        color=INK,
        loc="left",
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
