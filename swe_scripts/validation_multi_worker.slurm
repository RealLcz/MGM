#!/bin/bash
#SBATCH --job-name=val-multi-5
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
#SBATCH --time=12:00:00
#SBATCH --output=logs/val_multi5_%j.out
#SBATCH --error=logs/val_multi5_%j.err
#
# Validation: 5 SWE-bench tasks from small.json, 5 concurrent harness workers, real vLLM.
# Submit: sbatch swe_scripts/validation_multi_worker.slurm

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

VLLM_PID=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null && sleep 1 && kill -9 "${VLLM_PID}" 2>/dev/null
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate HGM
export PYTHON_BIN="${CONDA_PREFIX}/bin/python"

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-262144}"
export VLLM_HOST="${VLLM_HOST:-127.0.0.1}"
export VLLM_CONTAINER_HOST="${VLLM_CONTAINER_HOST:-127.0.0.1}"
export APPTAINER_USE_HOST_NETWORK="${APPTAINER_USE_HOST_NETWORK:-0}"
export VAL_AGENT_TIMEOUT="${VAL_AGENT_TIMEOUT:-1800}"
export VAL_MAX_WORKERS="${VAL_MAX_WORKERS:-5}"

if [ -z "${TOOL_CALL_PARSER:-}" ]; then
    case "${VLLM_MODEL_NAME}" in
        *Qwen3*) TOOL_CALL_PARSER="qwen3_coder" ;;
        *)       TOOL_CALL_PARSER="hermes" ;;
    esac
fi
export TOOL_CALL_PARSER

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
vllm_pre_start_cleanup

echo "Starting vLLM..."
"${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 --port "${VLLM_PORT}" \
    --model "${VLLM_MODEL_NAME}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-model-len "${VLLM_MAX_MODEL_LEN}" \
    --enforce-eager --enable-auto-tool-choice \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    --dtype auto --trust-remote-code \
    > "logs/vllm_val_multi5_${SLURM_JOB_ID}.stdout.log" \
    2> "logs/vllm_val_multi5_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
until "${PYTHON_BIN}" -c "import urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then exit 1; fi
    VLLM_WAIT=$((VLLM_WAIT + 5))
    [ "${VLLM_WAIT}" -ge 900 ] && exit 1
    sleep 5
done
echo "vLLM ready (~${VLLM_WAIT}s)"

VAL_OUTPUT="${VAL_OUTPUT:-${REPO_ROOT}/output_validation_multi5}"
VAL_PRED="${VAL_OUTPUT}/predictions/val_multi5"
mkdir -p "${VAL_PRED}"

TASKS=(
    django__django-10973
    django__django-11066
    django__django-12754
    django__django-15930
    django__django-13279
)

echo "Pre-pulling images for ${#TASKS[@]} tasks..."
for tid in "${TASKS[@]}"; do
    "${PYTHON_BIN}" -u -c "
from utils.container_runtime import container_from_env
tag = f'sweb.eval.x86_64.${tid}:latest'
c = container_from_env(timeout=7200)
c.images.pull(tag)
print('OK', tag)
" || echo "WARN: pull failed for ${tid}"
done

echo "Running harness (${VAL_MAX_WORKERS} workers, timeout=${VAL_AGENT_TIMEOUT}s)..."
"${PYTHON_BIN}" -u -c "
import json
import swe_bench.harness as h
from swe_bench.harness import harness

tasks = json.loads(open('swe_bench/subsets/small.json').read())[:5]
h.llm = '${SERVED_MODEL_NAME}'
h.timeout = int('${VAL_AGENT_TIMEOUT}')
out_dirs = harness(
    test_task_list=tasks,
    max_workers=int('${VAL_MAX_WORKERS}'),
    model_name_or_path='val_multi5',
    pred_dname='${VAL_PRED}',
    init_agent_path='.',
)
print('Harness out_dirs:', out_dirs)
"

echo "Running make_report (Apptainer grading)..."
"${PYTHON_BIN}" -u -c "
from swe_bench.report import make_report
make_report(
    dnames=['${VAL_PRED}'],
    run_ids=['val_multi5_report'],
    dataset_name='princeton-nlp/SWE-bench',
    num_eval_procs=5,
)
"

echo "=== VALIDATION MULTI-5 COMPLETE ==="
ls -la "${VAL_PRED}"
find "${VAL_PRED}" -name '*.json' | head -20
