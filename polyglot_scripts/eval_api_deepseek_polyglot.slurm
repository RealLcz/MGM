#!/bin/bash
#SBATCH --job-name=PG-DS4P
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=96
#SBATCH --mem=1536G
#SBATCH --partition=all
#SBATCH --exclude=cn34,cn35
#SBATCH --exclusive
#SBATCH --time=168:00:00
#SBATCH --output=logs/eval_api_deepseek_polyglot_%j.out
#SBATCH --error=logs/eval_api_deepseek_polyglot_%j.err
#
# Full Polyglot (225 tasks) evaluation via DeepSeek official API + Apptainer.
# No local vLLM / GPU required.
#
# Submit from repo root:
#   sbatch polyglot_scripts/eval_api_deepseek_polyglot.slurm
#
# Common overrides:
#   EVAL_NODE_ID=20260506_091600_219908 \
#   HGM_OUTPUT_DIR=output_polyglot/20260505_122548 \
#   HGM_LLM_MODEL_ID=deepseek-v4-pro \
#   EVAL_MAX_WORKERS=20 \
#   EVAL_FORCE=1 \
#   sbatch polyglot_scripts/eval_api_deepseek_polyglot.slurm
#
# Env:
#   DEEPSEEK_API_KEY       required (or .secrets/deepseek.env)
#   HGM_LLM_MODEL_ID       default deepseek-v4-pro
#   EVAL_NODE_ID           evolved node commit id under HGM_OUTPUT_DIR
#   HGM_OUTPUT_DIR         evolution tree for patch resolution
#   EVAL_OUTPUT_DIR        fresh eval output root (default under polyglot_eval_outputs/)
#   EVAL_INIT_AGENT_SRC    default initial_polyglot/default_agent/src
#   EVAL_DATASET_JSON      default polyglot/polyglot_benchmark_metadata.json
#   EVAL_MERGE_SOURCE_DIR  optional existing eval output to preserve/merge from
#   EVAL_RETEST_STATUSES   statuses to rerun from source (default: empty,unresolved,error,missing)
#   EVAL_TASK_IDS          optional explicit task ids to rerun (comma/space/colon separated)
#   EVAL_REUSE_MODEL_PATCH_DIR optional prediction dir to reuse non-empty model_patch from
#   EVAL_MAX_WORKERS       default 20
#   EVAL_TIMEOUT           default 3600
#   EVAL_FORCE             default 1 (rerun all 225)
#   DEEPSEEK_MAX_OUTPUT_TOKENS  default 16384

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs .secrets

CONDA_BASE="${CONDA_BASE:-/mnt/vast/home/ym56kacy/miniconda3}"
if command -v conda >/dev/null 2>&1; then
    eval "$(conda shell.bash hook)"
elif [ -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]; then
    # shellcheck source=/dev/null
    . "${CONDA_BASE}/etc/profile.d/conda.sh"
else
    echo "ERROR: conda not found; set CONDA_BASE to your Miniconda install." >&2
    exit 1
fi
conda activate "${CONDA_ENV_NAME:-HGM}"
REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

if [ -z "${SWE_EVAL_TMPDIR:-}" ] && [ -n "${SLURM_JOB_ID:-}" ] && [ -d /tmp ]; then
    export SWE_EVAL_TMPDIR="/tmp/${USER:-$(whoami)}-${SLURM_JOB_ID}"
fi

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cache_env.inc.sh"
cache_env_setup
export APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${REPO_ROOT}/apptainer_images}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

if [ -f "${REPO_ROOT}/.secrets/deepseek.env" ]; then
    # shellcheck source=/dev/null
    . "${REPO_ROOT}/.secrets/deepseek.env"
fi

