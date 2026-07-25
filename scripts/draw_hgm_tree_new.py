#!/usr/bin/env python3
"""
Draw HGM evolution tree from hgm_metadata.jsonl — circular (lower-semicircle) cladogram.

Geometry:
  - Tips lie on the lower semicircle  (θ ∈ [π, 2π], y ≤ 0).
  - Row order matches the linear tree (pre-order); root at θ = π (left),
    last node at θ = 2π (right), through θ = 3π/2 at the bottom.
  - Radial segments replace horizontal cladogram arms; circular arcs at
    fixed radius replace vertical spines.
  - All nodes sit on the tip circle (outer edge), same as the linear rail.
"""

import argparse
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import matplotlib.patches as mpatches
from matplotlib import font_manager as fm
import numpy as np
from matplotlib.colors import LinearSegmentedColormap, Normalize
from matplotlib.lines import Line2D
from PIL import Image, ImageDraw

PAPER_WHITE = "#FFFFFF"
_FONT_DIR = Path(__file__).resolve().parent.parent / "fonts"


def _register_times_new_roman() -> None:
    """Load bundled Times New Roman TTFs (no system/sudo install required)."""
    if not _FONT_DIR.is_dir():
        return
    for pattern in ("Times*.TTF", "Times*.ttf", "times*.ttf"):
        for ttf in sorted(_FONT_DIR.glob(pattern)):
            fm.fontManager.addfont(str(ttf))


_register_times_new_roman()

plt.rcParams.update({
    "figure.facecolor": PAPER_WHITE,
    "axes.facecolor": PAPER_WHITE,
    "savefig.facecolor": PAPER_WHITE,
    "savefig.edgecolor": PAPER_WHITE,
    "font.family": "serif",
    "font.serif": ["Times New Roman", "Times", "DejaVu Serif"],
    "mathtext.fontset": "custom",
    "mathtext.rm": "Times New Roman",
    "mathtext.it": "Times New Roman:italic",
    "mathtext.bf": "Times New Roman:bold",
})

STRATEGY_COLORS = {
    "A": "#EA6B66",    
    "B": "#FFD966",     
    "C": "#97D077",     
}

ECOL = {
    "clonal": STRATEGY_COLORS["A"],
    "reaction": STRATEGY_COLORS["B"],
    "hybridize": STRATEGY_COLORS["C"],
    None: "#888888",
}
CTX_COL = "#CCCCCC"
CTX_ALPHA = 1
CTX_ARROWSTYLE = "-|>"
CTX_ARROW_SCALE = 9
VERT_COL = "#888888"
LW = 1.2

FIG_DPI = 300
FIG_SIZE = (5, 5)
LEGEND_FONT_SIZE = 8
# Tip-circle diameter / horizontal data span (fixed across plots).
TREE_WIDTH_FRAC = 0.80
SAVE_PAD_INCHES = 0.05
DEFAULT_CTX_SWEEP = 1.0
NODE_R_MAX_FRAC = 0.07
NODE_R_MIN_RATIO = 0.4  # min radius = NODE_R_MIN_RATIO × max radius

# Accuracy colormap stops (hex): low → high
ACC_CMAP_STOPS = [
    "#F19C99",
    "#FFCE9F",
    "#FFE599",
    "#B9E0A5",
]

ETYPE_LEGEND = {
    "clonal": ("Clonal Mutation", STRATEGY_COLORS["A"]),
    "reaction": ("Reaction-norm Mutation", STRATEGY_COLORS["B"]),
    "hybridize": ("Cross-lineage Hybridization", STRATEGY_COLORS["C"]),
}


def build_accuracy_cmap() -> LinearSegmentedColormap:
    return LinearSegmentedColormap.from_list("accuracy", ACC_CMAP_STOPS, N=256)


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


@dataclass
class TreeNode:
    label: str
    nid: int
    etype: Optional[str]
    acc: float
    perf: float
    ctx: Optional[str]
    children: List["TreeNode"] = field(default_factory=list)


def _assign_rows(node: TreeNode, row_map: Optional[dict] = None, counter: Optional[list] = None) -> dict:
    if counter is None:
        counter = [0]
        row_map = {}
    row_map[id(node)] = counter[0]
    counter[0] += 1
    for child in node.children:
        _assign_rows(child, row_map, counter)
    return row_map


def _assign_depth(node: TreeNode, depth_map: Optional[dict] = None, depth: int = 0) -> dict:
    if depth_map is None:
        depth_map = {}
    depth_map[id(node)] = depth
    for child in node.children:
        _assign_depth(child, depth_map, depth + 1)
    return depth_map


