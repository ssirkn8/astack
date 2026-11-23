"""Build the synthetic research-visuals example. Every value here is made up.

Run from a project root that has the astack skills:
    python build.py figures --out .
    python build.py prototypes --out .
    python build.py diagram --out . --selected C
    python build.py standalone --out standalone
    python build.py qa --out .
"""

from __future__ import annotations

import argparse
import csv
import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib  # type: ignore[import-not-found,unused-ignore]
import matplotlib.colors  # type: ignore[import-not-found,unused-ignore]

matplotlib.use("Agg")
import matplotlib.image as mpimg  # type: ignore[import-not-found,unused-ignore]  # noqa: E402
import matplotlib.pyplot as plt  # type: ignore[import-not-found,unused-ignore]  # noqa: E402
import numpy as np  # type: ignore[import-not-found,unused-ignore]  # noqa: E402
from matplotlib.patches import (  # type: ignore[import-not-found,unused-ignore]  # noqa: E402
    Polygon,
    Rectangle,
)

STYLE = Path(".opencode/skills/research-visuals/journal-serif.mplstyle")
SITES = ["North Bay", "Mill Inlet", "Center Basin", "Reed Shore", "South Arm", "Outflow"]
INK = "#1F2933"
FILLS = {"data": "#DCEAF6", "process": "#FCE8D5", "product": "#C9DDF0"}
SVG_META = {"Date": None, "Creator": None}

NODES = {
    "sensors": ("data", "Synthetic monthly\noxygen series"),
    "qc": ("process", "Organize series\nby site"),
    "plot": ("process", "Plot monthly\nseries"),
    "fit": ("process", "Fit seasonal\ntrend"),
    "lab": ("data", "Synthetic paired\noxygen values"),
    "validate": ("process", "Assemble trend and\nagreement panels"),
    "trends": ("product", "Report figures\nand trend table"),
}
EDGES = (
    ("sensors", "qc", ""),
    ("qc", "fit", ""),
    ("qc", "plot", ""),
    ("fit", "validate", ""),
    ("plot", "validate", ""),
    ("lab", "validate", ""),
    ("validate", "trends", ""),
)


@dataclass(frozen=True)
class Layout:
    size: tuple[float, float]
    node: tuple[float, float]
    places: dict[str, tuple[float, float]]
    ports: dict[tuple[str, str], tuple[str, str]]
    groups: tuple[tuple[str, float, float, float, float], ...] = ()


LAYOUTS = {
    "A": Layout(
        (6.0, 7.7),
        (1.5, 0.52),
        {
            "sensors": (3.0, 6.9),
            "qc": (3.0, 5.85),
            "fit": (2.0, 4.45),
            "plot": (4.0, 4.45),
            "lab": (1.05, 2.85),
            "validate": (3.0, 2.85),
            "trends": (3.0, 1.15),
        },
        {
            ("sensors", "qc"): ("s", "n"),
            ("qc", "fit"): ("s", "n"),
            ("qc", "plot"): ("s", "n"),
            ("fit", "validate"): ("s", "n"),
            ("plot", "validate"): ("s", "n"),
            ("lab", "validate"): ("e", "w"),
            ("validate", "trends"): ("s", "n"),
        },
        (
            ("Data preparation", 0.15, 5.3, 5.85, 7.55),
            ("Analysis and panels", 0.15, 2.35, 5.85, 5.12),
            ("Output", 0.15, 0.7, 5.85, 2.0),
        ),
    ),
    "B": Layout(
        (7.4, 3.5),
        (0.96, 0.56),
        {
            "sensors": (0.95, 2.25),
            "qc": (2.55, 2.25),
            "fit": (4.31, 2.25),
            "validate": (5.52, 2.25),
            "trends": (6.73, 2.25),
            "plot": (4.31, 0.85),
            "lab": (5.52, 0.85),
        },
        {
            ("sensors", "qc"): ("e", "w"),
            ("qc", "fit"): ("e", "w"),
            ("qc", "plot"): ("s", "w"),
            ("fit", "validate"): ("e", "w"),
            ("plot", "validate"): ("e", "s"),
            ("lab", "validate"): ("n", "s"),
            ("validate", "trends"): ("e", "w"),
        },
        (
            ("Prepare", 0.08, 0.35, 3.68, 3.25),
            ("Analyze", 3.76, 0.35, 6.10, 3.25),
            ("Report", 6.18, 0.35, 7.32, 3.25),
        ),
    ),
    "C": Layout(
        (7.4, 4.7),
        (1.25, 0.54),
        {
            "sensors": (0.75, 3.95),
            "qc": (2.6, 3.95),
            "fit": (5.0, 3.95),
            "plot": (2.6, 2.95),
            "lab": (2.9, 1.75),
            "validate": (5.0, 2.2),
            "trends": (5.0, 0.65),
        },
        {
            ("sensors", "qc"): ("e", "w"),
            ("qc", "fit"): ("e", "w"),
            ("qc", "plot"): ("s", "n"),
            ("fit", "validate"): ("s", "n"),
            ("plot", "validate"): ("e", "w"),
            ("lab", "validate"): ("e", "w"),
            ("validate", "trends"): ("s", "n"),
        },
    ),
}


