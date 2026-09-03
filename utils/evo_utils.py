# This file is adapted from https://github.com/jennyzzt/dgm.

import json
import os

from utils.common_utils import load_json_file, read_file

# Higher value wins when the same instance appears in multiple grading reports.
_INSTANCE_STATUS_PRIORITY = {
    "resolved": 4,
    "emptypatch": 3,
    "unresolved": 2,
    "error": 1,
}


def _instance_statuses_from_report(eval_results: dict) -> dict[str, str]:
    """Map instance_id -> coarse status from one SWE-bench grading report."""
    resolved = set(eval_results.get("resolved_ids") or [])
    unresolved = set(eval_results.get("unresolved_ids") or [])
    empty = set(eval_results.get("empty_patch_ids") or [])
    errors = set(eval_results.get("error_ids") or [])
    submitted = list(eval_results.get("submitted_ids") or [])
    if not submitted:
        submitted = list(resolved | unresolved | empty | errors)

    statuses: dict[str, str] = {}
    for inst in submitted:
        if inst in resolved:
            statuses[inst] = "resolved"
        elif inst in unresolved:
            statuses[inst] = "unresolved"
        elif inst in empty:
            statuses[inst] = "emptypatch"
        elif inst in errors:
            statuses[inst] = "error"
        else:
            statuses[inst] = "unresolved"

    for inst in resolved:
        statuses[inst] = "resolved"
    for inst in unresolved:
        if statuses.get(inst) != "resolved":
            statuses[inst] = "unresolved"
    for inst in empty:
        if statuses.get(inst) not in ("resolved", "unresolved"):
            statuses[inst] = "emptypatch"
    for inst in errors:
        if statuses.get(inst) != "resolved":
            statuses[inst] = "error"
    return statuses


def _merge_instance_statuses(
    base: dict[str, str], new: dict[str, str]
) -> dict[str, str]:
    merged = dict(base)
    for inst, status in new.items():
        if inst not in merged:
            merged[inst] = status
            continue
        if _INSTANCE_STATUS_PRIORITY[status] > _INSTANCE_STATUS_PRIORITY[merged[inst]]:
            merged[inst] = status
    return merged


def overall_performance_from_instance_statuses(
    statuses: dict[str, str], files: list[str]
) -> dict:
    """Build overall_performance dict from a per-instance status map."""
    resolved_ids = [inst for inst, s in statuses.items() if s == "resolved"]
    error_ids = [inst for inst, s in statuses.items() if s == "error"]
    emptypatch_ids = [inst for inst, s in statuses.items() if s == "emptypatch"]
    unresolved_ids = [
        inst
        for inst, s in statuses.items()
        if s in ("unresolved", "error")
    ]
    submitted_ids = list(statuses.keys())
    return normalize_overall_performance(
        {
            "files": files,
            "total_resolved_ids": resolved_ids,
            "total_unresolved_ids": unresolved_ids,
            "total_emptypatch_ids": emptypatch_ids,
            "total_error_ids": error_ids,
            "total_submitted_ids": submitted_ids,
        }
    )


def normalize_overall_performance(overall_performance: dict) -> dict:
    """
    Make overall_performance ID lists mutually consistent.

    Resolved instances are removed from unresolved / error / emptypatch lists.
    Count fields are derived from the normalized lists (not summed across reports).
    """
    if not overall_performance:
        return overall_performance

    op = dict(overall_performance)
    resolved_ids = list(dict.fromkeys(op.get("total_resolved_ids") or []))
    resolved_set = set(resolved_ids)

    emptypatch_ids = [
        inst
        for inst in dict.fromkeys(op.get("total_emptypatch_ids") or [])
        if inst not in resolved_set
    ]
    emptypatch_set = set(emptypatch_ids)

    error_ids = [
        inst
        for inst in dict.fromkeys(op.get("total_error_ids") or [])
        if inst not in resolved_set and inst not in emptypatch_set
    ]

    unresolved_ids = []
    for inst in dict.fromkeys(op.get("total_unresolved_ids") or []):
        if inst in resolved_set or inst in emptypatch_set:
            continue
        if inst not in unresolved_ids:
            unresolved_ids.append(inst)
    for inst in error_ids:
        if inst not in unresolved_ids:
            unresolved_ids.append(inst)

    submitted_ids = list(dict.fromkeys(op.get("total_submitted_ids") or []))
    if not submitted_ids:
        submitted_ids = list(
            dict.fromkeys(resolved_ids + unresolved_ids + emptypatch_ids)
        )

    total_resolved = len(resolved_ids)
    total_submitted = len(submitted_ids)
    op["total_resolved_ids"] = resolved_ids
    op["total_unresolved_ids"] = unresolved_ids
    op["total_emptypatch_ids"] = emptypatch_ids
    op["total_error_ids"] = error_ids
    op["total_submitted_ids"] = submitted_ids
    op["total_resolved_instances"] = total_resolved
    op["total_submitted_instances"] = total_submitted
    op["accuracy_score"] = (
        total_resolved / total_submitted if total_submitted else 0
    )
    return op


def load_hgm_metadata(hgm_metadata_path, last_only=False):
    # Load all archives from given metadata file
    if not os.path.exists(hgm_metadata_path):
        raise FileNotFoundError(f"Metadata file not found at {hgm_metadata_path}")
    # Read all JSON entries from the metadata file
    content = read_file(hgm_metadata_path)
    json_entries = content.split("\n{")
    # Parse all JSON entries
    hgm_metadata = []
    for json_entry in json_entries:
        # Add back the { if it was removed by split
        if not json_entry.startswith("{"):
            json_entry = "{" + json_entry
        # Parse the JSON entry
        metadata = json.loads(json_entry)
        hgm_metadata.append(metadata)

    if last_only:
        return hgm_metadata[-1]
    return hgm_metadata


