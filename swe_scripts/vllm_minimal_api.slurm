#!/bin/bash
#SBATCH --job-name=vllm-min
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=96G
#SBATCH --gres=gpu:1
#SBATCH --time=24:00:00
#SBATCH --output=logs/vllm_min_%j.out
#SBATCH --error=logs/vllm_min_%j.err

source ~/.bashrc
conda activate HGM

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

SSH_TUNNEL_PID=""
VLLM_PID=""

cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null || true
    [ -n "${SSH_TUNNEL_PID}" ] && kill "${SSH_TUNNEL_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate "${CONDA_ENV_NAME:-HGM}"
export PYTHON_BIN="${PYTHON_BIN:-python}"
"${PYTHON_BIN}" -V

export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${VLLM_MODEL_NAME}}"
export VLLM_PORT="${VLLM_PORT:-8000}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-32768}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.9}"

# Reuse repo helper to align tensor-parallel setup with Slurm GPU allocation.
REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

HOST_IP="$(hostname -I | awk '{print $1}')"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="127.0.0.1"
fi
export VLLM_HOST="${VLLM_HOST:-$HOST_IP}"

echo "Starting vLLM API on ${VLLM_HOST}:${VLLM_PORT}"
"${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --port "${VLLM_PORT}" \
    --model "${VLLM_MODEL_NAME}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-model-len "${VLLM_MAX_MODEL_LEN}" \
    --dtype auto \
    --trust-remote-code \
    > "logs/vllm_min_${SLURM_JOB_ID}.stdout.log" 2> "logs/vllm_min_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

echo "Waiting for vLLM readiness..."
WAITED=0
MAX_WAIT=600
until "${PYTHON_BIN}" -c "import urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2).read()" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process died; check logs/vllm_min_${SLURM_JOB_ID}.stderr.log" >&2
        exit 1
    fi
    WAITED=$((WAITED + 5))
    if [ "${WAITED}" -ge "${MAX_WAIT}" ]; then
        echo "ERROR: vLLM did not become ready within ${MAX_WAIT}s" >&2
        exit 1
    fi
    sleep 5
done

echo "vLLM is ready: http://${VLLM_HOST}:${VLLM_PORT}/v1/models"

# Optional reverse tunnel: expose vLLM on a remote machine.
# Example:
#   EXPOSE_REMOTE=1 REMOTE_SSH_HOST=user@remote REMOTE_VLLM_PORT=18000 sbatch swe_scripts/vllm_minimal_api.slurm
if [ "${EXPOSE_REMOTE:-0}" = "1" ]; then
    if [ -z "${REMOTE_SSH_HOST:-}" ]; then
        echo "ERROR: EXPOSE_REMOTE=1 requires REMOTE_SSH_HOST (example: user@server)" >&2
        exit 1
    fi
    export REMOTE_VLLM_PORT="${REMOTE_VLLM_PORT:-${VLLM_PORT}}"
    echo "Opening reverse tunnel: ${REMOTE_SSH_HOST}:${REMOTE_VLLM_PORT} -> 127.0.0.1:${VLLM_PORT}"
    ssh -nNT -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
        -o ExitOnForwardFailure=yes \
        -R "${REMOTE_VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
        "${REMOTE_SSH_HOST}" &
    SSH_TUNNEL_PID=$!
fi

echo "Server running. Press Ctrl-C or cancel Slurm job to stop."
wait "${VLLM_PID}"