def _style() -> None:
    plt.style.use(STYLE)
    plt.rcParams["svg.hashsalt"] = "research-visuals"


def _save(fig: Any, stem: Path) -> None:
    stem.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(stem.with_suffix(".svg"), metadata=SVG_META)
    fig.savefig(stem.with_suffix(".png"), dpi=300)
    plt.close(fig)


def _synthetic() -> dict[str, Any]:
    rng = np.random.default_rng(296300)
    months = np.arange(60)
    truth = np.array([1.8, 1.4, 1.0, 0.7, 0.3, -0.1]) / 120
    base = np.array([7.2, 7.0, 6.8, 6.7, 6.4, 6.3])
    season = 1.4 * np.sin(2 * np.pi * (months - 3) / 12)
    series = np.array(
        [
            b + m * months + season * (1 - 0.04 * i) + rng.normal(0, 0.2, 60)
            for i, (b, m) in enumerate(zip(base, truth, strict=True))
        ]
    )
    design = np.column_stack(
        [np.ones(60), months, np.sin(2 * np.pi * months / 12), np.cos(2 * np.pi * months / 12)]
    )
    inverse = np.linalg.inv(design.T @ design)
    slopes, intervals = [], []
    for values in series:
        beta = inverse @ design.T @ values
        variance = np.sum((values - design @ beta) ** 2) / (60 - 4)
        slopes.append(float(beta[1] * 120))
        intervals.append(float(2.004 * np.sqrt(variance * inverse[1, 1]) * 120))
    observed = rng.uniform(4, 11, 140)
    modeled = 0.35 + 0.95 * observed + rng.normal(0, 0.34, 140)
    return {
        "months": months,
        "series": series,
        "slopes": slopes,
        "intervals": intervals,
        "observed": observed,
        "modeled": modeled,
        "rmse": float(np.sqrt(np.mean((modeled - observed) ** 2))),
    }


def _scatter(ax: Any, data: dict[str, Any]) -> None:
    ax.scatter(data["observed"], data["modeled"], s=9, facecolor="none", edgecolor="#2B5F85")
    ax.plot([3.5, 12], [3.5, 12], color="#B5482F", ls="--", label="1:1 line")
    ax.set(
        xlim=(3.5, 12),
        ylim=(3.5, 12),
        xlabel="Observed oxygen (mg/L)",
        ylabel="Modeled oxygen (mg/L)",
        aspect="equal",
    )
    ax.legend(loc="upper left")


