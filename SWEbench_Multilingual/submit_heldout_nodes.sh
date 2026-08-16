#!/usr/bin/env bash
# Submit held-out SWE-bench Multilingual eval jobs (Apptainer + local vLLM).

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "${REPO_ROOT}"

MGM_NODE="/mnt/vast/home/ym56kacy/MendelGM/output_polyglot/20260505_122548/20260506_091600_219908"
HGM_NODE="/mnt/vast/home/ym56kacy/MendelGM/output_polyglot/20260507_160801/20260508_072523_695997"
SUBSET="${SWE_ML_SUBSET:-SWEbench_Multilingual/subsets/heldout_60.json}"
MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
MAX_WORKERS="${SWE_ML_MAX_WORKERS:-10}"
JINHE_APPTAINER="/mnt/vast/home/ym56kacy/jinhe/MendelGM/apptainer_images"
APPTAINER_DIR="${APPTAINER_IMAGE_DIR:-${JINHE_APPTAINER}}"

echo "Generating held-out subset if missing..."
if [ ! -f "${SUBSET}" ]; then
    python SWEbench_Multilingual/scripts/sample_heldout.py
fi

COMMON_EXPORTS="ALL"
COMMON_EXPORTS+=",SWE_ML_SUBSET=${SUBSET}"
COMMON_EXPORTS+=",VLLM_MODEL_NAME=${MODEL_NAME}"
COMMON_EXPORTS+=",SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-${MODEL_NAME}}"
COMMON_EXPORTS+=",HGM_LLM_MODEL_ID=${HGM_LLM_MODEL_ID:-${MODEL_NAME}}"
COMMON_EXPORTS+=",SWE_ML_MAX_WORKERS=${MAX_WORKERS}"
COMMON_EXPORTS+=",SWE_ML_INIT_AGENT_SRC=initial_polyglot/default_agent/src"
COMMON_EXPORTS+=",APPTAINER_IMAGE_DIR=${APPTAINER_DIR}"

MGM_EXPORTS="${COMMON_EXPORTS},SWE_ML_PREPULL=1,SWE_ML_NODE_PATH=${MGM_NODE},SWE_ML_OUTPUT_DIR=SWEbench_Multilingual/outputs/mgm_node_20260506_091600_219908"
HGM_EXPORTS="${COMMON_EXPORTS},SWE_ML_PREPULL=0,SWE_ML_NODE_PATH=${HGM_NODE},SWE_ML_OUTPUT_DIR=SWEbench_Multilingual/outputs/hgm_node_20260508_072523_695997"

job_mgm="$(sbatch --parsable --export="${MGM_EXPORTS}" SWEbench_Multilingual/eval_mgm_ml.slurm)"
job_mgm_id="${job_mgm%%;*}"
echo "Submitted MGM multilingual held-out eval (Apptainer): ${job_mgm_id}"

# Start HGM after MGM begins prepull/vLLM setup to reduce duplicate image pulls.
job_hgm="$(sbatch --parsable --dependency=after:${job_mgm_id} --export="${HGM_EXPORTS}" SWEbench_Multilingual/eval_mgm_ml.slurm)"
job_hgm_id="${job_hgm%%;*}"
echo "Submitted HGM multilingual held-out eval (Apptainer): ${job_hgm_id}"
