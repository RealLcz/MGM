#!/bin/bash
#SBATCH --job-name=e2e-initial-1
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
#SBATCH --time=06:00:00
#SBATCH --output=logs/e2e_initial_one_%j.out
#SBATCH --error=logs/e2e_initial_one_%j.err
#
# E2E: one task from small.json, initial agent (repo root), real vLLM + Apptainer.
#
# Submit:
#   sbatch swe_scripts/e2e_initial_one_task.slurm
#   E2E_INSTANCE_ID=django__django-11066 sbatch swe_scripts/e2e_initial_one_task.slurm

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
"${PYTHON_BIN}" -V

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cache_env.inc.sh"
cache_env_setup
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

export APPTAINER_IMAGE_DIR="${E2E_APPTAINER_IMAGE_DIR:-${REPO_ROOT}/apptainer_images}"
export APPTAINER_WORKSPACE_ROOT="${E2E_APPTAINER_WORKSPACE_ROOT:-${TMPDIR:-/tmp}/apptainer-workspaces-e2e}"
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-${APPTAINER_IMAGE_DIR}/cache}"
mkdir -p "${APPTAINER_IMAGE_DIR}" "${APPTAINER_WORKSPACE_ROOT}" "${APPTAINER_CACHEDIR}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-262144}"
HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="$(hostname -i 2>/dev/null | awk '{print $1}' || true)"
fi
export VLLM_HOST="${VLLM_HOST:-${HOST_IP:-127.0.0.1}}"
# VLLM_CONTAINER_HOST from apptainer_runtime.inc.sh (node IP when host net off)

if [ -z "${TOOL_CALL_PARSER:-}" ]; then
    case "${VLLM_MODEL_NAME}" in
        *Qwen3-Coder*|*Qwen3*)  TOOL_CALL_PARSER="qwen3_coder" ;;
        *gemma-4*|*gemma4*)      TOOL_CALL_PARSER="functiongemma" ;;
        *)                       TOOL_CALL_PARSER="hermes" ;;
    esac
fi
export TOOL_CALL_PARSER

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
vllm_pre_start_cleanup

echo "Starting vLLM (${VLLM_MODEL_NAME})..."
"${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --port "${VLLM_PORT}" \
    --model "${VLLM_MODEL_NAME}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-model-len "${VLLM_MAX_MODEL_LEN}" \
    --enforce-eager \
    --gdn-prefill-backend triton \
    --enable-auto-tool-choice \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    --dtype auto \
    --trust-remote-code \
    > "logs/vllm_e2e_${SLURM_JOB_ID}.stdout.log" 2> "logs/vllm_e2e_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT=900
until "${PYTHON_BIN}" -c "import urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM died. See logs/vllm_e2e_${SLURM_JOB_ID}.stderr.log" >&2
        exit 1
    fi
    VLLM_WAIT=$((VLLM_WAIT + 5))
    if [ "${VLLM_WAIT}" -ge "${VLLM_MAX_WAIT}" ]; then
        echo "ERROR: vLLM not ready within ${VLLM_MAX_WAIT}s" >&2
        exit 1
    fi
    sleep 5
done
echo "vLLM ready (~${VLLM_WAIT}s). Models:"
curl -s "http://${VLLM_HOST}:${VLLM_PORT}/v1/models" | head -c 500
echo ""
echo "Apptainer runtime: APPTAINER_USE_HOST_NETWORK=${APPTAINER_USE_HOST_NETWORK} VLLM_CONTAINER_HOST=${VLLM_CONTAINER_HOST}"

VERIFY_SIF="${APPTAINER_IMAGE_DIR}/sweb.eval.x86_64.django__django-10973_latest.sif"
if [ ! -f "${VERIFY_SIF}" ]; then
    VERIFY_SIF="$(ls "${APPTAINER_IMAGE_DIR}"/sweb.eval.x86_64.django__django-10973*.sif 2>/dev/null | head -1 || true)"
fi
if [ -n "${VERIFY_SIF}" ] && [ -f "${VERIFY_SIF}" ]; then
    NET_ARGS=()
    if [ "${APPTAINER_USE_HOST_NETWORK}" = "1" ]; then
        NET_ARGS=(--net --network host)
    fi
    echo "Verifying Apptainer -> vLLM at ${VLLM_CONTAINER_HOST}:${VLLM_PORT} ..."
    if ! apptainer exec "${NET_ARGS[@]}" "${VERIFY_SIF}" \
        curl -sf --connect-timeout 10 "http://${VLLM_CONTAINER_HOST}:${VLLM_PORT}/v1/models" | head -c 200; then
        echo "ERROR: Apptainer cannot reach vLLM" >&2
        exit 1
    fi
    echo ""
    echo "Apptainer -> vLLM connectivity OK."
fi

E2E_INSTANCE_ID="${E2E_INSTANCE_ID:-django__django-10973}"
E2E_OUTPUT_DIR="${E2E_OUTPUT_DIR:-${REPO_ROOT}/output_e2e_real_vllm}"
E2E_PRED_DIR="${E2E_OUTPUT_DIR}/predictions"
mkdir -p "${E2E_PRED_DIR}"

echo "Pulling Apptainer image for ${E2E_INSTANCE_ID} if missing..."
"${PYTHON_BIN}" -u -c "
from utils.container_runtime import container_from_env
tag = f'sweb.eval.x86_64.${E2E_INSTANCE_ID}:latest'
c = container_from_env(timeout=7200)
c.images.pull(tag)
print('Image OK:', c.images.resolve_sif(tag))
"

echo "Running harness for ${E2E_INSTANCE_ID} (initial agent, real vLLM)..."
"${PYTHON_BIN}" -u -c "
import swe_bench.harness as h
from swe_bench.harness import harness

h.llm = '${SERVED_MODEL_NAME}'
h.timeout = int('${E2E_AGENT_TIMEOUT:-3600}')

out_dirs = harness(
    test_task_list=['${E2E_INSTANCE_ID}'],
    max_workers=1,
    model_name_or_path='e2e_real_initial',
    pred_dname='${E2E_PRED_DIR}',
    init_agent_path='${REPO_ROOT}',
)
print('Harness out:', out_dirs)
import json
from pathlib import Path
for d in out_dirs:
    jf = d / '${E2E_INSTANCE_ID}.json'
    if jf.exists():
        j = json.loads(jf.read_text())
        print('patch_len:', len(j.get('model_patch', '')))
        print('json:', jf)
"

echo "E2E harness finished. Outputs under ${E2E_OUTPUT_DIR}"
