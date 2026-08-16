#!/usr/bin/env python3
"""
Preprocess HGM/MGM run archives into compact web tree JSON for docs/assets.

For each node in hgm_metadata.jsonl snapshots, operator type and hybridization
peer come from that commit's metadata.json under the run directory:

  <run-dir>/<commit_id>/metadata.json
    self_improve_strategy  → A|B|C  (clonal / reaction / hybridize)
    peer_commit            → mapped to peer node id when strategy is C

Default MGM source: docs/assets/mgm_meta (copied from the experiment run).

Pass rates stick to the archive snapshots (tree_mgm.json / hgm_metadata.jsonl),
which already store metadata-sourced counts at each write (see hgm.py
update_metadata):
  submitted = num_evals
  resolved  = round(mean_utility * num_evals)   # integer solves
  pass      = f\"{resolved}/{submitted}\"
Same formula on every frame — no final-frame override.
"""
from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Dict, List, Optional, Tuple

ROOT = Path(__file__).resolve().parents[1]

ETYPE = {"A": "clonal", "B": "reaction", "C": "hybridize"}


def load_snapshots(path: Path) -> List[dict]:
    raw = path.read_text(encoding="utf-8").strip()
    parts = raw.split("\n}\n{")
    out: List[dict] = []
    for part in parts:
        if not part.startswith("{"):
            part = "{" + part
        if not part.endswith("}"):
            part = part + "}"
        out.append(json.loads(part))
    return out


def pass_from_snapshot(node: dict) -> Tuple[float, int, int, Optional[str]]:
    """
    Utility + integer pass counts from one archive node record.

    hgm.py writes mean_utility = resolved/submitted and num_evals = submitted
    from overall_performance, so recovering resolved via rounding is exact
    for ratios that came from integers.
    """
    submitted = int(node.get("num_evals") or 0)
    utility = float(node.get("mean_utility") or 0.0)
    if submitted <= 0:
        return round(utility, 6), 0, 0, None
    resolved = int(round(utility * submitted))
    resolved = max(0, min(submitted, resolved))
    # Keep utility coherent with the integer pass fraction.
    utility = resolved / submitted
    return round(utility, 6), resolved, submitted, f"{resolved}/{submitted}"


def enrich_from_run_dir(
    run_dir: Path,
    final_nodes: List[dict],
    *,
    require_meta: bool,
) -> Dict[int, dict]:
    """Return per-node strategy + peer from metadata.json (not pass rates)."""
    commit_to_id = {"initial": 0}
    for n in final_nodes:
        commit_to_id[n["commit_id"]] = int(n["id"])

    enrich: Dict[int, dict] = {}
    missing: List[str] = []
    for n in final_nodes:
        nid = int(n["id"])
        commit_id = n["commit_id"]
        meta_path = run_dir / commit_id / "metadata.json"
        strategy = "A"
        peer_id = None
        if not meta_path.exists():
            missing.append(commit_id)
            if require_meta:
                continue
        else:
            meta = json.loads(meta_path.read_text(encoding="utf-8"))
            strategy = str(meta.get("self_improve_strategy", "A")).upper()
            peer_commit = meta.get("peer_commit")
            if peer_commit:
                peer_id = commit_to_id.get(peer_commit)
                if peer_id is None and require_meta:
                    raise SystemExit(
                        f"node {nid} ({commit_id}): peer_commit {peer_commit!r} "
                        f"not found in archive"
                    )
        enrich[nid] = {"strategy": strategy, "peer_id": peer_id}

    if require_meta and missing:
        raise SystemExit(
            f"missing metadata.json for {len(missing)} commits under {run_dir}: "
            + ", ".join(missing[:5])
            + ("…" if len(missing) > 5 else "")
        )
    return enrich


