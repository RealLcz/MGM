#!/usr/bin/env python3
"""Violin plot of token usage by category across MGM, HGM, and ablation runs."""

from __future__ import annotations

import json
import math
import re
import statistics
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager as fm
import numpy as np

_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"


def _register_times_new_roman() -> None:
    if not _FONT_DIR.is_dir():
        return
    for pattern in ("Times*.TTF", "Times*.ttf", "times*.ttf"):
        for ttf in sorted(_FONT_DIR.glob(pattern)):
            fm.fontManager.addfont(str(ttf))


_register_times_new_roman()
plt.rcParams.update({
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "mathtext.fontset": "custom",
    "mathtext.rm": "Times New Roman",
    "mathtext.it": "Times New Roman:italic",
    "mathtext.bf": "Times New Roman:bold",
})

USAGE = re.compile(
    r"usage=CompletionUsage\(completion_tokens=(\d+),\s*prompt_tokens=(\d+),\s*total_tokens=(\d+)"
)

RUNS = {
    "MGM": "20260505_122548",
    "HGM": "20260507_160801",
    "A+B ablation": "20260509_135040",
    "A+C ablation": "20260509_084344",
}

BASE = Path(__file__).resolve().parents[1] / "output_polyglot"
OUT_PATH = Path(__file__).resolve().parents[1] / "docs" / "images" / "token_boxplot_by_category.png"

AXIS_FONT_SIZE = 11
VIOLIN_ALPHA = 0.30
POINT_ALPHA = 0.35
EVAL_POINT_ALPHA = 0.10
COLORS = ["#4C78A8", "#F58518", "#54A24B", "#E45756"]

CATEGORIES = [
    (r"$\varphi$-evaluation", "eval"),
    (r"$\Phi_\mathrm{CM}$", "A"),
    (r"$\Phi_\mathrm{RM}$", "B"),
    (r"$\Phi_\mathrm{CH}$", "C"),
]


def sum_usage(text: str) -> int:
    hits = USAGE.findall(text)
    return sum(int(h[2]) for h in hits) if hits else 0


def collect_run_tokens(run_dir: Path) -> dict[str, list[int]]:
    data: dict[str, list[int]] = {"eval": [], "A": [], "B": [], "C": []}

    for md in run_dir.rglob("*.md"):
        rel = md.relative_to(run_dir)
        if "predictions" not in rel.parts or md.name.endswith("_eval.md"):
            continue
        tokens = sum_usage(md.read_text(errors="ignore"))
        if tokens > 0:
            data["eval"].append(tokens)

    for node in run_dir.iterdir():
        if not node.is_dir():
            continue
        evo = node / "self_evo.md"
        meta = node / "metadata.json"
        if not evo.exists() or not meta.exists():
            continue
        strat = str(json.loads(meta.read_text()).get("self_improve_strategy", "A")).upper()
        tokens = sum_usage(evo.read_text(errors="ignore"))
        if tokens > 0 and strat in ("A", "B", "C"):
            data[strat].append(tokens)

    return data


def _to_log10(tokens: list[int]) -> list[float]:
    return [math.log10(v) for v in tokens if v > 0]


def _configure_log_yaxis(ax) -> None:
    """Linear axis in log10(token); major ticks at 10^n."""
    ax.autoscale(axis="y")
    y0, y1 = ax.get_ylim()
    major = list(range(int(math.floor(y0)), int(math.ceil(y1)) + 1))
    ax.set_yticks(major)
    ax.set_yticklabels(
        [rf"$10^{{{d}}}$" for d in major],
        fontsize=AXIS_FONT_SIZE,
    )
    ax.tick_params(axis="both", labelsize=AXIS_FONT_SIZE)
    ax.grid(axis="y", alpha=0.25, linestyle="--")


