"""Architecture diagrams for the paper: drawn, not measured.

    python -m paper.diagrams            # writes paper/figures/d*.pdf and .png

Unlike :mod:`paper.figures`, nothing here is read from an archive: these are pictures of the
system's structure, so they are drawn to its design. They share that module's palette and
save routine, so a diagram and a data figure placed side by side read as one set.

Three are offered, to choose from:

    da_architecture_wide     the whole system, left to right — a full-width figure
    db_architecture_column   the same, top to bottom — fits one column
    dc_lifecycle             the six-step lifecycle, and the one step an observer cannot see
"""

from __future__ import annotations

import argparse
from collections.abc import Callable

import matplotlib.pyplot as plt
from matplotlib.patches import Ellipse, FancyArrowPatch, FancyBboxPatch, Rectangle

from paper.figures import ACCENT, INK, LIGHT, MUTED, OUTPUT, WARN, _save

#: Fills. The group tints differ in lightness as well as hue, so the operational and meta
#: layers stay distinct on a greyscale printout.
TINT = "#eef1f6"
CARD = "#ffffff"
META = INK
BUS = ACCENT


def _canvas(width: float, height: float) -> tuple[plt.Figure, plt.Axes]:
    """A figure whose data coordinates are inches, so geometry reads as page geometry."""
    fig, ax = plt.subplots(figsize=(width, height))
    ax.set_xlim(0, width)
    ax.set_ylim(0, height)
    ax.set_aspect("equal")
    ax.axis("off")
    fig.subplots_adjust(0, 0, 1, 1)
    return fig, ax


def _box(
    ax: plt.Axes,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str,
    edge: str | None = None,
    radius: float = 0.06,
) -> None:
    ax.add_patch(
        FancyBboxPatch(
            (x, y),
            w,
            h,
            boxstyle=f"round,pad=0,rounding_size={radius}",
            facecolor=fill,
            edgecolor=edge or fill,
            linewidth=0.8,
        )
    )


def _label(
    ax: plt.Axes,
    x: float,
    y: float,
    title: str,
    detail: str = "",
    *,
    color: str = INK,
    detail_color: str = MUTED,
    size: float = 7.5,
    align: str = "center",
) -> None:
    """A bold title with an optional smaller line beneath, centred on (x, y)."""
    if detail:
        ax.text(
            x,
            y + 0.075,
            title,
            ha=align,
            va="center",
            fontsize=size,
            weight="bold",
            color=color,
        )
        ax.text(
            x, y - 0.085, detail, ha=align, va="center", fontsize=size - 1.4, color=detail_color
        )
    else:
        ax.text(x, y, title, ha=align, va="center", fontsize=size, weight="bold", color=color)


def _arrow(
    ax: plt.Axes,
    start: tuple[float, float],
    end: tuple[float, float],
    *,
    color: str = MUTED,
    dashed: bool = False,
) -> None:
    ax.add_patch(
        FancyArrowPatch(
            start,
            end,
            arrowstyle="-|>",
            mutation_scale=8,
            color=color,
            linewidth=1.0,
            linestyle=(0, (3, 2)) if dashed else "-",
            shrinkA=0,
            shrinkB=0,
        )
    )


def _store(ax: plt.Axes, x: float, y: float, w: float, h: float) -> None:
    """yProvStore as a cylinder: the conventional mark for a store."""
    cap = 0.09
    ax.add_patch(
        Rectangle((x, y + cap / 2), w, h - cap, facecolor=TINT, edgecolor=MUTED, linewidth=0.8)
    )
    ax.add_patch(
        Ellipse(
            (x + w / 2, y + cap / 2), w, cap, facecolor=TINT, edgecolor=MUTED, linewidth=0.8
        )
    )
    # The top cap is drawn last and filled, hiding the rectangle's upper edge.
    ax.add_patch(
        Ellipse(
            (x + w / 2, y + h - cap / 2), w, cap, facecolor=TINT, edgecolor=MUTED, linewidth=0.8
        )
    )
    _label(ax, x + w / 2, y + h / 2 - 0.02, "yProvStore", "pid + SHA-256", size=7.5)


OPERATIONAL = (
    ("CAx  Summariser", "LLM summary, figures checked"),
    ("CA2  Metrics", "charts from the record"),
    ("CA3  Analytics", "five mining algorithms"),
    ("CA1  Storage", "persists after consumers use it"),
)
META_AGENTS = (
    ("MC1  Compliance", "checks the standard"),
    ("MC3  Audit", "rebuilds the trail"),
)