def build_web_payload(
    snapshots: List[dict],
    *,
    label: str,
    default_strategy: str,
    run_enrich: Dict[int, dict],
) -> dict:
    final = snapshots[-1]
    final_by_id = {int(n["id"]): n for n in final["nodes"]}

    born: Dict[int, int] = {}
    for snap in snapshots:
        e = int(snap["n_task_evals"])
        for n in snap["nodes"]:
            nid = int(n["id"])
            if nid not in born:
                born[nid] = e

    nodes_out: List[dict] = [
        {
            "id": 0,
            "parent": None,
            "strategy": None,
            "peer_id": None,
            "born": 0,
        }
    ]
    for nid in sorted(final_by_id):
        n = final_by_id[nid]
        if nid in run_enrich:
            strat_code = run_enrich[nid]["strategy"]
            peer_id = run_enrich[nid]["peer_id"]
        else:
            strat_code = default_strategy
            peer_id = None
        if strat_code != "C":
            peer_id = None
        _u, resolved, submitted, pass_s = pass_from_snapshot(n)
        nodes_out.append(
            {
                "id": nid,
                "parent": int(n["parent_id"]),
                "strategy": ETYPE.get(strat_code, "clonal"),
                "peer_id": peer_id,
                "born": born.get(nid, 0),
                "resolved": resolved,
                "submitted": submitted,
                "pass": pass_s,
            }
        )

    frames: List[dict] = []
    prev_key: Optional[tuple] = None
    for snap in snapshots:
        utils = {}
        passes = {}
        for n in snap["nodes"]:
            nid = str(int(n["id"]))
            u, _r, _s, p = pass_from_snapshot(n)
            utils[nid] = u
            if p:
                passes[nid] = p
        key = (
            int(snap["n_task_evals"]),
            tuple(sorted(utils.items())),
            tuple(sorted(passes.items())),
        )
        if key == prev_key:
            continue
        prev_key = key
        frames.append(
            {
                "evals": int(snap["n_task_evals"]),
                "utils": utils,
                "pass": passes,
            }
        )

    return {
        "label": label,
        "max_evals": int(final["n_task_evals"]),
        "n_nodes": len(final_by_id),
        "nodes": nodes_out,
        "frames": frames,
    }


def process_one(
    *,
    label: str,
    dst: Path,
    default_strategy: str,
    run_dir: Optional[Path],
    snapshots_path: Optional[Path],
    require_meta: bool,
) -> None:
    if run_dir is not None:
        meta_jsonl = run_dir / "hgm_metadata.jsonl"
        if meta_jsonl.exists():
            src = meta_jsonl
        elif snapshots_path is not None and snapshots_path.exists():
            src = snapshots_path
        else:
            raise SystemExit(f"{label}: no hgm_metadata.jsonl under {run_dir}")
    elif snapshots_path is not None and snapshots_path.exists():
        src = snapshots_path
    else:
        raise SystemExit(f"{label}: need --run-dir or snapshots JSON")

    snapshots = load_snapshots(src)
    final_nodes = snapshots[-1]["nodes"]
    run_enrich: Dict[int, dict] = {}
    if run_dir is not None:
        run_enrich = enrich_from_run_dir(
            run_dir, final_nodes, require_meta=require_meta
        )

    payload = build_web_payload(
        snapshots,
        label=label,
        default_strategy=default_strategy,
        run_enrich=run_enrich,
    )
    dst.write_text(json.dumps(payload, separators=(",", ":")) + "\n", encoding="utf-8")

    by_strat: Dict[str, int] = {}
    for n in payload["nodes"]:
        s = n.get("strategy") or "root"
        by_strat[s] = by_strat.get(s, 0) + 1
    n_peer = sum(1 for n in payload["nodes"] if n.get("peer_id") is not None)
    print(
        f"{label}: {len(snapshots)} snapshots -> {len(payload['frames'])} frames, "
        f"{payload['n_nodes']} nodes, strategies={by_strat}, {n_peer} CH refs -> "
        f"{dst.relative_to(ROOT)} ({dst.stat().st_size:,} bytes)"
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--mgm-run-dir",
        type=Path,
        default=ROOT / "docs/assets/mgm_meta",
        help="MGM run dir with hgm_metadata.jsonl + <commit>/metadata.json",
    )
    parser.add_argument(
        "--hgm-run-dir",
        type=Path,
        default=None,
        help="Optional HGM run dir (same layout). Else clonal-only from tree_hgm.json",
    )
    parser.add_argument(
        "--skip-hgm",
        action="store_true",
        help="Only rebuild MGM web JSON",
    )
    args = parser.parse_args()

    assets = ROOT / "docs/assets"
    if not args.mgm_run_dir.exists():
        raise SystemExit(f"MGM run dir not found: {args.mgm_run_dir}")

    process_one(
        label="MGM",
        dst=assets / "tree_mgm_web.json",
        default_strategy="A",
        run_dir=args.mgm_run_dir,
        snapshots_path=assets / "tree_mgm.json",
        require_meta=True,
    )

    if args.skip_hgm:
        return

    hgm_dst = assets / "tree_hgm_web.json"
    hgm_src = assets / "tree_hgm.json"
    if args.hgm_run_dir is not None:
        process_one(
            label="HGM",
            dst=hgm_dst,
            default_strategy="A",
            run_dir=args.hgm_run_dir,
            snapshots_path=hgm_src if hgm_src.exists() else None,
            require_meta=True,
        )
    elif hgm_src.exists():
        process_one(
            label="HGM",
            dst=hgm_dst,
            default_strategy="A",
            run_dir=None,
            snapshots_path=hgm_src,
            require_meta=False,
        )


if __name__ == "__main__":
    main()