def build_figures(out: Path) -> None:
    _style()
    data = _synthetic()
    fig, (left, right) = plt.subplots(
        1, 2, figsize=(7.1, 2.9), width_ratios=[1.35, 1], constrained_layout=True
    )
    left.plot(data["months"], data["series"][0], label=SITES[0], marker="o", ms=2, markevery=4)
    left.plot(
        data["months"], data["series"][4], label=SITES[4], ls="--", marker="s", ms=2, markevery=4
    )
    left.set_xticks(range(0, 61, 12), [str(2020 + i) for i in range(6)])
    left.set(xlabel="Year", ylabel="Dissolved oxygen (mg/L)", ylim=(4.2, 11.8))
    left.legend(loc="upper left", ncol=2)
    left.set_title("(a) Monthly series", loc="left")
    positions = np.arange(6)[::-1]
    right.errorbar(data["slopes"], positions, xerr=data["intervals"], fmt="o", ms=3.4, capsize=1.6)
    right.axvline(0, color="#3F444A", lw=0.7, ls=":")
    right.set_yticks(positions, SITES)
    right.set_xlabel("Trend (mg/L per decade)")
    right.set_title("(b) Site trends", loc="left")
    _save(fig, out / "reports/figures/lake/trends")

    fig, ax = plt.subplots(figsize=(3.5, 3.3), constrained_layout=True)
    _scatter(ax, data)
    _save(fig, out / "reports/figures/lake/validation")

    # Shrinking the full figure would leave unreadable axis text, so the card labels this one.
    fig = plt.figure(figsize=(1.3, 1.3))
    ax = fig.add_axes((0.02, 0.02, 0.96, 0.96))
    ax.scatter(data["observed"], data["modeled"], s=3, facecolor="none", edgecolor="#2B5F85")
    ax.plot([3.5, 12], [3.5, 12], color="#B5482F", ls="--", lw=1.1, zorder=3)
    ax.set(xlim=(3.5, 12), ylim=(3.5, 12), xticks=[], yticks=[], aspect="equal")
    ax.grid(False)
    fig.savefig(out / "reports/figures/lake/validation-thumbnail.png", dpi=300)
    plt.close(fig)

    rows = [
        (site, f"{slope:.2f}", f"{interval:.2f}")
        for site, slope, interval in zip(SITES, data["slopes"], data["intervals"], strict=True)
    ]
    evidence = out / "evidence/lake"
    evidence.mkdir(parents=True, exist_ok=True)
    with (evidence / "site_trends.csv").open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle, lineterminator="\n")
        writer.writerow(["site", "trend_mg_l_per_decade", "ci95_half_width_mg_l_per_decade"])
        writer.writerows(rows)

    table = out / "reports/tables/lake/site_trends.md"
    table.parent.mkdir(parents=True, exist_ok=True)
    lines = [
        "**Table 2. Decadal oxygen trends differ across six synthetic sites.**",
        "",
        "| Site | Trend (mg/L per decade) | 95% CI half-width (mg/L per decade) |",
        "|---|---:|---:|",
        *(f"| {site} | {slope} | {interval} |" for site, slope, interval in rows),
    ]
    table.write_text("\n".join(lines) + "\n", encoding="utf-8")

    metrics = {
        "synthetic": True,
        "n_validation": int(data["observed"].size),
        "rmse": round(data["rmse"], 4),
        "top_trend": round(data["slopes"][0], 4),
    }
    (evidence / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    records = {
        "monthly-oxygen": int(data["series"].size),
        "paired-oxygen": int(data["observed"].size),
    }
    (evidence / "sources.json").write_text(json.dumps(records, indent=2) + "\n", encoding="utf-8")


def _port(place: tuple[float, float], size: tuple[float, float], side: str) -> tuple[float, float]:
    x, y = place
    w, h = size
    return {
        "n": (x, y + h / 2),
        "s": (x, y - h / 2),
        "e": (x + w / 2, y),
        "w": (x - w / 2, y),
    }[side]


def _shape(kind: str, x: float, y: float, w: float, h: float) -> list[tuple[float, float]]:
    left, right, bottom, top = x - w / 2, x + w / 2, y - h / 2, y + h / 2
    if kind == "product":
        cut = min(w, h) * 0.24
        return [
            (left + cut, top),
            (right - cut, top),
            (right, top - cut),
            (right, bottom + cut),
            (right - cut, bottom),
            (left + cut, bottom),
            (left, bottom + cut),
            (left, top - cut),
        ]
    if kind == "data":
        skew = 0.1
        return [(left + skew, top), (right, top), (right - skew, bottom), (left, bottom)]
    return [(left, top), (right, top), (right, bottom), (left, bottom)]


def _draw_graph(ax: Any, layout: Layout, nodes: dict[str, tuple[str, str]], edges: Any) -> Any:
    width, height = layout.size
    ax.set(xlim=(0, width), ylim=(0, height), aspect="equal")
    ax.axis("off")
    for title, x0, y0, x1, y1 in layout.groups:
        ax.add_patch(
            Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, ec="#4A5560", lw=0.8, ls="--")
        )
        ax.text(x0 + 0.12, y1 - 0.11, title, fontweight="bold", fontsize=9, va="top", color=INK)
    labels = {}
    for name, (kind, label) in nodes.items():
        x, y = layout.places[name]
        w, h = layout.node
        ax.add_patch(Polygon(_shape(kind, x, y, w, h), fc=FILLS[kind], ec=INK, lw=0.8, zorder=2))
        labels[name] = ax.text(
            x, y, label, ha="center", va="center", fontsize=7.6, color=INK, zorder=3
        )
    for source, target, label in edges:
        start_side, end_side = layout.ports[(source, target)]
        start = _port(layout.places[source], layout.node, start_side)
        end = _port(layout.places[target], layout.node, end_side)
        ax.annotate(
            "",
            xy=end,
            xytext=start,
            zorder=1,
            arrowprops={"arrowstyle": "-|>", "color": INK, "lw": 0.9, "shrinkA": 0, "shrinkB": 0},
        )
        if label:
            if start_side == "e":
                x, y = (start[0] + end[0]) / 2, start[1] + 0.1
            else:
                x, y = start[0] + 0.16, start[1] - 0.13
            ax.text(
                x,
                y,
                label,
                fontsize=7,
                ha="center",
                va="center",
                color=INK,
                zorder=4,
                bbox={"fc": "white", "ec": "none", "pad": 0.6},
            )
    return labels


