#!/usr/bin/env python3
"""Draw HGM evolution tree from hgm_metadata.jsonl."""

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib.pyplot as plt
import numpy as np
from matplotlib.collections import LineCollection
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from matplotlib import cm
from PIL import Image, ImageDraw

PAPER_WHITE = "#FFFFFF"

plt.rcParams.update({
    "figure.facecolor": PAPER_WHITE,
    "axes.facecolor": PAPER_WHITE,
    "savefig.facecolor": PAPER_WHITE,
    "savefig.edgecolor": PAPER_WHITE,
})


def load_final_metadata(path: Path) -> dict:
    content = path.read_text().strip()
    parts = content.split("\n}\n{")
    last = parts[-1]
    if not last.startswith("{"):
        last = "{" + last
    if not last.endswith("}"):
        last = last + "}"
    return json.loads(last)


def load_node_metadata(run_dir: Path, commit_id: str) -> dict:
    meta_path = run_dir / commit_id / "metadata.json"
    if not meta_path.exists():
        return {}
    with meta_path.open() as f:
        return json.load(f)


def load_node_performance(run_dir: Path, commit_id: str) -> Tuple[int, int]:
    meta = load_node_metadata(run_dir, commit_id)
    perf = meta.get("overall_performance", {})
    resolved = int(perf.get("total_resolved_instances", 0))
    submitted = int(perf.get("total_submitted_instances", 0))
    return resolved, submitted


STRATEGY_COLORS = {
    "A": "#D62828",  # red
    "B": "#F77F00",  # orange
    "C": "#7209B7",  # purple
}


def utility_rgba(t: float) -> Tuple[float, float, float, float]:
    t = max(0.0, min(1.0, t))
    if t >= 0.5:
        r = 1.0 - (t - 0.5) * 2
        g = 0.75 + (t - 0.5) * 0.5
        b = 0.2
    else:
        r = 0.9
        g = 0.3 + t
        b = 0.2
    return (r, g, b, 0.92)


def build_utility_cmap() -> LinearSegmentedColormap:
    samples = [utility_rgba(t)[:3] for t in np.linspace(0.0, 1.0, 256)]
    return LinearSegmentedColormap.from_list("utility", samples)


FIG_SIZE = (18, 10)
FIG_DPI = 150
X_PAD = 0.35
Y_TOP_PAD = 0.35
Y_BOTTOM_PAD = 0.95
SAVE_PAD_INCHES = 0.05


