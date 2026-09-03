#!/usr/bin/env python3
"""
Re-run specific SWE-bench tasks using the already-evolved best agent code
(with bug fixes applied), skipping evolution patch application.

This is used to re-test tasks where the diff minimality bug caused empty patches.
"""

from __future__ import annotations

import json
import os
import sys
import time

REPO_ROOT = os.path.abspath(os.path.dirname(os.path.dirname(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from swe_bench.harness import swe_harness  # noqa: E402
from swe_bench.report import make_report  # noqa: E402
from hgm_utils import _collect_swe_report_files, get_all_performance  # noqa: E402
from utils.common_utils import load_json_file  # noqa: E402
from utils.evo_utils import normalize_overall_performance  # noqa: E402


def main():
    node_id = os.environ.get("EVAL_NODE_ID", "20260616_024835_287218")
    hgm_output_dir = os.environ.get("HGM_OUTPUT_DIR", "output_mgm")
    task_ids_str = os.environ.get("EVAL_TASK_IDS", "")
    max_workers = int(os.environ.get("EVAL_MAX_WORKERS", "4"))
    timeout = int(os.environ.get("EVAL_TIMEOUT", "7200"))

    # Use the best_agent directory which already has evolution patches + fixes applied
    init_agent_path = os.environ.get(
        "EVAL_INIT_AGENT_SRC",
        os.path.join(REPO_ROOT, "best_agent/mgm_20260616_024835_287218/src"),
    )

    if not task_ids_str:
        print("ERROR: EVAL_TASK_IDS not set", file=sys.stderr)
        sys.exit(1)

    task_ids = [t.strip() for t in task_ids_str.replace(":", ",").replace(" ", ",").split(",") if t.strip()]
    print(f"Node: {node_id}")
    print(f"Tasks to re-run: {task_ids}")
    print(f"Max workers: {max_workers}")
    print(f"Timeout: {timeout}")
    print(f"Init agent path: {init_agent_path}")

    if not os.path.isdir(init_agent_path):
        print(f"ERROR: init_agent_path not found: {init_agent_path}", file=sys.stderr)
        sys.exit(1)

    # Load the SWE-bench dataset and filter to the requested tasks
    from swe_bench.harness import load_swebench_dataset
    full_dataset = load_swebench_dataset(
        os.environ.get("SWE_EVAL_DATASET", "princeton-nlp/SWE-bench"),
        split="test",
        instance_ids=task_ids,
    )
    print(f"Loaded {len(full_dataset)} dataset entries")

    if not full_dataset:
        print("ERROR: No dataset entries found for the requested tasks", file=sys.stderr)
        sys.exit(1)

    # Determine prediction subdirectory (use _41 for this re-run)
    node_dir = os.path.join(REPO_ROOT, hgm_output_dir, node_id)
    pred_base = os.path.join(node_dir, "predictions")
    existing_subdirs = sorted([d for d in os.listdir(pred_base) if d.startswith(f"{node_id}_")]) if os.path.isdir(pred_base) else []
    pred_subdir_num = 41
    for d in existing_subdirs:
        try:
            num = int(d.split("_")[-1])
            if num >= pred_subdir_num:
                pred_subdir_num = num + 1
        except ValueError:
            continue
    pred_subdir = f"{node_id}_{pred_subdir_num}"
    pred_dname = os.path.join(pred_base, pred_subdir)
    os.makedirs(pred_dname, exist_ok=True)
    print(f"Prediction subdir: {pred_subdir}")
    print(f"Prediction dir: {pred_dname}")

    # Run the harness with EMPTY model_patch_paths (patches already applied in best_agent)
    print("\nStarting swe_harness (no evolution patches, using fixed best_agent code)...")
    eval_started_at = time.time()

    dnames = swe_harness(
        test_task_list=task_ids,
        max_workers=min(max_workers, len(task_ids)),
        model_name_or_path=pred_subdir,
        model_patch_paths=[],  # Empty: evolution patches already applied in best_agent
        pred_dname=pred_dname,
        init_agent_path=init_agent_path,
    )

    eval_elapsed = time.time() - eval_started_at
    print(f"\nHarness completed in {eval_elapsed:.1f}s")
    print(f"Prediction subdirs: {dnames}")

    # Grade the results
    print("\nGrading results...")
    run_id = f"{node_id}_0"
    make_report(
        dnames,
        run_ids=[run_id],
        dataset_name=os.environ.get("SWE_EVAL_DATASET", "princeton-nlp/SWE-bench"),
        output_dir=os.path.join(hgm_output_dir, node_id),
        num_eval_procs=max_workers,
    )

    # Collect and display results
    results_subdir = os.path.join(REPO_ROOT, hgm_output_dir, node_id)
    _collect_swe_report_files(REPO_ROOT, results_subdir, [run_id])
    _, overall_performance = get_all_performance(node_id, results_dir=results_subdir)
    overall_performance = normalize_overall_performance(overall_performance)

    resolved = overall_performance.get("total_resolved_ids", [])
    submitted = overall_performance.get("total_submitted_ids", [])
    print(f"\n=== Results ===")
    print(f"Resolved: {len(resolved)}/{len(submitted)}")
    print(f"Resolved IDs: {resolved}")
    print(f"Accuracy: {len(resolved)}/{len(submitted)} = {len(resolved)/max(len(submitted),1):.4f}")


if __name__ == "__main__":
    main()
