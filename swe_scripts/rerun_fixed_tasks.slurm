#!/bin/bash
#SBATCH --job-name=SWE-MGM-FIX
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=0
#SBATCH --partition=all
#SBATCH --time=06:00:00
#SBATCH --output=logs/rerun_fixed_%j.out
#SBATCH --error=logs/rerun_fixed_%j.err
#
# Re-run specific tasks with fixed diff minimality code.
# Uses best_agent code directly (evolution patches already applied + bug fixes).

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

CONDA_BASE="${CONDA_BASE:-/mnt/vast/home/ym56kacy/miniconda3}"
if [[ -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]]; then
  source "${CONDA_BASE}/etc/profile.d/conda.sh"
  conda activate HGM
  export PYTHON_BIN="${CONDA_PREFIX}/bin/python"
else
  export PYTHON_BIN="${CONDA_BASE}/envs/HGM/bin/python"
fi
"${PYTHON_BIN}" -V

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
. "${REPO_ROOT}/swe_scripts/swe_eval_dataset.inc.sh"
. "${REPO_ROOT}/swe_scripts/cache_env.inc.sh"
cache_env_setup
export APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${REPO_ROOT}/apptainer_images}"
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

# Use the fixed best_agent code (evolution patches + bug fixes already applied)
export EVAL_INIT_AGENT_SRC="${REPO_ROOT}/best_agent/mgm_20260616_024835_287218/src"
export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_mgm}"
export EVAL_NODE_ID="${EVAL_NODE_ID:-20260616_024835_287218}"
export EVAL_TASK_IDS="${EVAL_TASK_IDS:-django__django-12273,django__django-12406,sphinx-doc__sphinx-9229}"
export EVAL_MAX_WORKERS="${EVAL_MAX_WORKERS:-3}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-7200}"

echo "Re-running tasks with fixed diff minimality code"
echo "  EVAL_INIT_AGENT_SRC=${EVAL_INIT_AGENT_SRC}"
echo "  EVAL_NODE_ID=${EVAL_NODE_ID}"
echo "  EVAL_TASK_IDS=${EVAL_TASK_IDS}"
echo "  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}"
echo "  EVAL_TIMEOUT=${EVAL_TIMEOUT}"

# Start vLLM
HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-Qwen/Qwen3.6-35B-A3B}"
export HGM_LLM_MODEL_ID
echo "Starting vLLM for model ${HGM_LLM_MODEL_ID}..."
source "${REPO_ROOT}/swe_scripts/vllm_startup.inc.sh"
vllm_start

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" scripts/rerun_fixed_tasks.py

echo "Done."
