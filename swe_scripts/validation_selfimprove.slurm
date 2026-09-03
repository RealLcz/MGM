#!/bin/bash
#SBATCH --job-name=val-selfimprove
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
#SBATCH --time=08:00:00
#SBATCH --output=logs/val_selfimprove_%j.out
#SBATCH --error=logs/val_selfimprove_%j.err
#
# Validation: self-improve smoke with 2 parallel sample_child workers.
# Submit: sbatch swe_scripts/validation_selfimprove.slurm

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
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"

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
    > "logs/vllm_val_si_${SLURM_JOB_ID}.stdout.log" \
    2> "logs/vllm_val_si_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
until "${PYTHON_BIN}" -c "import urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then exit 1; fi
    VLLM_WAIT=$((VLLM_WAIT + 5))
    [ "${VLLM_WAIT}" -ge 900 ] && exit 1
    sleep 5
done
echo "vLLM ready (~${VLLM_WAIT}s)"

VAL_OUTPUT="${VAL_OUTPUT:-${REPO_ROOT}/output_validation_selfimprove}"
export HGM_OUTPUT_DIR="${VAL_OUTPUT}"
export HGM_IMAGE="${HGM_IMAGE:-default_agent:latest}"
mkdir -p "${VAL_OUTPUT}/initial"

echo "Pre-pulling Apptainer images for bootstrap tasks..."
"${PYTHON_BIN}" -u -c "
import json
from utils.container_runtime import container_from_env
tasks = json.loads(open('swe_bench/subsets/small.json').read())[:2]
c = container_from_env(timeout=7200)
for tid in tasks:
    tag = f'sweb.eval.x86_64.{tid}:latest'
    c.images.pull(tag)
    print('OK', tag)
"

echo "Bootstrapping initial eval (2 tasks) for self-improve parent metadata..."
"${PYTHON_BIN}" -u -c "
import json
import hgm_utils
from pathlib import Path

tasks = json.loads(open('swe_bench/subsets/small.json').read())[:2]
hgm_utils.init(False, '${VAL_OUTPUT}', tasks, 0, '${HGM_LLM_MODEL_ID}', int('${VAL_SELFIMPROVE_TIMEOUT:-2400}'))
hgm_utils.llm = '${HGM_LLM_MODEL_ID}'
hgm_utils.timeout = int('${VAL_SELFIMPROVE_TIMEOUT:-2400}')
# minimal initial metadata
from self_improve_step import save_metadata
save_metadata({
    'run_id': 'initial',
    'overall_performance': {
        'accuracy_score': 0,
        'total_resolved_instances': 0,
        'total_submitted_instances': 0,
        'files': [],
        'total_unresolved_ids': [],
        'total_emptypatch_ids': [],
        'total_resolved_ids': [],
        'total_submitted_ids': [],
    },
}, '${VAL_OUTPUT}/initial')
hgm_utils.eval_agent(
    'initial',
    tasks=tasks,
    max_workers=2,
    init_agent_path='.',
    skip=False,
)
print('Initial eval done')
"

echo "Running 2 parallel sample_child (strategy A)..."
"${PYTHON_BIN}" -u -c "
import json
import hgm_utils

tasks = json.loads(open('swe_bench/subsets/small.json').read())[:2]
hgm_utils.init(
    False,
    '${VAL_OUTPUT}',
    tasks,
    0,
    '${HGM_LLM_MODEL_ID}',
    int('${VAL_SELFIMPROVE_TIMEOUT:-2400}'),
)

parent = 'initial'
image = '${HGM_IMAGE}'

def run_one(i):
    return hgm_utils.sample_child(parent, image, self_improve_strategy='A')

from concurrent.futures import ThreadPoolExecutor, as_completed
with ThreadPoolExecutor(max_workers=2) as ex:
    futures = [ex.submit(run_one, i) for i in range(2)]
    results = [f.result() for f in as_completed(futures)]
print('sample_child results:', results)
failed = [r for r in results if r == 'failed']
if failed:
    raise SystemExit(f'{len(failed)} sample_child runs failed')
"

echo "=== VALIDATION SELF-IMPROVE COMPLETE ==="
find "${VAL_OUTPUT}" -type f | head -30