def _all_nodes(node: TreeNode) -> List[TreeNode]:
    out = [node]
    for child in node.children:
        out.extend(_all_nodes(child))
    return out


def _node_text(node: TreeNode) -> str:
    return str(node.nid)


def _row_angle(row: int, n_rows: int) -> float:
    if n_rows <= 1:
        return 1.5 * np.pi
    return np.pi + (row / (n_rows - 1)) * np.pi


def _pol_xy(r: float, theta: float) -> Tuple[float, float]:
    return r * np.cos(theta), r * np.sin(theta)


def _arc_xy(r: float, t0: float, t1: float, n: int = 64) -> Tuple[np.ndarray, np.ndarray]:
    ts = np.linspace(t0, t1, n)
    return r * np.cos(ts), r * np.sin(ts)


def _r_at_depth(depth: int, max_depth: int, r_tip: float) -> float:
    return r_tip * depth / (max_depth + 1)


def _outward_normal(m: np.ndarray, u_hat: np.ndarray) -> np.ndarray:
    n_hat = np.array([-u_hat[1], u_hat[0]])
    if np.linalg.norm(m + n_hat) <= np.linalg.norm(m - n_hat):
        n_hat = -n_hat
    return n_hat


def _ctx_arc_xy(
    a_xy: Tuple[float, float],
    b_xy: Tuple[float, float],
    sweep: float,
    r_tip: float,
    *,
    n: int = 400,
) -> Tuple[np.ndarray, np.ndarray]:
    sweep = float(np.clip(sweep, np.pi, 2.0 * np.pi))
    a = np.asarray(a_xy, float)
    b = np.asarray(b_xy, float)
    m = (a + b) / 2.0
    r_chord = np.linalg.norm(b - a) / 2.0
    if r_chord < 1e-12:
        return np.array([a[0]]), np.array([a[1]])

    u_hat = (b - a) / (2.0 * r_chord)
    n_hat = _outward_normal(m, u_hat)

    half = sweep / 2.0
    R = r_chord / max(abs(np.sin(half)), 1e-12)
    o = m + n_hat * (R * np.cos(half))

    ang_a = np.arctan2(a[1] - o[1], a[0] - o[0])
    ang_b = np.arctan2(b[1] - o[1], b[0] - o[0])

    short_ccw = (ang_b - ang_a) % (2.0 * np.pi)
    if short_ccw < 1e-12:
        short_ccw = 2.0 * np.pi
    if short_ccw <= np.pi + 1e-9:
        minor, major = short_ccw, short_ccw - 2.0 * np.pi
    else:
        minor, major = short_ccw - 2.0 * np.pi, short_ccw

    def _build(travel: float) -> Tuple[np.ndarray, np.ndarray]:
        angles = ang_a + np.linspace(0.0, travel, n)
        xs = o[0] + R * np.cos(angles)
        ys = o[1] + R * np.sin(angles)
        xs[0], ys[0] = a[0], a[1]
        xs[-1], ys[-1] = b[0], b[1]
        return xs, ys

    candidates = sorted((minor, major), key=lambda t: abs(abs(t) - sweep))

    best, best_score = None, -np.inf
    for travel in candidates:
        xs, ys = _build(travel)
        min_r = float(np.min(np.hypot(xs, ys)))
        score = min_r if min_r >= r_tip - 1e-5 else min_r - 10.0
        if score > best_score:
            best_score = score
            best = (xs, ys)

    return best if best is not None else _build(candidates[0])


