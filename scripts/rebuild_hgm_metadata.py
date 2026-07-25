#!/usr/bin/env python3
"""
Rebuild ``hgm_metadata.jsonl`` for an HGM run directory by scanning every node
sub-folder's ``metadata.json``.

Why this exists:
- ``hgm.py::initialize_run`` (when ``paths.continue_from`` is set) reads
  ``<run_dir>/hgm_metadata.jsonl`` (last record) to reconstruct the search tree.
  If that file is missing or stale, startup raises FileNotFoundError or recovers
  the wrong tree shape.
- Each node folder already contains ``metadata.json`` with ``parent_commit`` and
  ``overall_performance`` — enough to rebuild the snapshot deterministically.

Output JSONL contains a single record (``last_only=True`` is what the loader
uses). IDs are reassigned 1..N in chronological order (``initial`` keeps id=0)
so they are dense — avoids the latent ``len(nodes)``-based collision in
``tree.Node.__init__`` when later nodes are appended.

Usage:
    python scripts/rebuild_hgm_metadata.py --run-dir output_mgm
    python scripts/rebuild_hgm_metadata.py --run-dir output_mgm --dry-run
    python scripts/rebuild_hgm_metadata.py --run-dir output_mgm --remove-orphans
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import time
from typing import Any, Dict, List


def _is_node_dir(name: str) -> bool:
    # Node folders are timestamps like 20260409_134545_289259.
    if name == "initial":
        return False
    parts = name.split("_")
    if len(parts) != 3:
        return False
    return all(p.isdigit() for p in parts)


def _load(run_dir: str):
    nodes: List[Dict[str, Any]] = []
    orphans: List[str] = []
    for entry in sorted(os.listdir(run_dir)):
        full = os.path.join(run_dir, entry)
        if not os.path.isdir(full) or not _is_node_dir(entry):
            continue
        meta_path = os.path.join(full, "metadata.json")
        if not os.path.isfile(meta_path):
            orphans.append(entry)
            continue
        with open(meta_path) as f:
            meta = json.load(f)
        perf = (meta.get("overall_performance") or {})
        submitted = int(perf.get("total_submitted_instances", 0) or 0)
        resolved = int(perf.get("total_resolved_instances", 0) or 0)
        nodes.append({
            "commit_id": entry,
            "parent_commit": meta.get("parent_commit") or "initial",
            "submitted": submitted,
            "resolved": resolved,
        })
    return nodes, orphans


def _build_record(nodes: List[Dict[str, Any]]) -> Dict[str, Any]:
    nodes_sorted = sorted(nodes, key=lambda n: n["commit_id"])
    commit_to_id: Dict[str, int] = {"initial": 0}
    for idx, n in enumerate(nodes_sorted, start=1):
        commit_to_id[n["commit_id"]] = idx

    payload: List[Dict[str, Any]] = []
    n_task_evals = 0
    for n in nodes_sorted:
        nid = commit_to_id[n["commit_id"]]
        parent = n["parent_commit"] or "initial"
        if parent not in commit_to_id:
            raise SystemExit(
                f"Unknown parent {parent!r} for {n['commit_id']!r}; cannot rebuild."
            )
        submitted = n["submitted"]
        resolved = n["resolved"]
        mean_utility = (resolved / submitted) if submitted else 0.0
        payload.append({
            "commit_id": n["commit_id"],
            "id": nid,
            "parent_id": commit_to_id[parent],
            "mean_utility": mean_utility,
            "num_evals": submitted,
        })
        n_task_evals += submitted
    return {"n_task_evals": n_task_evals, "nodes": payload}


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("--run-dir", required=True, help="HGM output dir, e.g. output_mgm")
    p.add_argument(
        "--out", default=None,
        help="Output jsonl path (default: <run-dir>/hgm_metadata.jsonl).",
    )
    p.add_argument("--dry-run", action="store_true")
    p.add_argument(
        "--remove-orphans", action="store_true",
        help="Delete timestamp dirs lacking metadata.json (failed self-improve).",
    )
    args = p.parse_args()

    run_dir = os.path.abspath(args.run_dir)
    if not os.path.isdir(run_dir):
        print(f"ERROR: run-dir not found: {run_dir}", file=sys.stderr)
        return 1
    out_path = args.out or os.path.join(run_dir, "hgm_metadata.jsonl")

    nodes, orphans = _load(run_dir)
    record = _build_record(nodes)

    print(f"Run dir: {run_dir}")
    print(f"Found nodes with metadata.json: {len(nodes)}")
    print(f"Orphan timestamp dirs (no metadata.json): {len(orphans)}")
    for o in orphans:
        print(f"  orphan: {o}")
    print(f"n_task_evals = {record['n_task_evals']}")
    for n in record["nodes"]:
        print(f"  id={n['id']:>3}  parent={n['parent_id']:>3}  "
              f"{n['commit_id']}  evals={n['num_evals']}  "
              f"util={n['mean_utility']:.4f}")

    if args.dry_run:
        print("--dry-run: not writing output.")
        return 0

    if os.path.exists(out_path):
        backup = f"{out_path}.bak.{time.strftime('%Y%m%d_%H%M%S')}"
        shutil.copy2(out_path, backup)
        print(f"Existing jsonl backed up to: {backup}")

    with open(out_path, "w") as f:
        f.write(json.dumps(record, indent=2) + "\n")
    print(f"Wrote single-record snapshot to: {out_path}")

    if args.remove_orphans and orphans:
        for o in orphans:
            full = os.path.join(run_dir, o)
            shutil.rmtree(full)
            print(f"Removed orphan dir: {full}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
