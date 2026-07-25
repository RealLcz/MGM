#!/usr/bin/env bash
# Submit the 60-task SWE-bench Pro subset as two sequential 30-task jobs.

set -euo pipefail

REPO_ROOT="$(git rev-parse --show-toplevel 2>/dev/null || pwd)"
cd "${REPO_ROOT}"

PART1_SUBSET="${SWE_PRO_PART1_SUBSET:-SWEbench_Pro/subsets/test_part1_30.json}"
PART2_SUBSET="${SWE_PRO_PART2_SUBSET:-SWEbench_Pro/subsets/test_part2_30.json}"
OUTPUT_PREFIX="${SWE_PRO_OUTPUT_PREFIX:-SWEbench_Pro/outputs/polyglot_initial_qwen3_6_35b_a3b_split30}"

AGENT_MODE="${SWE_PRO_AGENT_MODE:-initial}"
INIT_AGENT_SRC="${SWE_PRO_INIT_AGENT_SRC:-initial_polyglot/default_agent/src}"
MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
MAX_WORKERS="${SWE_PRO_MAX_WORKERS:-1}"
PRUNE_AFTER="${SWE_PRO_PRUNE_AFTER:-1}"

if [ -z "${REMOTE_DOCKER_PASSWORD:-}" ]; then
    echo "WARNING: REMOTE_DOCKER_PASSWORD is not set in this shell."
    echo "         Evaluation may still work with SSH keys, but prune-after requires password auth."
fi

COMMON_EXPORTS="ALL"
COMMON_EXPORTS+=",SWE_PRO_AGENT_MODE=${AGENT_MODE}"
COMMON_EXPORTS+=",SWE_PRO_INIT_AGENT_SRC=${INIT_AGENT_SRC}"
COMMON_EXPORTS+=",VLLM_MODEL_NAME=${MODEL_NAME}"
COMMON_EXPORTS+=",SERVED_MODEL_NAME=${SERVED_MODEL_NAME:-${MODEL_NAME}}"
COMMON_EXPORTS+=",HGM_LLM_MODEL_ID=${HGM_LLM_MODEL_ID:-${MODEL_NAME}}"
COMMON_EXPORTS+=",SWE_PRO_MAX_WORKERS=${MAX_WORKERS}"
COMMON_EXPORTS+=",SWE_PRO_PRUNE_AFTER=${PRUNE_AFTER}"

PART1_EXPORTS="${COMMON_EXPORTS},SWE_PRO_SUBSET=${PART1_SUBSET},SWE_PRO_OUTPUT_DIR=${OUTPUT_PREFIX}_part1"
PART2_EXPORTS="${COMMON_EXPORTS},SWE_PRO_SUBSET=${PART2_SUBSET},SWE_PRO_OUTPUT_DIR=${OUTPUT_PREFIX}_part2"

job1="$(sbatch --parsable --export="${PART1_EXPORTS}" SWEbench_Pro/eval_mgm_pro.slurm)"
job1_id="${job1%%;*}"
echo "Submitted part1: ${job1_id}"

job2="$(sbatch --parsable --dependency="afterany:${job1_id}" --export="${PART2_EXPORTS}" SWEbench_Pro/eval_mgm_pro.slurm)"
job2_id="${job2%%;*}"
echo "Submitted part2: ${job2_id} (afterany:${job1_id})"
