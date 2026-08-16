#!/bin/bash
#SBATCH --job-name=HGM
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:4
#SBATCH --time=192:00:00
#SBATCH --output=logs/hgm_%j.out
#SBATCH --error=logs/hgm_%j.err

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
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-262144}"
export VLLM_MODEL_IMPL="${VLLM_MODEL_IMPL:-auto}"
export HGM_MAX_WORKERS="${HGM_MAX_WORKERS:-2}"

HOST_IP="$(hostname -I | awk '{print $1}')"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="$(hostname -i | awk '{print $1}')"
fi
if [ -z "${HOST_IP}" ]; then
    HOST_IP="127.0.0.1"
fi
export VLLM_HOST="${VLLM_HOST:-$HOST_IP}"
export VLLM_CONTAINER_HOST="${VLLM_CONTAINER_HOST:-127.0.0.1}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"

if [ -z "${TOOL_CALL_PARSER:-}" ]; then
    case "${VLLM_MODEL_NAME}" in
        *Qwen3-Coder*|*Qwen3*)  TOOL_CALL_PARSER="qwen3_coder" ;;
        *gemma-4*|*gemma4*)      TOOL_CALL_PARSER="functiongemma" ;;
        *)                       TOOL_CALL_PARSER="hermes" ;;
    esac
fi
export TOOL_CALL_PARSER

echo "Cleaning up any stale processes on port ${VLLM_PORT}..."
fuser -k -9 "${VLLM_PORT}/tcp" 2>/dev/null || true
for pid in $(ss -tlnp "sport = :${VLLM_PORT}" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u); do
    echo "Killing stale process ${pid} and its children on port ${VLLM_PORT}"
    pkill -9 -P "${pid}" 2>/dev/null || true
    kill -9 "${pid}" 2>/dev/null || true
done
sleep 5
if ss -tln "sport = :${VLLM_PORT}" 2>/dev/null | grep -q ":${VLLM_PORT}"; then
    echo "WARNING: port ${VLLM_PORT} still occupied, trying alternate port $((VLLM_PORT + 1))..."
    export VLLM_PORT=$((VLLM_PORT + 1))
    echo "Using port ${VLLM_PORT} instead"
fi

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
vllm_pre_start_cleanup

echo "Starting vLLM server..."

"${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --port "${VLLM_PORT}" \
    --model "${VLLM_MODEL_NAME}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --model-impl "${VLLM_MODEL_IMPL}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-model-len "${VLLM_MAX_MODEL_LEN}" \
    --enforce-eager \
    --enable-auto-tool-choice \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    --dtype auto \
    --trust-remote-code \
    > "logs/vllm_${SLURM_JOB_ID}.stdout.log" 2> "logs/vllm_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT=600
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process (pid=${VLLM_PID}) died. Check logs/vllm_${SLURM_JOB_ID}.stderr.log" >&2
        exit 1
    fi
    VLLM_WAIT=$((VLLM_WAIT + 5))
    if [ "${VLLM_WAIT}" -ge "${VLLM_MAX_WAIT}" ]; then
        echo "ERROR: vLLM did not become ready within ${VLLM_MAX_WAIT}s" >&2
        exit 1
    fi
    sleep 5
done

echo "vLLM server is ready (took ~${VLLM_WAIT}s)."

echo "Pre-pulling Apptainer images (timeout ${PRE_PULL_TIMEOUT:-600}s)..."
timeout "${PRE_PULL_TIMEOUT:-600}" "${PYTHON_BIN}" -u scripts/pull_epoch_images.py all || {
    echo "WARNING: Image pre-pull incomplete (timeout or error). HGM will proceed."
    echo "Missing images will cause per-task failures that are handled gracefully."
}

export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_mgm}"

echo "Starting HGM (fresh run, output_dir=${HGM_OUTPUT_DIR}, no continue_from)..."
echo "  strategy A only, sequential evals, budget 200"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" hgm.py \
    --config "${HGM_CONFIG:-config.yaml}" \
    --output_dir "${HGM_OUTPUT_DIR}" \
    --max_task_evals 200 \
    --max_workers "${HGM_MAX_WORKERS}" \
    --eval_random_level 0 \
    --failed_pool_boost 1.0 \
    --self_improve_weight_a 1.0 \
    --self_improve_weight_b 0.0 \
    --self_improve_weight_c 0.0 \
    --self_improve_llm "${HGM_LLM_MODEL_ID}" \
    --downstream_llm "${HGM_LLM_MODEL_ID}" \
    --diagnose_llm "${HGM_LLM_MODEL_ID}"