export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-deepseek-v4-pro}"
export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_polyglot/20260505_122548}"
export EVAL_NODE_ID="${EVAL_NODE_ID:-20260506_091600_219908}"
export EVAL_INIT_AGENT_SRC="${EVAL_INIT_AGENT_SRC:-initial_polyglot/default_agent/src}"
export POLYGLOT_INIT_AGENT_SRC="${POLYGLOT_INIT_AGENT_SRC:-${EVAL_INIT_AGENT_SRC}}"
export EVAL_DATASET_JSON="${EVAL_DATASET_JSON:-polyglot/polyglot_benchmark_metadata.json}"
export EVAL_MAX_WORKERS="${EVAL_MAX_WORKERS:-20}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-3600}"
export EVAL_FORCE="${EVAL_FORCE:-1}"
export DEEPSEEK_MAX_OUTPUT_TOKENS="${DEEPSEEK_MAX_OUTPUT_TOKENS:-16384}"
export DEEPSEEK_API_TIMEOUT="${DEEPSEEK_API_TIMEOUT:-240}"
export DEEPSEEK_THINKING_MODE="${DEEPSEEK_THINKING_MODE:-disabled}"
export DEEPSEEK_REASONING_EFFORT="${DEEPSEEK_REASONING_EFFORT:-max}"
export POLYGLOT_CONTAINER_NETWORK_MODE="${POLYGLOT_CONTAINER_NETWORK_MODE:-host}"

# Host agent venv for non-Python Polyglot containers (C++/Go/Rust/Java/JS).
# shellcheck source=/dev/null
. "${REPO_ROOT}/polyglot_scripts/polyglot_agent_runtime.inc.sh"
polyglot_agent_runtime_setup

if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: Set DEEPSEEK_API_KEY or OPENAI_API_KEY (e.g. in .secrets/deepseek.env)" >&2
    exit 1
fi

if [ -z "${EVAL_OUTPUT_DIR:-}" ]; then
    EVAL_OUTPUT_DIR="polyglot_eval_outputs/${EVAL_NODE_ID}_${HGM_LLM_MODEL_ID}_$(date +%Y%m%d_%H%M%S)"
fi
export EVAL_OUTPUT_DIR
export EVAL_MODEL_NAME="${EVAL_MODEL_NAME:-${EVAL_NODE_ID}_${HGM_LLM_MODEL_ID}}"

# Stage agent src with repo-root llm.py / llm_withtools.py (DeepSeek API support).
AGENT_STAGE="${REPO_ROOT}/.cache/polyglot_eval_agent_stage_${SLURM_JOB_ID:-manual}"
rm -rf "${AGENT_STAGE}"
mkdir -p "${AGENT_STAGE}"
rsync -a "${EVAL_INIT_AGENT_SRC}/" "${AGENT_STAGE}/"
cp "${REPO_ROOT}/llm.py" "${REPO_ROOT}/llm_withtools.py" "${AGENT_STAGE}/"
export EVAL_INIT_AGENT_SRC="${AGENT_STAGE}"

if [ "${EVAL_DRY_RUN:-0}" = "1" ]; then
    echo "EVAL_DRY_RUN=1 - listing Polyglot tasks only."
    "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

dataset_path = Path(os.environ["EVAL_DATASET_JSON"])
data = json.loads(dataset_path.read_text(encoding="utf-8"))
ids = [e["instance_id"] for e in data]
print(f"tasks={len(ids)}")
for task_id in ids:
    print(task_id)
PY
    exit 0
fi

echo "Verifying DeepSeek API connectivity..."
if ! "${PYTHON_BIN}" -c "
import os, urllib.request
key = os.environ.get('DEEPSEEK_API_KEY') or os.environ.get('OPENAI_API_KEY')
base = os.environ.get('DEEPSEEK_API_BASE_URL', 'https://api.deepseek.com/v1').rstrip('/')
req = urllib.request.Request(
    f'{base}/models',
    headers={'Authorization': f'Bearer {key}'},
)
with urllib.request.urlopen(req, timeout=30) as resp:
    print('DeepSeek API OK:', resp.read()[:200])
"; then
    echo "ERROR: Cannot reach DeepSeek API from this node." >&2
    exit 1
fi

mkdir -p "${EVAL_OUTPUT_DIR}/predictions"