# ══════════════════════════════════════════════════════════════════════════════════
#  A — the whole system, left to right
# ══════════════════════════════════════════════════════════════════════════════════
def architecture_wide() -> None:
    """Producer, provenance bus, operational agents, meta agents; yProvStore behind CA1."""
    width, height = 7.0, 2.75
    fig, ax = _canvas(width, height)
    top, bottom = 2.6, 0.15

    # Producer
    _box(ax, 0.05, 1.0, 1.05, 0.75, fill=INK)
    _label(ax, 0.575, 1.375, "Producer", "replays yProv4ML", color="white", detail_color=LIGHT)
    _arrow(ax, (1.1, 1.375), (1.35, 1.375))

    # Provenance bus
    _box(ax, 1.35, bottom, 0.45, top - bottom, fill=BUS, radius=0.08)
    ax.text(
        1.575,
        (top + bottom) / 2,
        "Provenance bus",
        rotation=90,
        ha="center",
        va="center",
        fontsize=8,
        weight="bold",
        color="white",
    )
    _arrow(ax, (1.8, 1.375), (2.0, 1.375))

    # Operational agents
    _box(ax, 2.0, bottom, 2.05, top - bottom, fill=TINT, radius=0.08)
    ax.text(
        3.025,
        top - 0.15,
        "Operational agents: do the work",
        ha="center",
        va="center",
        fontsize=7.5,
        weight="bold",
        color=INK,
    )
    card_h, gap = 0.44, 0.07
    for index, (name, role) in enumerate(OPERATIONAL):
        y = top - 0.36 - card_h - index * (card_h + gap)
        _box(ax, 2.1, y, 1.85, card_h, fill=CARD, edge=LIGHT)
        _label(ax, 3.025, y + card_h / 2, name, role, size=7.2)
    ca1_y = top - 0.36 - card_h - 3 * (card_h + gap) + card_h / 2

    # Each agent's own PROV, to the meta layer
    _arrow(ax, (4.05, 1.9), (4.75, 1.9), color=INK)
    ax.text(
        4.4,
        2.05,
        "each agent's\nown PROV",
        ha="center",
        va="bottom",
        fontsize=6.5,
        color=MUTED,
        linespacing=1.1,
    )

    # Meta agents
    _box(ax, 4.75, 1.05, 1.45, top - 1.05, fill=META, radius=0.08)
    ax.text(
        5.475,
        top - 0.15,
        "Meta agents: govern",
        ha="center",
        va="center",
        fontsize=7.5,
        weight="bold",
        color="white",
    )
    for index, (name, role) in enumerate(META_AGENTS):
        y = top - 0.36 - card_h - index * (card_h + gap)
        _box(ax, 4.83, y, 1.29, card_h, fill="#2b3c5c")
        _label(
            ax, 5.475, y + card_h / 2, name, role, color="white", detail_color=LIGHT, size=7.2
        )

    # Governance: nobody subscribes
    _arrow(ax, (6.2, 1.9), (6.45, 1.9), color=INK)
    ax.text(
        6.48,
        1.9,
        "governance\n(no subscriber:\none level deep)",
        ha="left",
        va="center",
        fontsize=6.3,
        color=MUTED,
        linespacing=1.15,
    )

    # yProvStore, behind CA1 — written through its API, never over the bus
    _store(ax, 4.75, bottom + 0.05, 1.45, 0.62)
    _arrow(ax, (3.95, ca1_y), (4.75, ca1_y), dashed=True)
    ax.text(
        4.35, ca1_y + 0.09, "store API", ha="center", va="bottom", fontsize=6.3, color=MUTED
    )

    _save(fig, "da_architecture_wide")


