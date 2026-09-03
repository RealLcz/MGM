#!/bin/bash
#SBATCH --job-name=SWE-DS4F
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=512G
#SBATCH --time=96:00:00
#SBATCH --output=logs/eval_api_deepseek_%j.out
#SBATCH --error=logs/eval_api_deepseek_%j.err
#
# Evaluate SWE-bench initial agent (small + medium = 60 tasks) via DeepSeek official API.
# No local vLLM / GPU required.
#
# Submit:
#   sbatch swe_scripts/eval_api_deepseek.slurm
#
#   EVAL_MAX_WORKERS=20 sbatch swe_scripts/eval_api_deepseek.slurm
#
# Env (optional):
#   HGM_LLM_MODEL_ID     default deepseek-v4-flash
#   DEEPSEEK_API_KEY     or set in .secrets/deepseek.env
#   EVAL_MAX_WORKERS     default 20
#   EVAL_TIMEOUT         default 3600
#   HGM_OUTPUT_DIR       auto-generated if unset
#   EVAL_TASK_LIST_FILE  optional JSON list of instance_ids (subset retest)
#   DEEPSEEK_MAX_OUTPUT_TOKENS  default 131072 for DeepSeek API calls
#   DEEPSEEK_THINKING_MODE     disabled|enabled (default disabled)
#   DEEPSEEK_REASONING_EFFORT  high|max when thinking enabled (default max)

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

eval "$(conda shell.bash hook)"
conda activate HGM
export PYTHON_BIN="${CONDA_PREFIX}/bin/python"
"${PYTHON_BIN}" -V

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
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

export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-deepseek-v4-flash}"
export INITIAL_AGENT_PATH="${INITIAL_AGENT_PATH:-initial_swe/default_agent/src}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-3600}"
export EVAL_MAX_WORKERS="${EVAL_MAX_WORKERS:-20}"

if [ -z "${DEEPSEEK_API_KEY:-}" ] && [ -z "${OPENAI_API_KEY:-}" ]; then
    echo "ERROR: Set DEEPSEEK_API_KEY or OPENAI_API_KEY (e.g. in .secrets/deepseek.env)" >&2
    exit 1
fi

if [ -z "${HGM_OUTPUT_DIR:-}" ]; then
    HGM_OUTPUT_DIR="output_swe/initial_agent_deepseek_v4_flash_$(date +%Y%m%d_%H%M%S)"
fi
export HGM_OUTPUT_DIR
export EVAL_MODEL_NAME="${EVAL_MODEL_NAME:-initial_agent_deepseek-v4-flash_toolfix}"
export DEEPSEEK_MAX_OUTPUT_TOKENS="${DEEPSEEK_MAX_OUTPUT_TOKENS:-131072}"
export DEEPSEEK_THINKING_MODE="${DEEPSEEK_THINKING_MODE:-disabled}"
export DEEPSEEK_REASONING_EFFORT="${DEEPSEEK_REASONING_EFFORT:-max}"

if [ "${EVAL_DRY_RUN:-0}" = "1" ]; then
    echo "EVAL_DRY_RUN=1 - listing SWE-bench tasks only."
    "${PYTHON_BIN}" - <<'PY'
import json
import os

if os.environ.get("EVAL_TASK_LIST_FILE"):
    task_ids = json.load(open(os.environ["EVAL_TASK_LIST_FILE"]))
else:
    small = json.load(open("swe_bench/subsets/small.json"))
    medium = json.load(open("swe_bench/subsets/medium.json"))
    task_ids = list(dict.fromkeys(small + medium))
print(f"tasks={len(task_ids)}")
for task_id in task_ids:
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

echo "Pre-pulling Apptainer images (timeout ${PRE_PULL_TIMEOUT:-7200}s)..."
timeout "${PRE_PULL_TIMEOUT:-7200}" "${PYTHON_BIN}" -u scripts/pull_epoch_images.py all || {
    echo "WARNING: Image pre-pull incomplete. Missing images will be pulled per task."
}

mkdir -p "${HGM_OUTPUT_DIR}" "${HGM_OUTPUT_DIR}/predictions"

echo "Starting SWE-bench initial-agent evaluation (DeepSeek API)"
echo "  output_dir=${HGM_OUTPUT_DIR}"
echo "  initial_agent_path=${INITIAL_AGENT_PATH}"
echo "  model=${HGM_LLM_MODEL_ID}"
echo "  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}  EVAL_TIMEOUT=${EVAL_TIMEOUT}"
echo "  DEEPSEEK_MAX_OUTPUT_TOKENS=${DEEPSEEK_MAX_OUTPUT_TOKENS}"
echo "  DEEPSEEK_THINKING_MODE=${DEEPSEEK_THINKING_MODE}  DEEPSEEK_REASONING_EFFORT=${DEEPSEEK_REASONING_EFFORT}"
echo "  EVAL_TASK_LIST_FILE=${EVAL_TASK_LIST_FILE:-<full 60-task subset>}"
echo "  DEEPSEEK_API_KEY=set"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

import swe_bench.harness as swe_harness
from swe_bench.harness import harness

small = json.load(open("swe_bench/subsets/small.json"))
medium = json.load(open("swe_bench/subsets/medium.json"))
if os.environ.get("EVAL_TASK_LIST_FILE"):
    task_ids = json.load(open(os.environ["EVAL_TASK_LIST_FILE"]))
else:
    task_ids = list(dict.fromkeys(small + medium))

init_agent_path = os.environ["INITIAL_AGENT_PATH"]
if not os.path.isdir(init_agent_path):
    raise SystemExit(f"INITIAL_AGENT_PATH does not exist: {init_agent_path}")

out_dir = Path(os.environ["HGM_OUTPUT_DIR"])
pred_dir = out_dir / "predictions"
out_dir.mkdir(parents=True, exist_ok=True)
pred_dir.mkdir(parents=True, exist_ok=True)

swe_harness.llm = os.environ["HGM_LLM_MODEL_ID"]
swe_harness.timeout = int(os.environ["EVAL_TIMEOUT"])

print(f"Submitting {len(task_ids)} SWE-bench tasks.")
out_dirs = harness(
    test_task_list=task_ids,
    max_workers=min(int(os.environ["EVAL_MAX_WORKERS"]), max(1, len(task_ids))),
    model_name_or_path=os.environ["EVAL_MODEL_NAME"],
    pred_dname=str(pred_dir),
    init_agent_path=init_agent_path,
)
print("Harness output dirs:", out_dirs)

from swe_bench.report import make_report

for out_dname in out_dirs:
    print(f"Running make_report on {out_dname} ...")
    make_report(
        dnames=[str(out_dname)],
        run_ids=[os.environ["EVAL_MODEL_NAME"]],
        dataset_name="princeton-nlp/SWE-bench",
        output_dir=str(out_dir),
        num_eval_procs=min(int(os.environ["EVAL_MAX_WORKERS"]), 10),
    )
    report_path = out_dir / f"{os.environ['EVAL_MODEL_NAME']}_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        resolved = report.get("resolved", [])
        print(f"Report: {len(resolved)}/{len(task_ids)} resolved")
PY

echo "eval_api_deepseek.slurm finished."