def whiten_png(path: Path) -> None:
    """Force pure #FFFFFF background connected to image borders (PDF-safe white)."""
    with Image.open(path) as im:
        if im.mode == "RGBA":
            background = Image.new("RGB", im.size, (255, 255, 255))
            background.paste(im, mask=im.split()[3])
            im = background
        else:
            im = im.convert("RGB")

        arr = np.array(im, dtype=np.uint8)
        channel_min = arr.min(axis=2)
        channel_max = arr.max(axis=2)
        spread = channel_max - channel_min
        near_white = (channel_min >= 220) & (spread <= 30)
        arr[near_white] = (255, 255, 255)
        neutral = (
            (arr[:, :, 0] == arr[:, :, 1])
            & (arr[:, :, 1] == arr[:, :, 2])
            & (arr[:, :, 0] >= 180)
        )
        arr[neutral] = (255, 255, 255)
        im = Image.fromarray(arr)

        w, h = im.size
        seeds = {(0, 0), (w - 1, 0), (0, h - 1), (w - 1, h - 1)}
        for x in range(0, w, max(1, w // 24)):
            seeds.add((x, 0))
            seeds.add((x, h - 1))
        for y in range(0, h, max(1, h // 24)):
            seeds.add((0, y))
            seeds.add((w - 1, y))
        for xy in seeds:
            ImageDraw.floodfill(im, xy, (255, 255, 255), thresh=40)

        im.save(path, format="PNG", optimize=False)


def draw_tree(
    run_dir: Path,
    out_path: Optional[Path] = None,
    color_by: str = "utility",
    highlight_node: Optional[int] = None,
    highlight_submitted: Optional[int] = None,
    tree_title: str = "",
) -> Path:
    meta_path = run_dir / "hgm_metadata.jsonl"
    out_path = out_path or (run_dir / "hgm_tree.png")

    data = load_final_metadata(meta_path)
    nodes = {n["id"]: n for n in data["nodes"]}
    commit_to_id: Dict[str, int] = {"initial": 0}
    for nid, node in nodes.items():
        commit_to_id[node["commit_id"]] = nid

    for nid, node in nodes.items():
        meta = load_node_metadata(run_dir, node["commit_id"])
        resolved, submitted = load_node_performance(run_dir, node["commit_id"])
        node["resolved"] = resolved
        node["submitted"] = submitted
        node["strategy"] = str(meta.get("self_improve_strategy", "A")).upper()
        peer_commit = meta.get("peer_commit")
        node["peer_id"] = commit_to_id.get(peer_commit) if peer_commit else None

    children: Dict[int, List[int]] = {0: []}
    for nid, node in nodes.items():
        children.setdefault(node["parent_id"], []).append(nid)
    for pid in children:
        children[pid].sort()

    positions: Dict[int, Tuple[float, float]] = {}

    def assign_x(nid: int, depth: int = 0, counter: Optional[list] = None) -> float:
        if counter is None:
            counter = [0]
        if not children.get(nid):
            x = counter[0]
            counter[0] += 1
        else:
            child_xs = [assign_x(c, depth + 1, counter) for c in children[nid]]
            x = sum(child_xs) / len(child_xs)
        positions[nid] = (x, -depth)
        return x

    assign_x(0)

    if color_by == "resolved":
        values = [nodes[nid]["resolved"] for nid in nodes]
        vmax = max(values) if values else 1
        norm = Normalize(vmin=0, vmax=max(vmax, 1))
        node_cmap = cm.get_cmap("YlGn")
        cbar_label = "Resolved tasks"
    else:
        norm = Normalize(vmin=0, vmax=1)
        node_cmap = build_utility_cmap()
        cbar_label = "Accuracy"

    def node_color(value: float) -> Tuple[float, float, float, float]:
        rgba = node_cmap(norm(value))
        return (rgba[0], rgba[1], rgba[2], 0.92)

    fig, ax = plt.subplots(figsize=FIG_SIZE, facecolor=PAPER_WHITE)
    ax.set_facecolor(PAPER_WHITE)
    fig.patch.set_facecolor(PAPER_WHITE)
    ax.patch.set_alpha(1.0)

    node_size = 1000
    root_size = 1300
    label_fontsize = 8
    ctx_label_fontsize = 6.5
    if highlight_submitted is not None:
        highlight_nodes = {
            nid for nid in nodes if nodes[nid]["submitted"] == highlight_submitted
        }
    elif highlight_node is not None:
        highlight_nodes = {highlight_node}
    else:
        highlight_nodes = {
            max(nodes, key=lambda nid: nodes[nid].get("mean_utility", 0))
        }
    ctx_label_color = STRATEGY_COLORS["C"]

    edges_by_strategy: Dict[str, list] = {"A": [], "B": [], "C": []}
    for pid, kids in children.items():
        if pid not in positions:
            continue
        p1 = positions[pid]
        for cid in kids:
            strategy = nodes[cid]["strategy"]
            color_key = strategy if strategy in edges_by_strategy else "A"
            edges_by_strategy[color_key].append([p1, positions[cid]])

    for strategy, segments in edges_by_strategy.items():
        if not segments:
            continue
        ax.add_collection(
            LineCollection(
                segments,
                colors=STRATEGY_COLORS[strategy],
                linewidths=2.0,
                zorder=1,
            )
        )

    for nid in sorted(positions):
        x, y = positions[nid]
        if nid == 0:
            ax.scatter(x, y, s=root_size, c="#333333", edgecolors="none", zorder=3)
            ax.text(
                x, y, "root", ha="center", va="center",
                fontsize=9, color="#e8e8e8", fontweight="bold", zorder=4,
            )
            continue

        node = nodes[nid]
        if color_by == "resolved":
            value = node["resolved"]
        else:
            value = node["mean_utility"]
        label = f"#{nid}"
        peer_id = node.get("peer_id")
        has_ctx = node["strategy"] == "C" and peer_id is not None

        face = node_color(value)
        if nid in highlight_nodes:
            ax.scatter(
                x, y, s=2200, marker="*", c=[face],
                edgecolors="#333333", linewidths=0.8, zorder=3,
            )
        else:
            ax.scatter(
                x, y, s=node_size, c=[face],
                edgecolors="none", zorder=3,
            )
        if has_ctx:
            ax.text(
                x, y + 0.06, f"ctx #{peer_id}", ha="center", va="center",
                fontsize=ctx_label_fontsize, color=ctx_label_color,
                fontweight="bold", zorder=4,
            )
            ax.text(
                x, y - 0.06, label, ha="center", va="center",
                fontsize=label_fontsize, color="#000000",
                fontweight="bold", zorder=4,
            )
        else:
            ax.text(
                x, y, label, ha="center", va="center",
                fontsize=label_fontsize, color="#000000",
                fontweight="bold", zorder=4,
            )

    xs = [p[0] for p in positions.values()]
    ys = [p[1] for p in positions.values()]
    ax.set_xlim(min(xs) - X_PAD, max(xs) + X_PAD)
    ax.set_ylim(min(ys) - Y_BOTTOM_PAD, Y_TOP_PAD)
    ax.set_aspect("equal")
    ax.axis("off")

    if tree_title:
        color_desc = "resolved tasks" if color_by == "resolved" else "utility"
        title_extra = f" · colored by {color_desc}" if color_by == "resolved" else ""
        evals_label = "200 task evals"
        ax.set_title(
            f"{tree_title}\n"
            f"{len(nodes)} nodes · {evals_label}{title_extra}",
            fontsize=14, fontweight="bold", pad=8,
        )
        top_margin = 0.94
    else:
        top_margin = 0.98

    sm = plt.cm.ScalarMappable(cmap=node_cmap, norm=norm)
    sm.set_array([])
    cbar = fig.colorbar(
        sm, ax=ax, fraction=0.028, pad=0.01, aspect=12, shrink=0.62,
    )
    cbar.set_label(cbar_label, fontsize=9)
    cbar.ax.tick_params(labelsize=7)
    cbar.ax.set_facecolor(PAPER_WHITE)

    for axis in fig.get_axes():
        axis.set_facecolor(PAPER_WHITE)
        axis.patch.set_alpha(1.0)

    if highlight_submitted is not None:
        star_label = f"Full eval ({highlight_submitted} tasks)"
    elif len(highlight_nodes) == 1:
        star_label = f"Node #{next(iter(highlight_nodes))}"
    else:
        star_label = "Highlighted nodes"

    legend_patches = [
        Line2D([0], [0], color=STRATEGY_COLORS["A"], linewidth=2.0, label="Clonal Mutation"),
        Line2D([0], [0], color=STRATEGY_COLORS["B"], linewidth=2.0, label="Reaction-norm Mutation"),
        Line2D([0], [0], color=STRATEGY_COLORS["C"], linewidth=2.0, label="Cross-lineage Hybridization"),
        Line2D([0], [0], color=ctx_label_color, linewidth=2.5, label="Context parent (ctx #id)"),
        Line2D([0], [0], marker="*", color="w", markerfacecolor="#8fbf5a",
               markeredgecolor="#333333", markersize=12, label=star_label),
    ]
    ax.legend(handles=legend_patches, loc="lower right", fontsize=8,
              framealpha=1.0, facecolor=PAPER_WHITE, edgecolor=PAPER_WHITE)

    fig.subplots_adjust(left=0.01, right=0.90, top=top_margin, bottom=0.02)
    save_kw = dict(
        facecolor=PAPER_WHITE, edgecolor="none",
        transparent=False, bbox_inches="tight", pad_inches=SAVE_PAD_INCHES,
    )
    fig.savefig(out_path, dpi=FIG_DPI, format="png", **save_kw)
    fig.savefig(out_path.with_suffix(".pdf"), format="pdf", **save_kw)
    plt.close(fig)
    whiten_png(out_path)
    return out_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("output_polyglot/20260505_122548"),
        help="HGM run directory containing hgm_metadata.jsonl",
    )
    parser.add_argument("--out", type=Path, default=None, help="Output PNG path")
    parser.add_argument(
        "--color-by",
        choices=["resolved", "utility"],
        default="utility",
        help="Node color metric (default: utility)",
    )
    parser.add_argument(
        "--highlight-node",
        type=int,
        default=None,
        help="Node id to mark with a star (default: highest mean_utility)",
    )
    parser.add_argument(
        "--highlight-submitted",
        type=int,
        default=None,
        help="Mark nodes that completed this many task evals with a star",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional tree figure title (default: none)",
    )
    args = parser.parse_args()

    out = draw_tree(
        args.run_dir, args.out, color_by=args.color_by,
        highlight_node=args.highlight_node,
        highlight_submitted=args.highlight_submitted,
        tree_title=args.title,
    )
    print(f"Saved: {out.resolve()}")


if __name__ == "__main__":
    main()
