#!/bin/bash
#SBATCH --job-name=SWE-GRADE
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --partition=all
#SBATCH --time=12:00:00
#SBATCH --output=logs/grade_existing_%j.out
#SBATCH --error=logs/grade_existing_%j.err
#
# Re-run SWE-bench grading only (no vLLM / no agent) on existing predictions.
#
# MGM:
#   export HGM_OUTPUT_DIR=output_mgm EVAL_NODE_ID=20260616_024835_287218 \
#     EVAL_PRED_SUBDIR=20260616_024835_287218_38 EVAL_GRADE_WORKERS=16
#   sbatch --job-name=SWE-MGM-GRADE --export=ALL swe_scripts/grade_existing.slurm
#
# HGM:
#   export HGM_OUTPUT_DIR=output_hgm EVAL_NODE_ID=20260604_123351_915920 \
#     EVAL_PRED_SUBDIR=20260604_123351_915920_32 EVAL_GRADE_WORKERS=16
#   sbatch --job-name=SWE-HGM-GRADE --partition=h200 --nodelist=cn36 \
#     --cpus-per-task=64 --export=ALL swe_scripts/grade_existing.slurm

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

CONDA_BASE="${CONDA_BASE:-/mnt/vast/home/ym56kacy/miniconda3}"
if [[ -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]]; then
  # shellcheck source=/dev/null
  source "${CONDA_BASE}/etc/profile.d/conda.sh"
  conda activate HGM
  export PYTHON_BIN="${CONDA_PREFIX}/bin/python"
else
  export PYTHON_BIN="${CONDA_BASE}/envs/HGM/bin/python"
fi
"${PYTHON_BIN}" -V

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/swe_eval_dataset.inc.sh"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cache_env.inc.sh"
cache_env_setup
export APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${REPO_ROOT}/apptainer_images}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

: "${HGM_OUTPUT_DIR:?Set HGM_OUTPUT_DIR (output_mgm or output_hgm)}"
: "${EVAL_NODE_ID:?Set EVAL_NODE_ID}"
: "${EVAL_PRED_SUBDIR:?Set EVAL_PRED_SUBDIR to predictions subfolder name}"

echo "Grading existing predictions (no agent/vLLM)"
echo "  HGM_OUTPUT_DIR=${HGM_OUTPUT_DIR}"
echo "  EVAL_NODE_ID=${EVAL_NODE_ID}"
echo "  EVAL_PRED_SUBDIR=${EVAL_PRED_SUBDIR}"
echo "  EVAL_GRADE_WORKERS=${EVAL_GRADE_WORKERS:-16}"

"${PYTHON_BIN}" scripts/grade_swe_predictions.py \
  --node-id "${EVAL_NODE_ID}" \
  --hgm-output-dir "${HGM_OUTPUT_DIR}" \
  --pred-subdir "${EVAL_PRED_SUBDIR}" \
  --max-workers "${EVAL_GRADE_WORKERS:-16}"

echo "Done."