def get_model_patch_paths(root_dir, hgm_dir, parent_commit):
    prev_commit = parent_commit
    patch_files = []
    while prev_commit != "initial":
        parent_dir = os.path.join(root_dir, hgm_dir, prev_commit)
        parent_patch_file = os.path.join(parent_dir, "model_patch.diff")
        if os.path.exists(parent_patch_file):
            patch_files.append(parent_patch_file)
        else:
            print(f"Parent patch file not found: {parent_patch_file}")
        # find next parent commit in the metadata
        parent_metadata = load_json_file(os.path.join(parent_dir, "metadata.json"))
        prev_commit = parent_metadata.get("parent_commit", "initial")
    return patch_files[::-1]  # reverse the list to get the correct order


def get_all_performance(run_keyword, results_dir="./swe_bench", does_print=True):
    """
    Retrieve performance results for all runs based on the provided keyword.

    Args:
        run_keyword (str): A keyword used to identify the target runs' evaluation results.

    Returns:
        list: A list of dictionaries, each containing performance results for a matching run.
    """
    matching_files = []
    matched_results = []
    fallback_files = []
    fallback_results = []
    if os.path.exists(results_dir):
        for root, _, files in os.walk(results_dir):
            if ".git-bootstrap" in root:
                continue
            for file_name in files:
                if not file_name.endswith(".json"):
                    continue
                rel_path = os.path.relpath(
                    os.path.join(root, file_name), start=results_dir
                )
                eval_agent_path = os.path.join(results_dir, rel_path)
                try:
                    eval_results = load_json_file(eval_agent_path)
                except Exception:
                    continue
                if not isinstance(eval_results, dict):
                    continue
                if (
                    "resolved_instances" not in eval_results
                    and "submitted_instances" not in eval_results
                ):
                    continue
                fallback_files.append(rel_path)
                fallback_results.append((rel_path, eval_results))
                if run_keyword in file_name or run_keyword in rel_path:
                    matching_files.append(rel_path)
                    matched_results.append((rel_path, eval_results))
    if not matching_files and fallback_files:
        matching_files = fallback_files
        matched_results = fallback_results
    performance_results = []
    instance_statuses: dict[str, str] = {}

    # Return an empty list if no matches are found
    if not matching_files:
        if does_print:
            print(f"No evaluation files found matching the keyword '{run_keyword}'.")
        overall_performance = normalize_overall_performance({"files": matching_files})
    else:
        # Older reports first so newer grading results can upgrade instance status.
        matched_results = sorted(
            matched_results,
            key=lambda item: os.path.getmtime(
                os.path.join(results_dir, item[0])
            ),
        )
        for file_name, eval_results in matched_results:
            resolved_instances = eval_results.get("resolved_instances", 0)
            submitted_instances = eval_results.get("submitted_instances", 0)
            if isinstance(resolved_instances, list):
                resolved_instances = len(resolved_instances)
            elif not isinstance(resolved_instances, int):
                resolved_instances = 0
            if isinstance(submitted_instances, list):
                submitted_instances = len(submitted_instances)
            elif not isinstance(submitted_instances, int):
                submitted_instances = 0
            accuracy_score = (
                resolved_instances / submitted_instances
                if submitted_instances > 0
                else 0
            )
            performance_results.append(
                {"file": file_name, "accuracy_score": accuracy_score, **eval_results}
            )
            instance_statuses = _merge_instance_statuses(
                instance_statuses,
                _instance_statuses_from_report(eval_results),
            )

        overall_performance = overall_performance_from_instance_statuses(
            instance_statuses, matching_files
        )

    return performance_results, overall_performance


def is_compiled_self_improve(metadata, num_swe_issues=[], logger=None):
    """
    Checks if the run was properly compiled and 'self-improved' by verifying:
      1. The 'overall_performance' dict has the required keys:
         ('accuracy_score', 'total_unresolved_ids', 'total_resolved_ids', 'total_emptypatch_ids').
      2. There is at least one non-empty patch (resolved + unresolved > 0).
      3. If num_swe_issues is provided, the total number of evaluated issues matches num_swe_issues.

    Returns True if all conditions are met, else False.
    """
    overall_perf = metadata.get("overall_performance", {})
    required_keys = [
        "accuracy_score",
        "total_unresolved_ids",
        "total_resolved_ids",
        "total_emptypatch_ids",
    ]

    # 1. Must have the required keys
    if not overall_perf or not all(k in overall_perf for k in required_keys):
        print(f"no required keys")
        # raise KeyError(f"Missing required keys in overall_performance: {required_keys}")
        return False

    # 2. Must have at least one non-empty patch
    num_resolved = len(overall_perf["total_resolved_ids"])
    num_unresolved = len(overall_perf["total_unresolved_ids"])
    if (num_resolved + num_unresolved) == 0:
        print(f"no non-empty patch")
        # raise ValueError("No non-empty patches found in the overall performance data.")
        return False

    # 3. If specified, total evaluated must match num_swe_issues, else it means that some didn't compile
    total_evaluated = overall_perf["total_submitted_instances"]
    if total_evaluated < num_swe_issues[0]:
        print(f"not match num_issues")
        # raise ValueError(f"Total evaluated instances {total_evaluated} does not match num_swe_issues {num_swe_issues[0]}.")
        return False

    return True
