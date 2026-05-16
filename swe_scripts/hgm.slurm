#!/bin/bash
#SBATCH --job-name=HGM
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
#SBATCH --time=96:00:00
#SBATCH --output=logs/hgm_%j.out
#SBATCH --error=logs/hgm_%j.err

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

SSH_TUNNEL_PID=""
VLLM_PID=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null && sleep 1 && kill -9 "${VLLM_PID}" 2>/dev/null
    [ -n "${SSH_TUNNEL_PID}" ] && kill "${SSH_TUNNEL_PID}" 2>/dev/null
    rm -f "${REMOTE_DOCKER_SOCKET:-}"
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate HGM
export PYTHON_BIN="${PYTHON_BIN:-$HOME/mm/python311/bin/python}"
"${PYTHON_BIN}" -V

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3-Coder-Next}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3-Coder-Next}"
REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-98304}"
export HGM_MAX_WORKERS="${HGM_MAX_WORKERS:-2}"
export ENABLE_REMOTE_DOCKER="${ENABLE_REMOTE_DOCKER:-1}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/docker-remote.sock}"

HOST_IP="$(hostname -I | awk '{print $1}')"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="$(hostname -i | awk '{print $1}')"
fi
if [ -z "${HOST_IP}" ]; then
    HOST_IP="127.0.0.1"
fi
export VLLM_HOST="${VLLM_HOST:-$HOST_IP}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"

# Auto-detect tool-call-parser from model name if not explicitly set
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
export REMOTE_VLLM_PORT="${REMOTE_VLLM_PORT:-${VLLM_PORT}}"

if [ "${ENABLE_REMOTE_DOCKER}" = "1" ]; then
    echo "Setting up SSH tunnel to remote Docker on ${REMOTE_DOCKER_HOST}..."

    echo "Killing any stale SSH tunnels to ${REMOTE_DOCKER_HOST}..."
    pkill -f "ssh.*${REMOTE_DOCKER_HOST}.*docker-remote" 2>/dev/null || true
    ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
        "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" \
        "fuser -k ${REMOTE_VLLM_PORT}/tcp 2>/dev/null || true" 2>/dev/null || true
    sleep 2

    rm -f "${REMOTE_DOCKER_SOCKET}"
    ssh -nNT -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
        -o ExitOnForwardFailure=yes \
        -L "${REMOTE_DOCKER_SOCKET}:/var/run/docker.sock" \
        -R "${REMOTE_VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
        "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" &
    SSH_TUNNEL_PID=$!

    for i in $(seq 1 15); do
        if [ -S "${REMOTE_DOCKER_SOCKET}" ]; then break; fi
        sleep 1
    done
    if [ ! -S "${REMOTE_DOCKER_SOCKET}" ]; then
        echo "ERROR: SSH tunnel failed — ${REMOTE_DOCKER_SOCKET} not created" >&2
        exit 1
    fi

    export DOCKER_HOST="unix://${REMOTE_DOCKER_SOCKET}"
    echo "Remote Docker tunnel ready (pid=${SSH_TUNNEL_PID})."
    echo "vLLM reverse tunnel: VM:${REMOTE_VLLM_PORT} -> localhost:${VLLM_PORT}"
fi

# Slurm runs a copy under /var/spool/slurmd/.../ — use submit dir, not BASH_SOURCE.
REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
vllm_pre_start_cleanup

echo "Starting vLLM server..."

"${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --port "${VLLM_PORT}" \
    --model "${VLLM_MODEL_NAME}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
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

if [ "${ENABLE_REMOTE_DOCKER}" = "1" ]; then
    echo "Verifying vLLM reverse tunnel from VM..."
    TUNNEL_OK=0
    for attempt in $(seq 1 3); do
        VM_RESULT=$(ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" \
            "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 http://127.0.0.1:${REMOTE_VLLM_PORT}/v1/models" 2>/dev/null || echo "000")
        if [ "${VM_RESULT}" = "200" ]; then
            TUNNEL_OK=1
            echo "Reverse tunnel verified: VM can reach vLLM (attempt ${attempt})."
            break
        fi
        echo "Reverse tunnel check failed (attempt ${attempt}/3, HTTP ${VM_RESULT}). Restarting tunnel..."
        kill "${SSH_TUNNEL_PID}" 2>/dev/null || true
        sleep 3
        ssh -o StrictHostKeyChecking=no -o ConnectTimeout=5 \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" \
            "fuser -k ${REMOTE_VLLM_PORT}/tcp 2>/dev/null || true" 2>/dev/null || true
        sleep 2
        rm -f "${REMOTE_DOCKER_SOCKET}"
        ssh -nNT -o StrictHostKeyChecking=no -o ServerAliveInterval=30 \
            -o ExitOnForwardFailure=yes \
            -L "${REMOTE_DOCKER_SOCKET}:/var/run/docker.sock" \
            -R "${REMOTE_VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" &
        SSH_TUNNEL_PID=$!
        for i in $(seq 1 10); do
            if [ -S "${REMOTE_DOCKER_SOCKET}" ]; then break; fi
            sleep 1
        done
        export DOCKER_HOST="unix://${REMOTE_DOCKER_SOCKET}"
        sleep 2
    done
    if [ "${TUNNEL_OK}" -ne 1 ]; then
        echo "ERROR: vLLM reverse tunnel could not be established after 3 attempts" >&2
        exit 1
    fi
fi

# Full run from scratch: do not pass --continue_from (otherwise initialize_run loads prior tree and skips cold bootstrap).
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