# ══════════════════════════════════════════════════════════════════════════════════
#  B — the same, top to bottom, for one column
# ══════════════════════════════════════════════════════════════════════════════════
def architecture_column() -> None:
    """The architecture stacked vertically, sized for a single 3.5-inch column."""
    width, height = 3.45, 4.35
    fig, ax = _canvas(width, height)
    left, right = 0.05, width - 0.05
    span = right - left

    # Producer
    _box(ax, 0.85, 3.85, 1.75, 0.45, fill=INK)
    _label(
        ax,
        1.725,
        4.075,
        "Producer",
        "replays yProv4ML records",
        color="white",
        detail_color=LIGHT,
    )
    _arrow(ax, (1.725, 3.85), (1.725, 3.68))

    # Provenance bus
    _box(ax, left, 3.38, span, 0.3, fill=BUS, radius=0.08)
    ax.text(
        width / 2,
        3.53,
        "Provenance bus",
        ha="center",
        va="center",
        fontsize=8,
        weight="bold",
        color="white",
    )
    _arrow(ax, (1.725, 3.38), (1.725, 3.2))

    # Operational agents, 2 x 2
    _box(ax, left, 1.95, span, 1.25, fill=TINT, radius=0.08)
    ax.text(
        width / 2,
        3.07,
        "Operational agents: do the work",
        ha="center",
        va="center",
        fontsize=7.5,
        weight="bold",
        color=INK,
    )
    card_w, card_h = (span - 0.3) / 2, 0.44
    positions = []
    for index, (name, role) in enumerate(OPERATIONAL):
        col, row = index % 2, index // 2
        x = left + 0.1 + col * (card_w + 0.1)
        y = 2.48 - row * (card_h + 0.06)
        positions.append((x, y))
        _box(ax, x, y, card_w, card_h, fill=CARD, edge=LIGHT)
        _label(ax, x + card_w / 2, y + card_h / 2, name, role, size=6.8)

    # Each agent's own PROV, down to the meta layer
    _arrow(ax, (1.0, 1.95), (1.0, 1.62), color=INK)
    ax.text(
        1.08, 1.785, "each agent's own PROV", ha="left", va="center", fontsize=6.3, color=MUTED
    )

    # Meta agents
    _box(ax, left, 0.55, 2.1, 1.07, fill=META, radius=0.08)
    ax.text(
        left + 1.05,
        1.49,
        "Meta agents: govern",
        ha="center",
        va="center",
        fontsize=7.5,
        weight="bold",
        color="white",
    )
    for index, (name, role) in enumerate(META_AGENTS):
        y = 1.0 - index * 0.41
        _box(ax, left + 0.08, y, 1.94, 0.36, fill="#2b3c5c")
        _label(
            ax, left + 1.05, y + 0.18, name, role, color="white", detail_color=LIGHT, size=6.8
        )
    ax.text(
        left + 1.05,
        0.38,
        "governance: no subscriber, one level deep",
        ha="center",
        va="center",
        fontsize=6.0,
        color=MUTED,
    )

    # yProvStore, behind CA1 (bottom-right card)
    ca1_x, ca1_y = positions[3]
    _store(ax, 2.3, 0.55, 1.1, 0.7)
    _arrow(ax, (ca1_x + card_w / 2, ca1_y), (2.85, 1.25), dashed=True)
    ax.text(2.95, 1.6, "store API", ha="left", va="center", fontsize=6.0, color=MUTED)

    _save(fig, "db_architecture_column")


# ══════════════════════════════════════════════════════════════════════════════════
#  C — the lifecycle, and the one step an observer cannot see
# ══════════════════════════════════════════════════════════════════════════════════
def lifecycle() -> None:
    """Six typed steps; five are observable from outside, the decision's rule is not."""
    width, height = 3.45, 1.45
    fig, ax = _canvas(width, height)
    steps = ("Receive", "Plan", "Act", "Observe", "Decide", "Respond")
    box_w, gap, y = 0.48, 0.084, 0.62
    for index, step in enumerate(steps):
        x = 0.05 + index * (box_w + gap)
        decide = step == "Decide"
        _box(
            ax, x, y, box_w, 0.36, fill=WARN if decide else TINT, edge=WARN if decide else LIGHT
        )
        ax.text(
            x + box_w / 2,
            y + 0.18,
            step,
            ha="center",
            va="center",
            fontsize=5.9,
            weight="bold",
            color="white" if decide else INK,
        )
        ax.text(
            x + box_w / 2,
            y - 0.1,
            f"S{index + 1}",
            ha="center",
            va="center",
            fontsize=6,
            color=MUTED,
        )
        if index < len(steps) - 1:
            _arrow(ax, (x + box_w, y + 0.18), (x + box_w + gap, y + 0.18))

    decide_x = 0.05 + 4 * (box_w + gap) + box_w / 2
    ax.text(
        width / 2,
        1.27,
        "observable by callbacks and traces: type, time, inputs, outputs",
        ha="center",
        va="center",
        fontsize=6.4,
        color=MUTED,
    )
    ax.plot([0.05, width - 0.05], [1.13, 1.13], color=LIGHT, linewidth=0.8)
    ax.annotate(
        "captured at the call site: verdict + yp:justification",
        xy=(decide_x, y - 0.18),
        xytext=(decide_x - 0.25, 0.14),
        ha="center",
        va="center",
        fontsize=6.4,
        color=WARN,
        weight="bold",
        arrowprops={"arrowstyle": "-|>", "color": WARN, "linewidth": 0.9, "mutation_scale": 7},
    )
    _save(fig, "dc_lifecycle")


DIAGRAMS: dict[str, Callable[[], None]] = {
    "da_architecture_wide": architecture_wide,
    "db_architecture_column": architecture_column,
    "dc_lifecycle": lifecycle,
}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m paper.diagrams", description="Draw the paper's architecture diagrams."
    )
    parser.add_argument("--only", choices=sorted(DIAGRAMS), help="draw one diagram")
    args = parser.parse_args(argv)
    for name, draw in ({args.only: DIAGRAMS[args.only]} if args.only else DIAGRAMS).items():
        print(f"[diagrams] {name}")
        draw()
    print(f"\n[diagrams] output in {OUTPUT}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