def _plot_categories(ax, log_data: list[list[float]], keys: list[str]) -> None:
    n = len(log_data)
    positions = np.arange(1, n + 1)

    parts = ax.violinplot(
        log_data,
        positions=positions,
        widths=0.45,
        showmeans=True,
        showmedians=True,
        showextrema=False,
        quantiles=[[0.25, 0.75] for _ in log_data],
    )
    for body, color in zip(parts["bodies"], COLORS):
        body.set_facecolor(color)
        body.set_edgecolor(color)
        body.set_alpha(VIOLIN_ALPHA)
        body.set_zorder(1)
    for key in ("cmeans", "cmedians", "cbars", "cquantiles"):
        if key in parts:
            parts[key].set_color("#333333")
            parts[key].set_linewidth(1.5 if key == "cmeans" else 1.0)

    bp = ax.boxplot(
        log_data,
        positions=positions,
        widths=0.12,
        patch_artist=False,
        showfliers=False,
        whis=(5, 95),
        zorder=4,
    )
    for element in ("boxes", "whiskers", "caps", "medians"):
        for artist in bp[element]:
            artist.set_color("#333333")
            artist.set_linewidth(1.0)

    rng = np.random.default_rng(42)
    for pos, vals, color, key in zip(positions, log_data, COLORS, keys):
        if not vals:
            continue
        alpha = EVAL_POINT_ALPHA if key == "eval" else POINT_ALPHA
        jitter = (rng.random(len(vals)) - 0.5) * 0.10
        ax.scatter(
            np.full(len(vals), pos) + jitter,
            vals,
            s=10,
            c=color,
            alpha=alpha,
            linewidths=0,
            zorder=3,
        )


def main() -> None:
    pooled: dict[str, list[int]] = {key: [] for _, key in CATEGORIES}
    per_run: dict[str, dict[str, list[int]]] = {}

    for label, run_id in RUNS.items():
        run_dir = BASE / run_id
        if not run_dir.is_dir():
            raise FileNotFoundError(run_dir)
        run_data = collect_run_tokens(run_dir)
        per_run[label] = run_data
        for _, key in CATEGORIES:
            pooled[key].extend(run_data[key])

    labels = [name for name, _ in CATEGORIES]
    keys = [key for _, key in CATEGORIES]
    log_data = [_to_log10(pooled[key]) for key in keys]
    y_log = [v for group in log_data for v in group]
    if not y_log:
        raise ValueError("No token usage data to plot")

    fig, ax = plt.subplots(figsize=(4, 3))
    _plot_categories(ax, log_data, keys)
    ax.set_xticks(np.arange(1, len(labels) + 1))
    ax.set_xticklabels(labels, fontsize=AXIS_FONT_SIZE)
    ax.set_ylabel("token count", fontsize=AXIS_FONT_SIZE)
    _configure_log_yaxis(ax)

    OUT_PATH.parent.mkdir(parents=True, exist_ok=True)
    pdf_path = OUT_PATH.with_suffix(".pdf")
    fig.tight_layout(pad=0.15, w_pad=0.1, h_pad=0.1)
    save_kw = {"bbox_inches": "tight", "pad_inches": 0.02}
    fig.savefig(OUT_PATH, dpi=160, format="png", **save_kw)
    fig.savefig(pdf_path, format="pdf", **save_kw)
    plt.close(fig)
    print(f"Saved {OUT_PATH.resolve()}")
    print(f"Saved {pdf_path.resolve()}")

    print("\nPooled summary (log10 median):")
    for label, key in zip(labels, keys):
        vals = pooled[key]
        if vals:
            print(
                f"  {label}: n={len(vals)}, "
                f"median={statistics.median(vals):,.0f}, "
                f"log10={math.log10(statistics.median(vals)):.2f}"
            )
        else:
            print(f"  {label}: n=0")

    print("\nPer-run counts:")
    for run_label, run_data in per_run.items():
        counts = [f"eval={len(run_data['eval'])}"] + [
            f"{s}={len(run_data[s])}" for s in ("A", "B", "C")
        ]
        print(f"  {run_label}: {', '.join(counts)}")


if __name__ == "__main__":
    main()