def _draw_ctx_arc_arrow(
    ax: plt.Axes,
    xs: np.ndarray,
    ys: np.ndarray,
    *,
    color: str = CTX_COL,
    lw: float = LW,
    alpha: float = CTX_ALPHA,
    zorder: int = 3,
) -> None:
    ax.plot(xs, ys, color=color, lw=lw, alpha=alpha,
            solid_capstyle="round", zorder=zorder)

    seg_lens = np.hypot(np.diff(xs), np.diff(ys))
    half_len = float(seg_lens.sum()) * 0.5
    cum = np.cumsum(seg_lens)
    mid = int(np.searchsorted(cum, half_len))
    mid = max(1, min(mid, len(xs) - 2))
    half_span = max(2, len(xs) // 40)
    i0 = max(0, mid - half_span)
    i1 = min(len(xs) - 1, mid + half_span)

    patch = mpatches.FancyArrowPatch(
        (xs[i0], ys[i0]),
        (xs[i1], ys[i1]),
        arrowstyle=CTX_ARROWSTYLE,
        mutation_scale=CTX_ARROW_SCALE,
        color=color,
        linewidth=lw,
        alpha=alpha,
        shrinkA=0,
        shrinkB=0,
        fill=False,
        zorder=zorder + 1,
    )
    ax.add_patch(patch)


def _log_accuracy_stats(nodes: Dict[int, dict], color_by: str) -> None:
    """Log min/max for the plotted metric and underlying raw counts."""
    node_ids = [nid for nid in nodes if nid != 0]
    if not node_ids:
        print("Accuracy stats: no nodes (excluding root)")
        return

    resolved = [int(nodes[nid].get("resolved", 0)) for nid in node_ids]
    submitted = [int(nodes[nid].get("submitted", 0)) for nid in node_ids]
    utilities = [float(nodes[nid].get("mean_utility", 0.0)) for nid in node_ids]

    print("Accuracy / performance stats (nodes excluding root id=0):")
    print(f"  resolved (raw count):  min={min(resolved)}, max={max(resolved)}")
    print(f"  submitted (raw count): min={min(submitted)}, max={max(submitted)}")
    print(
        f"  mean_utility (resolved/submitted): "
        f"min={min(utilities):.6g}, max={max(utilities):.6g}"
    )

    if color_by == "resolved":
        print(f"  color/size metric: resolved (raw); colorbar range [0, {max(resolved, 1)}]")
    else:
        print(
            "  color metric: mean_utility (ratio, not re-scaled); colorbar fixed [0, 1]"
        )
        print(
            "  node size: mean_utility linearly mapped between tree min and max "
            "(relative normalization)"
        )


def _build_hgm_tree(
    nodes: Dict[int, dict],
    children: Dict[int, List[int]],
    color_by: str,
) -> TreeNode:
    etype_map = {"A": "clonal", "B": "reaction", "C": "hybridize"}

    def build(nid: int) -> TreeNode:
        if nid == 0:
            data = {"mean_utility": 0.0, "strategy": "A", "peer_id": None, "resolved": 0}
        else:
            data = nodes[nid]

        strategy = str(data.get("strategy", "A")).upper()
        etype = None if nid == 0 else etype_map.get(strategy, "clonal")
        peer_id = data.get("peer_id")
        ctx = str(peer_id) if peer_id is not None and strategy == "C" else None

        if color_by == "resolved":
            acc = float(data.get("resolved", 0))
        else:
            acc = float(data.get("mean_utility", 0.0))

        child_nodes = [build(c) for c in children.get(nid, [])]
        return TreeNode(
            label=str(nid),
            nid=nid,
            etype=etype,
            acc=acc,
            perf=acc,
            ctx=ctx,
            children=child_nodes,
        )

    return build(0)


def _node_radius(
    perf: float,
    perf_min: float,
    perf_max: float,
    r_max: float,
) -> float:
    r_min = r_max * NODE_R_MIN_RATIO
    if perf_max <= perf_min:
        return r_max
    t = (perf - perf_min) / (perf_max - perf_min)
    return r_min + t * (r_max - r_min)


def draw_tree_circular(
    root: TreeNode,
    *,
    figsize: Tuple[float, float] = FIG_SIZE,
    r_tip: float = 1.0,
    acc_cmap: LinearSegmentedColormap,
    norm: Normalize,
    cbar_label: str,
    tree_title: str = "",
    n_nodes: int = 0,
) -> Tuple[plt.Figure, plt.Axes]:
    row_map = _assign_rows(root)
    depth_map = _assign_depth(root)
    max_depth = max(depth_map.values())
    n_rows = len(row_map)
    all_nodes = _all_nodes(root)
    label_map = {n.label: n for n in all_nodes}

    perf_nodes = [n for n in all_nodes if n.nid != 0]
    perf_min = min((n.perf for n in perf_nodes), default=0.0)
    perf_max = max((n.perf for n in perf_nodes), default=1.0)
    r_max = r_tip * NODE_R_MAX_FRAC
    r_min = r_max * NODE_R_MIN_RATIO
    used_etypes: set = set()

    def acc_col(acc: float) -> Tuple[float, float, float]:
        rgba = acc_cmap(norm(acc))
        return rgba[0], rgba[1], rgba[2]

    def angle_of(node: TreeNode) -> float:
        return _row_angle(row_map[id(node)], n_rows)

    def tip_xy(node: TreeNode) -> Tuple[float, float]:
        return _pol_xy(r_tip, angle_of(node))

    ctx_arcs = [
        (label_map[n.ctx], n)
        for n in all_nodes
        if n.ctx and n.ctx in label_map
    ]

    fig, ax = plt.subplots(figsize=figsize, facecolor=PAPER_WHITE)
    ax.set_facecolor(PAPER_WHITE)
    fig.patch.set_facecolor(PAPER_WHITE)
    ax.set_aspect("equal")
    ax.axis("off")

    def _draw(node: TreeNode) -> None:
        depth = depth_map[id(node)]
        theta = angle_of(node)
        if node.etype is not None:
            used_etypes.add(node.etype)
        col = ECOL.get(node.etype, ECOL[None])
        r0 = _r_at_depth(depth, max_depth, r_tip)
        x0, y0 = _pol_xy(r0, theta)
        x1, y1 = tip_xy(node)

        ax.plot([x0, x1], [y0, y1], color=col, lw=LW,
                solid_capstyle="butt", zorder=2)

        if node.children:
            r_spine = _r_at_depth(depth + 1, max_depth, r_tip)
            t_last = angle_of(node.children[-1])
            xs, ys = _arc_xy(r_spine, theta, t_last)
            ax.plot(xs, ys, color=VERT_COL, lw=LW,
                    solid_capstyle="butt", zorder=1)
            for child in node.children:
                _draw(child)

    _draw(root)

    ctx_y_min: Optional[float] = None
    for src, dst in ctx_arcs:
        a_xy = tip_xy(src)
        b_xy = tip_xy(dst)
        sweep = DEFAULT_CTX_SWEEP * np.pi
        xs, ys = _ctx_arc_xy(a_xy, b_xy, sweep, r_tip)
        _draw_ctx_arc_arrow(ax, xs, ys)
        arc_min = float(np.min(ys))
        ctx_y_min = arc_min if ctx_y_min is None else min(ctx_y_min, arc_min)

    max_node_r = r_max
    for node in all_nodes:
        x, y = tip_xy(node)
        col = acc_col(node.acc)
        text = _node_text(node)
        if node.nid == 0:
            nr = r_min
        else:
            nr = _node_radius(node.perf, perf_min, perf_max, r_max)
        max_node_r = max(max_node_r, nr)
        fs = max(5.0, min(7.5, 5.5 + 2.0 * (nr / r_max)))
        text_color = "#000000"

        face = "#888888" if node.nid == 0 else col
        ax.add_patch(mpatches.Circle(
            (x, y), nr, facecolor=face, edgecolor="none", zorder=7,
        ))

        ax.text(x, y, text, ha="center", va="center",
                fontsize=fs, color=text_color, zorder=9)

    # Fixed horizontal span from tree geometry; extend downward for ctx arcs.
    x_half = r_tip / TREE_WIDTH_FRAC
    y_top = r_max * 1.2
    y_bot = -(r_tip + r_max * 1.5)
    if ctx_y_min is not None:
        y_bot = min(y_bot, ctx_y_min - r_max * 1.5)
    ax.set_xlim(-x_half, x_half)
    ax.set_ylim(y_bot, y_top)
    ax.margins(0)

    if tree_title:
        color_desc = "resolved tasks" if cbar_label == "Resolved tasks" else "utility"
        title_extra = f" · colored by {color_desc}" if cbar_label == "Resolved tasks" else ""
        evals_label = "200 task evals"
        ax.set_title(
            f"{tree_title}\n"
            f"{n_nodes} nodes · {evals_label}{title_extra}",
            fontsize=14, fontweight="bold", pad=8,
        )
        top_margin = 1.0
    else:
        top_margin = 1.0

    fig.subplots_adjust(left=0.06, right=0.98, top=top_margin, bottom=0.02)

    legend_patches = []
    for etype in ("clonal", "reaction", "hybridize"):
        if etype in used_etypes:
            label, color = ETYPE_LEGEND[etype]
            legend_patches.append(
                Line2D([0], [0], color=color, linewidth=LW, label=label),
            )
    if ctx_arcs:
        legend_patches.append(
            Line2D([0, 1], [0, 0], color=CTX_COL, linewidth=LW, alpha=CTX_ALPHA,
                   marker=">", markersize=5, markevery=[1],
                   label="Hybridization Reference"),
        )

    leg = None
    if legend_patches:
        leg = ax.legend(
            handles=legend_patches, loc="lower left", fontsize=LEGEND_FONT_SIZE,
            framealpha=0.0, facecolor="none", edgecolor="none",
            bbox_to_anchor=(0.0, 0.0),
            borderpad=0, borderaxespad=0,
            handletextpad=0.5, handlelength=1.2, labelspacing=0.35,
        )

    sm = plt.cm.ScalarMappable(cmap=acc_cmap, norm=norm)
    sm.set_array([])
    cbar_w = 0.15
    cbar_h = 0.03
    cbar_x = 0.98 - cbar_w
    cax = fig.add_axes([cbar_x, 0.02, cbar_w, cbar_h])
    cb = fig.colorbar(sm, cax=cax, orientation="horizontal")
    cax.set_title(cbar_label, fontsize=LEGEND_FONT_SIZE, pad=5)
    cb.set_ticks([norm.vmin, norm.vmax])
    cb.set_ticklabels(["min", "max"])
    cb.ax.tick_params(
        axis="x", labelsize=LEGEND_FONT_SIZE, pad=1, length=0,
    )
    cb.ax.tick_params(axis="y", left=False, right=False, labelleft=False, labelright=False)
    cb.ax.set_facecolor(PAPER_WHITE)
    cb.outline.set_visible(False)

    if leg is not None:
        fig.canvas.draw()
        renderer = fig.canvas.get_renderer()
        leg_bbox = leg.get_window_extent(renderer).transformed(fig.transFigure.inverted())
        tick_bottom = min(
            lbl.get_window_extent(renderer).transformed(fig.transFigure.inverted()).y0
            for lbl in cb.ax.get_xticklabels()
            if lbl.get_text()
        )
        dy = leg_bbox.y0 - tick_bottom
        pos = cax.get_position()
        cax.set_position([pos.x0, pos.y0 + dy, pos.width, pos.height])

    return fig, ax


def draw_tree(
    run_dir: Path,
    out_path: Optional[Path] = None,
    color_by: str = "utility",
    tree_title: str = "",
    figsize: Tuple[float, float] = FIG_SIZE,
) -> Tuple[Path, Path]:
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

    _log_accuracy_stats(nodes, color_by)

    if color_by == "resolved":
        values = [nodes[nid]["resolved"] for nid in nodes if nid != 0]
        vmax = max(values) if values else 1
        norm = Normalize(vmin=0, vmax=max(vmax, 1))
        acc_cmap = build_accuracy_cmap()
        cbar_label = "Resolved tasks"
    else:
        norm = Normalize(vmin=0, vmax=1)
        acc_cmap = build_accuracy_cmap()
        cbar_label = "Utility"

    root = _build_hgm_tree(nodes, children, color_by)

    fig, _ = draw_tree_circular(
        root,
        figsize=figsize,
        acc_cmap=acc_cmap,
        norm=norm,
        cbar_label=cbar_label,
        tree_title=tree_title,
        n_nodes=len(nodes),
    )

    pdf_path = out_path.with_suffix(".pdf")
    save_kw = dict(
        facecolor=PAPER_WHITE, edgecolor="none",
        transparent=False, bbox_inches="tight", pad_inches=SAVE_PAD_INCHES,
    )
    fig.savefig(out_path, dpi=FIG_DPI, format="png", **save_kw)
    fig.savefig(pdf_path, format="pdf", **save_kw)
    plt.close(fig)
    whiten_png(out_path)
    return out_path, pdf_path


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--run-dir",
        type=Path,
        default=Path("output_polyglot/20260505_122548"),
        help="HGM run directory containing hgm_metadata.jsonl",
    )
    parser.add_argument(
        "--fig-width",
        type=float,
        default=FIG_SIZE[0],
        help=f"Figure width in inches (default: {FIG_SIZE[0]})",
    )
    parser.add_argument(
        "--fig-height",
        type=float,
        default=FIG_SIZE[1],
        help=f"Figure height in inches (default: {FIG_SIZE[1]})",
    )
    parser.add_argument(
        "--out",
        type=Path,
        default=None,
        help="Output PNG path (PDF saved alongside with same stem)",
    )
    parser.add_argument(
        "--color-by",
        choices=["resolved", "utility"],
        default="utility",
        help="Node color and size metric (default: utility)",
    )
    parser.add_argument(
        "--title",
        default="",
        help="Optional tree figure title (default: none)",
    )
    args = parser.parse_args()

    png_path, pdf_path = draw_tree(
        args.run_dir, args.out, color_by=args.color_by,
        tree_title=args.title,
        figsize=(args.fig_width, args.fig_height),
    )
    print(f"Saved: {png_path.resolve()}")
    print(f"Saved: {pdf_path.resolve()}")


if __name__ == "__main__":
    main()