echo "Starting Polyglot full evaluation (DeepSeek API + Apptainer)"
echo "  HGM_OUTPUT_DIR=${HGM_OUTPUT_DIR}"
echo "  EVAL_NODE_ID=${EVAL_NODE_ID}"
echo "  EVAL_OUTPUT_DIR=${EVAL_OUTPUT_DIR}"
echo "  EVAL_INIT_AGENT_SRC=${EVAL_INIT_AGENT_SRC}"
echo "  model=${HGM_LLM_MODEL_ID}"
echo "  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}  EVAL_TIMEOUT=${EVAL_TIMEOUT}"
echo "  EVAL_FORCE=${EVAL_FORCE}"
echo "  EVAL_MERGE_SOURCE_DIR=${EVAL_MERGE_SOURCE_DIR:-}"
echo "  EVAL_RETEST_STATUSES=${EVAL_RETEST_STATUSES:-}"
echo "  EVAL_TASK_IDS=${EVAL_TASK_IDS:-}"
echo "  EVAL_REUSE_MODEL_PATCH_DIR=${EVAL_REUSE_MODEL_PATCH_DIR:-}"
echo "  POLYGLOT_HOST_AGENT_PYTHON=${POLYGLOT_HOST_AGENT_PYTHON}"
echo "  DEEPSEEK_MAX_OUTPUT_TOKENS=${DEEPSEEK_MAX_OUTPUT_TOKENS}"
echo "  DEEPSEEK_API_TIMEOUT=${DEEPSEEK_API_TIMEOUT}"
echo "  DEEPSEEK_API_KEY=set"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" - <<'PY'
import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

repo_root = Path.cwd()
output_tree = Path(os.environ["HGM_OUTPUT_DIR"])
if not output_tree.is_absolute():
    output_tree = (repo_root / output_tree).resolve()

commit_id = os.environ["EVAL_NODE_ID"]
node_dir = output_tree / commit_id
if not (node_dir / "metadata.json").exists():
    raise SystemExit(f"Node not found: {node_dir / 'metadata.json'}")

dataset_path = Path(os.environ["EVAL_DATASET_JSON"])
if not dataset_path.is_absolute():
    dataset_path = repo_root / dataset_path

data = json.loads(dataset_path.read_text(encoding="utf-8"))
universe = []
seen = set()
for entry in data:
    task_id = entry["instance_id"]
    if task_id not in seen:
        seen.add(task_id)
        universe.append(task_id)
if len(universe) != 225:
    print(f"WARNING: expected 225 Polyglot tasks, got {len(universe)}")

eval_out = Path(os.environ["EVAL_OUTPUT_DIR"])
pred_root = eval_out / "predictions"
eval_out.mkdir(parents=True, exist_ok=True)
pred_root.mkdir(parents=True, exist_ok=True)

force = os.environ.get("EVAL_FORCE", "1") == "1"
max_workers = max(1, int(os.environ["EVAL_MAX_WORKERS"]))
model_name = os.environ["EVAL_MODEL_NAME"]
pred_dir = pred_root / f"{model_name}_0"
pred_dir.mkdir(parents=True, exist_ok=True)

def split_tokens(raw):
    if not raw:
        return []
    return [token for token in raw.replace(",", " ").replace(":", " ").split() if token]


def status_from_result(result):
    eval_result = result.get("eval_result")
    if eval_result == "resolved" or result.get("resolved") is True:
        return "resolved"
    if eval_result == "empty_patch" or result.get("empty_patch") is True:
        return "empty"
    if eval_result == "unresolved":
        return "unresolved"
    if eval_result:
        return "error"
    return "error"


def find_prediction_dir(eval_dir, preferred_model_name):
    predictions = eval_dir / "predictions"
    preferred = predictions / f"{preferred_model_name}_0"
    if preferred.exists():
        return preferred
    candidates = sorted(path for path in predictions.glob("*_0") if path.is_dir())
    if len(candidates) == 1:
        return candidates[0]
    if candidates:
        print(
            "WARNING: preferred prediction dir missing; using most recent "
            f"candidate {candidates[-1]}. All candidates:"
        )
        for c in candidates:
            print(f"    {c}")
        return candidates[-1]
    return preferred


def load_statuses_from_pred_dir(source_pred_dir, task_ids):
    statuses = {}
    for task_id in task_ids:
        result_path = source_pred_dir / f"{task_id}.json"
        if not result_path.exists():
            continue
        try:
            result = json.loads(result_path.read_text(encoding="utf-8"))
        except Exception:
            statuses[task_id] = "error"
            continue
        statuses[task_id] = status_from_result(result)
    return statuses


