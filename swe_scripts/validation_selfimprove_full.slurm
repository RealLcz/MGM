#!/bin/bash
#SBATCH --job-name=val-si-full
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=32
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
#SBATCH --time=12:00:00
#SBATCH --output=logs/val_si_full_%j.out
#SBATCH --error=logs/val_si_full_%j.err
#
# Full self-improve E2E: pre-pull -> bootstrap eval (2 tasks) -> sample_child -> verify outputs.
# Submit: sbatch swe_scripts/validation_selfimprove_full.slurm

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
export OPENAI_API_KEY="${OPENAI_API_KEY:-dummy}"
export VAL_OUTPUT="${VAL_OUTPUT:-${REPO_ROOT}/output_validation_si_full}"
export VAL_BOOTSTRAP_TIMEOUT="${VAL_BOOTSTRAP_TIMEOUT:-1200}"
export VAL_SELFIMPROVE_TIMEOUT="${VAL_SELFIMPROVE_TIMEOUT:-2400}"
export HGM_IMAGE="${HGM_IMAGE:-default_agent:latest}"

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
    > "logs/vllm_val_si_full_${SLURM_JOB_ID}.stdout.log" \
    2> "logs/vllm_val_si_full_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
until "${PYTHON_BIN}" -c "import urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then exit 1; fi
    VLLM_WAIT=$((VLLM_WAIT + 5))
    [ "${VLLM_WAIT}" -ge 900 ] && exit 1
    sleep 5
done
echo "vLLM ready (~${VLLM_WAIT}s)"

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

echo "Bootstrap eval_agent (2 tasks, timeout=${VAL_BOOTSTRAP_TIMEOUT}s)..."
"${PYTHON_BIN}" -u -c "
import json
import hgm_utils

tasks = json.loads(open('swe_bench/subsets/small.json').read())[:2]
hgm_utils.init(False, '${VAL_OUTPUT}', tasks, 0, '${HGM_LLM_MODEL_ID}', int('${VAL_BOOTSTRAP_TIMEOUT}'))
hgm_utils.llm = '${HGM_LLM_MODEL_ID}'
hgm_utils.timeout = int('${VAL_BOOTSTRAP_TIMEOUT}')
hgm_utils.eval_agent(
    'initial',
    tasks=tasks,
    max_workers=2,
    init_agent_path='.',
    skip=False,
)
meta = __import__('json').loads(open('${VAL_OUTPUT}/initial/metadata.json').read())
perf = meta['overall_performance']
print('Bootstrap metadata:', {
    'submitted': perf.get('total_submitted_instances'),
    'unresolved': len(perf.get('total_unresolved_ids', [])),
    'emptypatch': len(perf.get('total_emptypatch_ids', [])),
})
if not perf.get('total_unresolved_ids') and not perf.get('total_emptypatch_ids'):
    print('WARNING: no failed tasks for strategy A; sample_child may fall back')
"

echo "Running sample_child (strategy A, image=${HGM_IMAGE})..."
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
    int('${VAL_SELFIMPROVE_TIMEOUT}'),
)

run_id = hgm_utils.sample_child(
    'initial',
    '${HGM_IMAGE}',
    self_improve_strategy='A',
    max_try=1,
)
print('sample_child run_id:', run_id)
if run_id == 'failed':
    raise SystemExit('sample_child failed')
"

RUN_DIR="$(find "${VAL_OUTPUT}" -mindepth 1 -maxdepth 1 -type d ! -name initial | head -1)"
echo "Verifying outputs in ${RUN_DIR}..."
test -n "${RUN_DIR}"
test -s "${RUN_DIR}/model_patch.diff"
test -s "${RUN_DIR}/self_evo.md"
test -f "${RUN_DIR}/metadata.json"
echo "model_patch.diff bytes: $(wc -c < "${RUN_DIR}/model_patch.diff")"
echo "self_evo.md bytes: $(wc -c < "${RUN_DIR}/self_evo.md")"

echo "=== VALIDATION SELF-IMPROVE FULL COMPLETE ==="
