#!/usr/bin/env python3
"""Re-run SWE-bench grading on existing harness prediction directories."""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from hgm_utils import _collect_swe_report_files  # noqa: E402
from self_improve_step import save_metadata  # noqa: E402
from swe_bench.report import make_report  # noqa: E402
from utils.common_utils import load_json_file  # noqa: E402
from utils.evo_utils import get_all_performance, normalize_overall_performance  # noqa: E402


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--node-id", required=True)
    parser.add_argument(
        "--hgm-output-dir",
        default="output_mgm",
        help="output_mgm or output_hgm",
    )
    parser.add_argument(
        "--pred-subdir",
        required=True,
        help="Prediction folder name under predictions/, e.g. 20260616_024835_287218_38",
    )
    parser.add_argument(
        "--run-id",
        default=None,
        help="SWE-bench run_id (defaults to pred-subdir suffix after node id)",
    )
    parser.add_argument(
        "--dataset-name",
        default=None,
        help="Grading dataset (default: SWE_EVAL_DATASET or SWE-bench)",
    )
    parser.add_argument("--max-workers", type=int, default=16)
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    node_dir = os.path.join(REPO_ROOT, args.hgm_output_dir, args.node_id)
    pred_dir = os.path.join(node_dir, "predictions", args.pred_subdir)
    if not os.path.isdir(pred_dir):
        raise SystemExit(f"Prediction dir not found: {pred_dir}")

    run_id = args.run_id or f"{args.node_id}_0"
    rel_pred = os.path.relpath(pred_dir, REPO_ROOT)
    started_at = time.time()
    dataset_name = args.dataset_name or os.environ.get(
        "SWE_EVAL_DATASET", "princeton-nlp/SWE-bench"
    )

    print(f"Node: {args.node_id}")
    print(f"Predictions: {pred_dir}")
    print(f"run_id: {run_id}")
    print(f"dataset_name: {dataset_name}")
    print(f"max_workers: {args.max_workers}")

    make_report(
        [rel_pred],
        run_ids=[run_id],
        dataset_name=dataset_name,
        output_dir=os.path.join(args.hgm_output_dir, args.node_id),
        num_eval_procs=args.max_workers,
    )

    results_subdir = os.path.join(REPO_ROOT, args.hgm_output_dir, args.node_id)
    _collect_swe_report_files(REPO_ROOT, results_subdir, [run_id])
    _, overall_performance = get_all_performance(
        args.node_id, results_dir=os.path.join(args.hgm_output_dir, args.node_id)
    )
    overall_performance = normalize_overall_performance(overall_performance)

    meta_path = os.path.join(node_dir, "metadata.json")
    metadata = load_json_file(meta_path) if os.path.exists(meta_path) else {}
    metadata["overall_performance"] = overall_performance
    metadata["swe_grade_only"] = {
        "pred_subdir": args.pred_subdir,
        "run_id": run_id,
        "graded_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "elapsed_sec": round(time.time() - started_at, 1),
    }
    save_metadata(metadata, node_dir)

    print(
        "Grading complete: "
        f"resolved={overall_performance.get('total_resolved_instances', 0)} "
        f"submitted={overall_performance.get('total_submitted_instances', 0)} "
        f"accuracy={overall_performance.get('accuracy_score', 0.0):.4f}"
    )


if __name__ == "__main__":
    main()