def load_statuses_from_summary(eval_dir, task_ids):
    summary_path = eval_dir / "eval_summary.json"
    if not summary_path.exists():
        return {}
    try:
        summary = json.loads(summary_path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    overall = summary.get("overall_performance") or {}
    statuses = {}
    for task_id in overall.get("total_resolved_ids", []):
        statuses[task_id] = "resolved"
    for task_id in overall.get("total_unresolved_ids", []):
        statuses[task_id] = "unresolved"
    for task_id in overall.get("total_emptypatch_ids", []):
        statuses[task_id] = "empty"
    for task_id in overall.get("total_error_ids", []):
        statuses[task_id] = "error"
    return {task_id: statuses[task_id] for task_id in task_ids if task_id in statuses}


source_eval_dir = None
source_pred_dir = None
source_statuses = {}
reuse_patch_dir = os.environ.get("EVAL_REUSE_MODEL_PATCH_DIR", "").strip()
if reuse_patch_dir:
    reuse_patch_dir = Path(reuse_patch_dir)
    if not reuse_patch_dir.is_absolute():
        reuse_patch_dir = (repo_root / reuse_patch_dir).resolve()
    if not reuse_patch_dir.exists():
        raise SystemExit(f"EVAL_REUSE_MODEL_PATCH_DIR missing: {reuse_patch_dir}")
else:
    reuse_patch_dir = None
merge_source = os.environ.get("EVAL_MERGE_SOURCE_DIR", "").strip()
if merge_source:
    source_eval_dir = Path(merge_source)
    if not source_eval_dir.is_absolute():
        source_eval_dir = (repo_root / source_eval_dir).resolve()
    # Guard against self-referential merge: merging an output dir into itself
    # makes status bookkeeping circular and can mask "nothing actually ran"
    # situations. The envfix3 run hit this (EVAL_MERGE_SOURCE_DIR==EVAL_OUTPUT_DIR
    # with empty EVAL_RETEST_STATUSES); it only worked because EVAL_TASK_IDS was
    # set explicitly.
    try:
        output_eval_dir_resolved = eval_out.resolve()
    except Exception:
        output_eval_dir_resolved = eval_out
    if source_eval_dir == output_eval_dir_resolved:
        raise SystemExit(
            f"EVAL_MERGE_SOURCE_DIR ({source_eval_dir}) must not equal "
            f"EVAL_OUTPUT_DIR ({output_eval_dir_resolved}); use a distinct "
            "prior run as the merge source or omit EVAL_MERGE_SOURCE_DIR."
        )
    source_pred_dir = find_prediction_dir(source_eval_dir, model_name)
    if not source_pred_dir.exists():
        raise SystemExit(f"EVAL_MERGE_SOURCE_DIR has no prediction dir: {source_pred_dir}")
    # Per-prediction .json files are ground truth (they reflect the actual
    # outcome of each task); the summary can be stale when a prior run was
    # SIGTERM'd before writing it. Load summary first, then let pred_dir
    # override.
    source_statuses = load_statuses_from_summary(source_eval_dir, universe)
    source_statuses.update(load_statuses_from_pred_dir(source_pred_dir, universe))

explicit_task_ids = split_tokens(os.environ.get("EVAL_TASK_IDS", ""))
unknown_explicit = [task_id for task_id in explicit_task_ids if task_id not in seen]
if unknown_explicit:
    raise SystemExit(f"Unknown EVAL_TASK_IDS: {unknown_explicit}")

retest_statuses_env = os.environ.get(
    "EVAL_RETEST_STATUSES",
    "empty,unresolved,error,missing" if merge_source else "",
).strip()
if merge_source and not retest_statuses_env and not explicit_task_ids:
    # Empty EVAL_RETEST_STATUSES with a merge source and no explicit task ids
    # would silently produce an empty candidate list ("Nothing to run"). Fall
    # back to the documented defaults so the run does something useful.
    print(
        "WARNING: EVAL_RETEST_STATUSES is empty with EVAL_MERGE_SOURCE_DIR set "
        "and no EVAL_TASK_IDS; falling back to 'empty,unresolved,error,missing'."
    )
    retest_statuses_env = "empty,unresolved,error,missing"
retest_statuses = set(split_tokens(retest_statuses_env))
if merge_source:
    if explicit_task_ids:
        candidates = explicit_task_ids
    else:
        candidates = [
            task_id
            for task_id in universe
            if source_statuses.get(task_id, "missing") in retest_statuses
        ]
else:
    candidates = explicit_task_ids or universe

if force:
    to_run = candidates
else:
    to_run = [
        task_id
        for task_id in candidates
        if not (pred_dir / f"{task_id}.json").exists()
    ]

print(f"Polyglot universe: {len(universe)} tasks")
print(f"To run: {len(to_run)} (EVAL_FORCE={int(force)})")
if merge_source:
    preserved = [
        task_id
        for task_id in universe
        if source_statuses.get(task_id) == "resolved" and task_id not in set(candidates)
    ]
    print(f"Merge source: {source_eval_dir}")
    print(f"Source prediction dir: {source_pred_dir}")
    print(f"Retest statuses: {sorted(retest_statuses)}")
    print(f"Retest candidates: {len(candidates)}")
    print(f"Preserved resolved from source: {len(preserved)}")
if reuse_patch_dir:
    print(f"Reuse model patches from: {reuse_patch_dir}")

init_src = os.environ["EVAL_INIT_AGENT_SRC"]
if not os.path.isdir(init_src):
    raise SystemExit(f"EVAL_INIT_AGENT_SRC missing: {init_src}")

import polyglot.harness as polyglot_harness_module
from polyglot.harness import harness as polyglot_harness
from utils.evo_utils import get_model_patch_paths

polyglot_harness_module.llm = os.environ["HGM_LLM_MODEL_ID"]
polyglot_harness_module.timeout = int(os.environ["EVAL_TIMEOUT"])

hgm_dir_rel = os.path.relpath(output_tree, repo_root)
patch_paths = get_model_patch_paths(str(repo_root), hgm_dir_rel, commit_id)
print(f"Patch chain ({len(patch_paths)}):")
for p in patch_paths:
    print(f"  {p}")

if to_run:
    polyglot_harness(
        dataset_path=str(dataset_path),
        test_task_list=to_run,
        max_workers=min(max_workers, len(to_run)),
        model_name_or_path=model_name,
        model_patch_paths=patch_paths,
        pred_dname=str(pred_root),
        output_dir=str(eval_out),
        skip_existing=not force,
        init_agent_path=init_src,
        reuse_patch_dir=str(reuse_patch_dir) if reuse_patch_dir else None,
    )
else:
    print("Nothing to run; writing merged/current summary only.")

statuses = dict(source_statuses) if merge_source else {}
for task_id in universe:
    result_path = pred_dir / f"{task_id}.json"
    if not result_path.exists():
        continue
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except Exception:
        statuses[task_id] = "error"
        continue
    statuses[task_id] = status_from_result(result)

submitted = [t for t in universe if t in statuses]
resolved = [t for t in submitted if statuses[t] == "resolved"]
unresolved = [t for t in submitted if statuses[t] == "unresolved"]
empty = [t for t in submitted if statuses[t] == "empty"]
errors = [t for t in submitted if statuses[t] == "error"]
missing = [t for t in universe if t not in statuses]

summary = {
    "agent_node": commit_id,
    "hgm_output_dir": str(output_tree),
    "eval_output_dir": str(eval_out),
    "merge_source_dir": str(source_eval_dir) if source_eval_dir else None,
    "source_prediction_dir": str(source_pred_dir) if source_pred_dir else None,
    "reuse_model_patch_dir": str(reuse_patch_dir) if reuse_patch_dir else None,
    "llm": os.environ["HGM_LLM_MODEL_ID"],
    "model_name": model_name,
    "num_tasks": len(universe),
    "retest_statuses": sorted(retest_statuses),
    "retest_candidate_ids": candidates,
    "retest_run_ids": to_run,
    "missing_ids": missing,
    "overall_performance": {
        "accuracy_score": len(resolved) / len(submitted) if submitted else 0.0,
        "total_resolved_instances": len(resolved),
        "total_submitted_instances": len(submitted),
        "total_unresolved_ids": unresolved,
        "total_emptypatch_ids": empty,
        "total_resolved_ids": resolved,
        "total_submitted_ids": submitted,
        "total_error_ids": errors,
        "total_missing_ids": missing,
    },
    "finished_at": datetime.now(timezone.utc).isoformat(),
}
summary_path = eval_out / "eval_summary.json"
summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
perf = summary["overall_performance"]
print(
    f"Done: {perf['total_resolved_instances']}/{perf['total_submitted_instances']} "
    f"accuracy={perf['accuracy_score']:.4f}"
)
print(f"Summary: {summary_path}")
PY

echo "eval_api_deepseek_polyglot.slurm finished."
