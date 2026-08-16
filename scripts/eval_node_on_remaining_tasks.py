#!/usr/bin/env python3
"""
Run an evolved HGM node (best agent) on SWE tasks from small.json + medium.json
that are not yet in that node's metadata total_submitted_ids.

The agent is reproduced the same way as during search: base code at init_agent_path
(default: initial_swe/default_agent/src) plus model_patch.diff files from the node
back to initial (see utils.evo_utils.get_model_patch_paths).

Usage (from MendelGM repo root):
  python scripts/eval_node_on_remaining_tasks.py \\
    --node-id 20260421_161727_010449 \\
    --hgm-output-dir output_mgm \\
    --llm Qwen/Qwen3.6-35B-A3B \\
    --max-workers 1

  # Preview only:
  python scripts/eval_node_on_remaining_tasks.py --node-id ... --dry-run
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import time

# Repo root = parent of scripts/
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.common_utils import load_json_file  # noqa: E402
from utils.evo_utils import get_model_patch_paths  # noqa: E402
from llm import resolve_llm_model  # noqa: E402


STATUS_KEYS = {
    "resolved": "total_resolved_ids",
    "unresolved": "total_unresolved_ids",
    "empty": "total_emptypatch_ids",
}


def parse_task_ids(values: list[str] | None, env_value: str | None) -> list[str]:
    task_ids: list[str] = []
    for source in (values or []):
        task_ids.extend(part for part in source.replace(",", " ").replace(":", " ").split() if part)
    if env_value:
        task_ids.extend(part for part in env_value.replace(",", " ").replace(":", " ").split() if part)

    deduped: list[str] = []
    seen: set[str] = set()
    for task_id in task_ids:
        if task_id not in seen:
            seen.add(task_id)
            deduped.append(task_id)
    return deduped


def unique_preserve_order(values: list[str]) -> list[str]:
    deduped: list[str] = []
    seen: set[str] = set()
    for value in values:
        if value not in seen:
            seen.add(value)
            deduped.append(value)
    return deduped


def load_latest_statuses(
    node_dir: str,
    task_ids: list[str],
    started_at: float,
) -> tuple[dict[str, str], list[str]]:
    task_set = set(task_ids)
    statuses: dict[str, str] = {}
    matched_files: list[str] = []
    candidates: list[tuple[float, str, dict]] = []

    for root, _, files in os.walk(node_dir):
        if ".git-bootstrap" in root:
            continue
        for file_name in files:
            if not file_name.endswith(".json"):
                continue
            path = os.path.join(root, file_name)
            try:
                mtime = os.path.getmtime(path)
            except OSError:
                continue
            if mtime + 1 < started_at:
                continue
            try:
                data = load_json_file(path)
            except Exception:
                continue
            if not isinstance(data, dict) or "submitted_ids" not in data:
                continue
            if not (set(data.get("submitted_ids") or []) & task_set):
                continue
            rel_path = os.path.relpath(path, node_dir)
            candidates.append((mtime, rel_path, data))

    for _, rel_path, data in sorted(candidates):
        matched_files.append(rel_path)
        resolved = set(data.get("resolved_ids") or [])
        empty = set(data.get("empty_patch_ids") or [])
        unresolved = set(data.get("unresolved_ids") or [])
        for task_id in set(data.get("submitted_ids") or []) & task_set:
            if task_id in resolved:
                statuses[task_id] = "resolved"
            elif task_id in empty:
                statuses[task_id] = "empty"
            elif task_id in unresolved:
                statuses[task_id] = "unresolved"
            else:
                statuses[task_id] = "unresolved"

    return statuses, unique_preserve_order(matched_files)


def replace_task_statuses(
    original_performance: dict,
    updated_performance: dict,
    task_ids: list[str],
    latest_statuses: dict[str, str],
    latest_files: list[str],
) -> dict:
    task_set = set(task_ids)
    submitted_ids = unique_preserve_order(
        list(original_performance.get("total_submitted_ids") or []) + task_ids
    )

    status_lists = {
        status: [
            task_id
            for task_id in original_performance.get(key, [])
            if task_id not in task_set
        ]
        for status, key in STATUS_KEYS.items()
    }
    for task_id in task_ids:
        status = latest_statuses.get(task_id)
        if status in status_lists:
            status_lists[status].append(task_id)
        else:
            # Keep the prior classification if the rerun did not emit a report for it.
            for old_status, key in STATUS_KEYS.items():
                if task_id in original_performance.get(key, []):
                    status_lists[old_status].append(task_id)
                    break

    resolved_ids = unique_preserve_order(status_lists["resolved"])
    unresolved_ids = unique_preserve_order(status_lists["unresolved"])
    empty_ids = unique_preserve_order(status_lists["empty"])
    files = unique_preserve_order(
        list(original_performance.get("files") or [])
        + list(updated_performance.get("files") or [])
        + latest_files
    )

    total_submitted = len(submitted_ids)
    total_resolved = len(resolved_ids)
    return {
        "accuracy_score": total_resolved / total_submitted if total_submitted else 0,
        "total_resolved_instances": total_resolved,
        "total_submitted_instances": total_submitted,
        "files": files,
        "total_unresolved_ids": unresolved_ids,
        "total_emptypatch_ids": empty_ids,
        "total_resolved_ids": resolved_ids,
        "total_submitted_ids": submitted_ids,
    }


def save_metadata(metadata: dict, node_dir: str) -> None:
    with open(os.path.join(node_dir, "metadata.json"), "w") as f:
        json.dump(metadata, f, indent=4)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--node-id",
        type=str,
        required=True,
        help="Commit folder name under hgm-output-dir, e.g. 20260421_161727_010449",
    )
    parser.add_argument(
        "--hgm-output-dir",
        type=str,
        default="output_mgm",
        help="Path relative to repo root (or absolute) where node folders live",
    )
    parser.add_argument(
        "--small-json",
        type=str,
        default="swe_bench/subsets/small.json",
    )
    parser.add_argument(
        "--medium-json",
        type=str,
        default="swe_bench/subsets/medium.json",
    )
    parser.add_argument(
        "--init-agent-src",
        type=str,
        default="initial_swe/default_agent/src",
        help="Base agent source; patches from the node chain are applied on top",
    )
    parser.add_argument("--llm", type=str, default=None)
    parser.add_argument(
        "--eval-timeout",
        type=int,
        default=7200,
        help="Per-task timeout in seconds (passed to harness / coding_agent)",
    )
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument(
        "--task-ids",
        nargs="*",
        default=None,
        help=(
            "Explicit SWE task IDs to run instead of metadata remaining tasks. "
            "Also accepts comma/colon/space separated IDs via EVAL_TASK_IDS."
        ),
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print remaining task IDs and counts, then exit",
    )
    args = parser.parse_args()

    os.chdir(REPO_ROOT)
    hgm_dir = os.path.abspath(
        args.hgm_output_dir
        if os.path.isabs(args.hgm_output_dir)
        else os.path.join(REPO_ROOT, args.hgm_output_dir)
    )
    hgm_dir_rel = os.path.relpath(hgm_dir, REPO_ROOT)

    meta_path = os.path.join(hgm_dir, args.node_id, "metadata.json")
    if not os.path.isfile(meta_path):
        print(f"Missing metadata: {meta_path}", file=sys.stderr)
        sys.exit(1)

    meta = load_json_file(meta_path)
    original_performance = dict(meta.get("overall_performance") or {})
    submitted = set(meta.get("overall_performance", {}).get("total_submitted_ids", []))

    small = load_json_file(
        args.small_json
        if os.path.isabs(args.small_json)
        else os.path.join(REPO_ROOT, args.small_json)
    )
    medium = load_json_file(
        args.medium_json
        if os.path.isabs(args.medium_json)
        else os.path.join(REPO_ROOT, args.medium_json)
    )

    seen: set[str] = set()
    universe: list[str] = []
    for t in list(small) + list(medium):
        if t not in seen:
            seen.add(t)
            universe.append(t)

    remaining = [t for t in universe if t not in submitted]
    explicit_task_ids = parse_task_ids(args.task_ids, os.environ.get("EVAL_TASK_IDS"))
    unknown_task_ids = [t for t in explicit_task_ids if t not in seen]
    if unknown_task_ids:
        print(
            "Unknown task IDs requested: " + ", ".join(unknown_task_ids),
            file=sys.stderr,
        )
        sys.exit(2)
    tasks_to_run = explicit_task_ids if explicit_task_ids else remaining

    print(f"Node: {args.node_id}")
    print(f"Universe (small+medium, deduped): {len(universe)} tasks")
    print(f"Already submitted (from metadata): {len(submitted)} ids")
    print(f"Remaining to run: {len(remaining)}")
    if explicit_task_ids:
        print(f"Explicit task IDs requested: {len(explicit_task_ids)}")
    print(f"Tasks selected for this run: {len(tasks_to_run)}")

    patch_paths = get_model_patch_paths(REPO_ROOT, hgm_dir_rel, args.node_id)
    print(f"Model patches (ancestor chain): {len(patch_paths)}")

    init_src = (
        args.init_agent_src
        if os.path.isabs(args.init_agent_src)
        else os.path.join(REPO_ROOT, args.init_agent_src)
    )
    if not os.path.isdir(init_src):
        print(f"init_agent_src not found: {init_src}", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        for t in tasks_to_run:
            print(t)
        return

    if not tasks_to_run:
        print("Nothing to run.")
        return

    import hgm_utils  # noqa: E402
    import swe_bench.harness  # noqa: E402

    # Same task universe as hgm.py (for any logic that scans total_tasks)
    llm_model = resolve_llm_model(args.llm)
    hgm_utils.init(
        False,
        hgm_dir_rel,
        universe,
        _n_task_evals=0,
        _llm=llm_model,
        _timeout=args.eval_timeout,
    )
    swe_bench.harness.llm = llm_model
    swe_bench.harness.timeout = args.eval_timeout

    print("Starting eval_agent (updates node metadata.json when done)...")
    eval_started_at = time.time()
    hgm_utils.eval_agent(
        args.node_id,
        tasks=tasks_to_run,
        max_workers=args.max_workers,
        skip=False,
        init_agent_path=init_src,
    )

    if explicit_task_ids:
        metadata_after_eval = load_json_file(meta_path)
        latest_statuses, latest_files = load_latest_statuses(
            os.path.join(hgm_dir, args.node_id),
            explicit_task_ids,
            eval_started_at,
        )
        corrected_performance = replace_task_statuses(
            original_performance,
            metadata_after_eval.get("overall_performance") or {},
            explicit_task_ids,
            latest_statuses,
            latest_files,
        )
        metadata_after_eval["overall_performance"] = corrected_performance
        save_metadata(metadata_after_eval, os.path.join(hgm_dir, args.node_id))
        missing = [task_id for task_id in explicit_task_ids if task_id not in latest_statuses]
        print("Corrected explicit rerun performance:")
        print(
            f"  resolved={corrected_performance['total_resolved_instances']} "
            f"submitted={corrected_performance['total_submitted_instances']} "
            f"accuracy={corrected_performance['accuracy_score']:.6f}"
        )
        if missing:
            print("  Warning: no fresh report found for: " + ", ".join(missing))
    print("Done.")


if __name__ == "__main__":
    main()