def _check_layout(fig: Any, layout: Layout, nodes: Any, labels: dict[str, Any]) -> dict[str, Any]:
    renderer = fig.canvas.get_renderer()
    inverse = fig.axes[0].transData.inverted()
    fits = True
    for name, text in labels.items():
        x, y = layout.places[name]
        w, h = layout.node
        box = text.get_window_extent(renderer).transformed(inverse)
        fits = fits and box.width <= w and box.height <= h
        fits = fits and abs(box.x0 + box.x1 - 2 * x) < 0.05 and abs(box.y0 + box.y1 - 2 * y) < 0.05
    boxes = [(*layout.places[name], *layout.node) for name in nodes]
    overlaps = sum(
        abs(a[0] - b[0]) < (a[2] + b[2]) / 2 and abs(a[1] - b[1]) < (a[3] + b[3]) / 2
        for index, a in enumerate(boxes)
        for b in boxes[index + 1 :]
    )
    inside = all(
        w / 2 <= x <= layout.size[0] - w / 2 and h / 2 <= y <= layout.size[1] - h / 2
        for x, y, w, h in boxes
    )
    return {"text_fits": bool(fits), "node_overlaps": int(overlaps), "inside_canvas": bool(inside)}


def _evidence_cards(fig: Any, ax: Any, layout: Layout, out: Path) -> None:
    width, height = layout.size
    with (out / "evidence/lake/site_trends.csv").open(encoding="utf-8") as handle:
        rows = list(csv.DictReader(handle))
    x0, y0, card_w, card_h = 5.82, 3.28, 1.5, 1.32
    ax.add_patch(Rectangle((x0, y0), card_w, card_h, fill=False, ec="#4A5560", lw=0.7, ls=":"))
    ax.plot([5.625, x0], [3.95, 3.95], color="#4A5560", lw=0.7, ls=":")
    ax.text(x0 + 0.07, y0 + card_h - 0.08, "Table 2 excerpt", fontsize=7, weight="bold", va="top")
    ax.text(x0 + 0.07, y0 + 0.96, "Site", fontsize=7, va="top", style="italic")
    ax.text(
        x0 + card_w - 0.07,
        y0 + 0.96,
        "mg/L/decade",
        fontsize=7,
        va="top",
        ha="right",
        style="italic",
    )
    for index, row in enumerate(rows[:3]):
        y = y0 + 0.77 - index * 0.19
        ax.text(x0 + 0.07, y, row["site"], fontsize=7, va="top")
        trend = row["trend_mg_l_per_decade"].replace("-", "\u2212")
        ax.text(x0 + card_w - 0.07, y, trend, fontsize=7, va="top", ha="right")
    ax.text(x0 + 0.07, y0 + 0.19, f"\u2026 {len(rows) - 3} more sites", fontsize=7, va="top")

    x0, y0, card_w, card_h = 5.82, 1.1, 1.5, 2.02
    ax.add_patch(Rectangle((x0, y0), card_w, card_h, fill=False, ec="#4A5560", lw=0.7, ls=":"))
    ax.plot([5.625, x0], [2.2, 2.2], color="#4A5560", lw=0.7, ls=":")
    ax.text(
        x0 + 0.07, y0 + card_h - 0.07, "Figure 3 thumbnail", fontsize=7, weight="bold", va="top"
    )
    image = mpimg.imread(out / "reports/figures/lake/validation-thumbnail.png")
    inset = fig.add_axes(((x0 + 0.1) / width, (y0 + 0.36) / height, 1.3 / width, 1.3 / height))
    inset.imshow(image, interpolation="antialiased")
    inset.axis("off")
    ax.text(x0 + 0.07, y0 + 0.31, "Modeled vs observed,", fontsize=7, va="top")
    ax.text(x0 + 0.07, y0 + 0.17, "dashed line is 1:1", fontsize=7, va="top")

    ax.text(0.2, 1.42, "Key", fontsize=7.5, weight="bold", color=INK)
    key = (
        ("data", "Input data"),
        ("process", "Process"),
        ("product", "Output"),
    )
    for index, (kind, label) in enumerate(key):
        x, y = 0.42 + 1.05 * (index % 2), 1.14 - 0.42 * (index // 2)
        ax.add_patch(Polygon(_shape(kind, x, y, 0.42, 0.26), fc=FILLS[kind], ec=INK, lw=0.7))
        ax.text(x + 0.27, y, label, fontsize=7, va="center", color=INK)


def _diagram(option: str, out: Path, stem: Path) -> dict[str, Any]:
    layout = LAYOUTS[option]
    fig = plt.figure(figsize=layout.size)
    ax = fig.add_axes((0, 0, 1, 1))
    labels = _draw_graph(ax, layout, NODES, EDGES)
    if option == "C":
        _evidence_cards(fig, ax, layout, out)
    fig.canvas.draw()
    result = _check_layout(fig, layout, NODES, labels)
    _save(fig, stem)
    return result


def build_prototypes(out: Path) -> None:
    _style()
    folder = out / "scratch/visuals/workflow"
    checks = {
        option: _diagram(option, out, folder / f"option-{option}") for option in sorted(LAYOUTS)
    }
    captions = {
        "A": "A: grouped spine, plain fallback",
        "B": "B: compact phase lanes",
        "C": "C: figure-enhanced flow",
    }
    fig, axes = plt.subplots(1, 3, figsize=(11, 4.6), width_ratios=[0.78, 1.25, 1.1])
    for ax, option in zip(axes, sorted(LAYOUTS), strict=True):
        ax.imshow(mpimg.imread(folder / f"option-{option}.png"))
        ax.set_title(captions[option], fontsize=10, fontweight="bold")
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(folder / "comparison.png", dpi=170, bbox_inches="tight")
    plt.close(fig)
    checks_path = folder / "checks.json"
    checks_path.write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")


def build_selected_diagram(out: Path, selected: str) -> None:
    _style()
    _diagram(selected, out, out / "reports/figures/lake/workflow")


def build_standalone(out: Path) -> None:
    """Show each capability without a report or project pipeline."""
    _style()
    nodes = {
        "wp1": ("process", "WP1 Sampling\ndesign"),
        "wp2": ("process", "WP2 Field\ncampaign"),
        "wp3": ("process", "WP3 Model\ndevelopment"),
        "wp4": ("product", "WP4 Decision\nbrief"),
    }
    edges = (("wp1", "wp2", ""), ("wp1", "wp3", ""), ("wp2", "wp4", ""), ("wp3", "wp4", ""))
    layout = Layout(
        (6.2, 2.7),
        (1.4, 0.6),
        {"wp1": (0.95, 1.35), "wp2": (3.1, 2.05), "wp3": (3.1, 0.65), "wp4": (5.25, 1.35)},
        {
            ("wp1", "wp2"): ("e", "w"),
            ("wp1", "wp3"): ("e", "w"),
            ("wp2", "wp4"): ("e", "w"),
            ("wp3", "wp4"): ("e", "w"),
        },
    )
    fig = plt.figure(figsize=layout.size)
    ax = fig.add_axes((0, 0, 1, 1))
    labels = _draw_graph(ax, layout, nodes, edges)
    fig.canvas.draw()
    checks = _check_layout(fig, layout, nodes, labels)
    _save(fig, out / "work-package-diagram")
    fig, ax = plt.subplots(figsize=(3.5, 3.3), constrained_layout=True)
    _scatter(ax, _synthetic())
    _save(fig, out / "single-panel-figure")
    (out / "method-comparison.md").write_text(
        "**Table 1. Candidate trend methods differ in their assumptions.** Synthetic example.\n\n"
        "| Method | Handles seasonality | Main assumption | Best use |\n"
        "|---|---|---|---|\n"
        "| Harmonic regression | Yes, fixed cycle | Stable seasonal shape | Regular series |\n"
        "| Seasonal Mann-Kendall | Yes, by season | Monotonic trend | Skewed data |\n",
        encoding="utf-8",
    )
    (out / "checks.json").write_text(json.dumps(checks, indent=2) + "\n", encoding="utf-8")


# Machado, Oliveira and Fernandes (2009), severity 1.0, applied to linear RGB.
CVD = {
    "protanopia": [
        [0.152286, 1.052583, -0.204868],
        [0.114503, 0.786281, 0.099216],
        [-0.003882, -0.048116, 1.051998],
    ],
    "deuteranopia": [
        [0.367322, 0.860646, -0.227968],
        [0.280085, 0.672501, 0.047413],
        [-0.011820, 0.042940, 0.968881],
    ],
    "tritanopia": [
        [1.255528, -0.076749, -0.178779],
        [-0.078411, 0.930809, 0.147602],
        [0.004733, 0.691367, 0.303900],
    ],
}
GRAY = [[0.2126, 0.7152, 0.0722]] * 3
MODES = {"original": [[1, 0, 0], [0, 1, 0], [0, 0, 1]], "grayscale": GRAY, **CVD}
WHITE = "#FFFFFF"
TEXT_PAIRS = [(INK, fill) for fill in (*FILLS.values(), WHITE)] + [("#000000", WHITE)]
GRAPHIC_PAIRS = [
    *[(INK, fill) for fill in FILLS.values()],
    ("#4A5560", WHITE),
    ("#2B5F85", WHITE),
    ("#B5482F", WHITE),
    ("#3F444A", WHITE),
]


def _linear(rgb: Any) -> Any:
    rgb = np.asarray(rgb, dtype=float)
    return np.where(rgb <= 0.04045, rgb / 12.92, ((rgb + 0.055) / 1.055) ** 2.4)


def _encoded(linear: Any) -> Any:
    linear = np.clip(linear, 0, 1)
    return np.where(linear <= 0.0031308, linear * 12.92, 1.055 * linear ** (1 / 2.4) - 0.055)


def _contrast(first: str, second: str, matrix: Any) -> float:
    def luminance(color: str) -> float:
        value = _linear(matplotlib.colors.to_rgb(color))
        shown = np.clip(np.asarray(matrix) @ value, 0, 1)
        return float(np.array([0.2126, 0.7152, 0.0722]) @ shown)

    high, low = sorted((luminance(first), luminance(second)), reverse=True)
    return (high + 0.05) / (low + 0.05)


def _review_sheet(source: Path, target: Path) -> None:
    image = mpimg.imread(source)[..., :3]
    height, width = image.shape[:2]
    fig, axes = plt.subplots(2, 2, figsize=(14.8, 14.8 * height / width + 0.7))
    for ax, mode in zip(axes.flat, ("grayscale", *CVD), strict=True):
        ax.imshow(_encoded(_linear(image) @ np.asarray(MODES[mode]).T))
        ax.set_title(mode.capitalize(), fontsize=13, fontweight="bold")
        ax.axis("off")
    fig.tight_layout()
    fig.savefig(target, dpi=135)
    plt.close(fig)


def build_qa(out: Path) -> None:
    """Record contrast under each simulation and write final-size review sheets."""
    _style()
    folder = out / "scratch/visuals/accessibility"
    folder.mkdir(parents=True, exist_ok=True)
    minimums = {
        mode: {
            "text": round(min(_contrast(*pair, matrix) for pair in TEXT_PAIRS), 2),
            "graphic": round(min(_contrast(*pair, matrix) for pair in GRAPHIC_PAIRS), 2),
        }
        for mode, matrix in MODES.items()
    }
    result = {
        "targets": {"text": 4.5, "graphic": 3.0},
        "minimum_contrast": minimums,
        "meets_targets": all(
            item["text"] >= 4.5 and item["graphic"] >= 3.0 for item in minimums.values()
        ),
        "non_color_cues": {
            "diagram": "Distinct shapes and text labels for input, process, and output nodes.",
            "series": "Solid line with circles, dashed line with squares, and a legend.",
            "validation": "Open markers and a dashed 1:1 line named in the card and legend.",
        },
        "smallest_diagram_text_pt": 7,
        "limits": (
            "Colors are computed from declared pairs, and simulations approximate color vision. "
            "No person with a color-vision deficiency reviewed these. This is not a WCAG "
            "conformance claim. The example has no ordered color scale."
        ),
    }
    (folder / "checks.json").write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    _review_sheet(out / "scratch/visuals/workflow/option-C.png", folder / "diagram-review.png")
    _review_sheet(out / "reports/figures/lake/trends.png", folder / "figure-review.png")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="step", required=True)
    for step in ("figures", "prototypes", "diagram", "standalone", "qa"):
        command = commands.add_parser(step)
        command.add_argument("--out", type=Path, default=Path("."))
        if step == "diagram":
            command.add_argument("--selected", choices=sorted(LAYOUTS), required=True)
    args = parser.parse_args()
    if args.step == "figures":
        build_figures(args.out)
    elif args.step == "prototypes":
        build_prototypes(args.out)
    elif args.step == "diagram":
        build_selected_diagram(args.out, args.selected)
    elif args.step == "qa":
        build_qa(args.out)
    else:
        build_standalone(args.out)


if __name__ == "__main__":
    main()
