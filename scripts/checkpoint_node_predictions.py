#!/usr/bin/env python3
"""
Run make_report on an existing predictions folder and merge results into node metadata.json.

Usage (from repo root, with DOCKER_HOST pointing at remote/local Docker):
  python scripts/checkpoint_node_predictions.py \\
    --node-id 20260616_024835_287218 \\
    --predictions-subdir 20260616_024835_287218_29 \\
    --hgm-output-dir output_mgm \\
    --num-eval-procs 2
"""

from __future__ import annotations

import argparse
import json
import os
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.common_utils import load_json_file  # noqa: E402
from utils.evo_utils import get_all_performance  # noqa: E402


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", required=True)
    parser.add_argument(
        "--predictions-subdir",
        required=True,
        help="Folder name under node/predictions/, e.g. 20260616_024835_287218_29",
    )
    parser.add_argument("--hgm-output-dir", default="output_mgm")
    parser.add_argument("--num-eval-procs", type=int, default=2)
    parser.add_argument(
        "--run-id",
        default=None,
        help="SWE-bench run_id (default: {node_id}_0)",
    )
    parser.add_argument("--dry-run", action="store_true")
    args = parser.parse_args()

    os.chdir(REPO_ROOT)
    hgm_dir = os.path.abspath(
        args.hgm_output_dir
        if os.path.isabs(args.hgm_output_dir)
        else os.path.join(REPO_ROOT, args.hgm_output_dir)
    )
    node_dir = os.path.join(hgm_dir, args.node_id)
    meta_path = os.path.join(node_dir, "metadata.json")
    pred_dir = os.path.join(node_dir, "predictions", args.predictions_subdir)

    if not os.path.isfile(meta_path):
        print(f"Missing metadata: {meta_path}", file=sys.stderr)
        sys.exit(1)
    if not os.path.isdir(pred_dir):
        print(f"Missing predictions dir: {pred_dir}", file=sys.stderr)
        sys.exit(1)

    pred_jsons = [
        f for f in os.listdir(pred_dir) if f.endswith(".json") and not f.startswith("all_")
    ]
    print(f"Node: {args.node_id}")
    print(f"Predictions dir: {pred_dir}")
    print(f"Prediction files: {len(pred_jsons)}")
    for name in sorted(pred_jsons):
        print(f"  - {name}")

    if args.dry_run:
        return

    run_id = args.run_id or f"{args.node_id}_0"
    rel_pred_dir = os.path.relpath(pred_dir, REPO_ROOT)
    rel_node_dir = os.path.relpath(node_dir, REPO_ROOT)

    from swe_bench.report import make_report  # noqa: E402

    print(f"Running make_report (run_id={run_id}, num_eval_procs={args.num_eval_procs})...")
    make_report(
        [rel_pred_dir],
        run_ids=[run_id],
        output_dir=rel_node_dir,
        num_eval_procs=args.num_eval_procs,
    )

    _, overall_performance = get_all_performance(
        args.node_id, results_dir=rel_node_dir, does_print=True
    )
    metadata = load_json_file(meta_path)
    metadata["overall_performance"] = overall_performance
    metadata["swe_dnames"] = list(
        dict.fromkeys(
            (metadata.get("swe_dnames") or [])
            + [pred_dir]
        )
    )

    with open(meta_path, "w") as f:
        json.dump(metadata, f, indent=4)

    perf = overall_performance
    print("Updated metadata.json:")
    print(
        f"  submitted={perf['total_submitted_instances']} "
        f"resolved={perf['total_resolved_instances']} "
        f"accuracy={perf['accuracy_score']:.6f}"
    )
    print("Done.")


if __name__ == "__main__":
    main()
