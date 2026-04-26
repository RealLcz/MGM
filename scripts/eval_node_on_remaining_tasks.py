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
    --llm Qwen/Qwen3-Coder-Next \\
    --max-workers 4

  # Preview only:
  python scripts/eval_node_on_remaining_tasks.py --node-id ... --dry-run
"""

from __future__ import annotations

import argparse
import os
import sys

# Repo root = parent of scripts/
REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.common_utils import load_json_file  # noqa: E402
from utils.evo_utils import get_model_patch_paths  # noqa: E402


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
    parser.add_argument("--llm", type=str, default="gpt-5")
    parser.add_argument(
        "--eval-timeout",
        type=int,
        default=3600,
        help="Per-task timeout in seconds (passed to harness / coding_agent)",
    )
    parser.add_argument("--max-workers", type=int, default=4)
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

    print(f"Node: {args.node_id}")
    print(f"Universe (small+medium, deduped): {len(universe)} tasks")
    print(f"Already submitted (from metadata): {len(submitted)} ids")
    print(f"Remaining to run: {len(remaining)}")

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
        for t in remaining:
            print(t)
        return

    if not remaining:
        print("Nothing to run.")
        return

    import hgm_utils  # noqa: E402
    import swe_bench.harness  # noqa: E402

    # Same task universe as hgm.py (for any logic that scans total_tasks)
    hgm_utils.init(
        False,
        hgm_dir_rel,
        universe,
        _n_task_evals=0,
        _llm=args.llm,
        _timeout=args.eval_timeout,
    )
    swe_bench.harness.llm = args.llm
    swe_bench.harness.timeout = args.eval_timeout

    print("Starting eval_agent (updates node metadata.json when done)...")
    hgm_utils.eval_agent(
        args.node_id,
        tasks=remaining,
        max_workers=args.max_workers,
        skip=False,
        init_agent_path=init_src,
    )
    print("Done.")


if __name__ == "__main__":
    main()
