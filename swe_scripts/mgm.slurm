#!/bin/bash
#SBATCH --job-name=MGM
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
# If jobs share the node and vLLM dies with NCCL/TCPStore errors, use a clean node, e.g.:
#   #SBATCH --exclusive
# or submit with only free GPU indices: sbatch --export=ALL,MGM_CUDA_VISIBLE_DEVICES=4,5,6,7 swe_scripts/mgm.slurm
#SBATCH --time=96:00:00
#SBATCH --output=logs/hgm_%j.out
#SBATCH --error=logs/hgm_%j.err

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

SSH_TUNNEL_PID=""
VLLM_PID=""
SSH_PASSWORD_HELPER=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null && sleep 1 && kill -9 "${VLLM_PID}" 2>/dev/null
    [ -n "${SSH_TUNNEL_PID}" ] && kill "${SSH_TUNNEL_PID}" 2>/dev/null
    [ -n "${SSH_PASSWORD_HELPER}" ] && rm -f "${SSH_PASSWORD_HELPER}"
    rm -f "${REMOTE_DOCKER_SOCKET:-}"
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate HGM
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
# GPU / tensor-parallel: see swe_scripts/cuda_and_tensor_parallel.inc.sh
# If you share a node, prefer #SBATCH --exclusive, or pass only free indices, e.g.:
#   sbatch --export=ALL,MGM_CUDA_VISIBLE_DEVICES=4,5,6,7 swe_scripts/mgm.slurm
REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-262144}"
export HGM_MAX_WORKERS="${HGM_MAX_WORKERS:-2}"
export ENABLE_REMOTE_DOCKER="${ENABLE_REMOTE_DOCKER:-1}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/docker-remote.sock}"
export REMOTE_DOCKER_PASSWORD="${REMOTE_DOCKER_PASSWORD:-@TXYfwq798798}"

if [ -n "${REMOTE_DOCKER_PASSWORD:-}" ]; then
    if command -v sshpass >/dev/null 2>&1; then
        echo "Remote Docker SSH password auth enabled via sshpass."
    else
        SSH_PASSWORD_HELPER="$(mktemp "${TMPDIR:-/tmp}/hgm-ssh-askpass.XXXXXX")"
        chmod 700 "${SSH_PASSWORD_HELPER}"
        cat > "${SSH_PASSWORD_HELPER}" <<'EOF'
#!/bin/sh
printf '%s\n' "${REMOTE_DOCKER_PASSWORD}"
EOF
        export SSH_ASKPASS="${SSH_PASSWORD_HELPER}"
        export SSH_ASKPASS_REQUIRE=force
        export DISPLAY="${DISPLAY:-hgm-askpass:0}"
        echo "Remote Docker SSH password auth enabled via SSH_ASKPASS."
    fi
fi

ssh_remote() {
    if [ -n "${REMOTE_DOCKER_PASSWORD:-}" ] && command -v sshpass >/dev/null 2>&1; then
        SSHPASS="${REMOTE_DOCKER_PASSWORD}" sshpass -e ssh \
            -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=5 \
            -o NumberOfPasswordPrompts=1 \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" \
            "$@"
    else
        ssh -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=5 \
            -o NumberOfPasswordPrompts=1 \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" \
            "$@"
    fi
}

ssh_tunnel() {
    if [ -n "${REMOTE_DOCKER_PASSWORD:-}" ] && command -v sshpass >/dev/null 2>&1; then
        SSHPASS="${REMOTE_DOCKER_PASSWORD}" sshpass -e ssh -nNT \
            -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ServerAliveInterval=30 \
            -o ServerAliveCountMax=20 \
            -o ExitOnForwardFailure=yes \
            -o NumberOfPasswordPrompts=1 \
            -L "${REMOTE_DOCKER_SOCKET}:/var/run/docker.sock" \
            -R "${REMOTE_VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}"
    else
        ssh -nNT -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ServerAliveInterval=30 \
            -o ServerAliveCountMax=20 \
            -o ExitOnForwardFailure=yes \
            -o NumberOfPasswordPrompts=1 \
            -L "${REMOTE_DOCKER_SOCKET}:/var/run/docker.sock" \
            -R "${REMOTE_VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}"
    fi
}

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
    ssh_remote "fuser -k ${REMOTE_VLLM_PORT}/tcp 2>/dev/null || true" 2>/dev/null || true
    sleep 2

    rm -f "${REMOTE_DOCKER_SOCKET}"
    ssh_tunnel &
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
        VM_RESULT=$(ssh_remote "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 http://127.0.0.1:${REMOTE_VLLM_PORT}/v1/models" 2>/dev/null || echo "000")
        if [ "${VM_RESULT}" = "200" ]; then
            TUNNEL_OK=1
            echo "Reverse tunnel verified: VM can reach vLLM (attempt ${attempt})."
            break
        fi
        echo "Reverse tunnel check failed (attempt ${attempt}/3, HTTP ${VM_RESULT}). Restarting tunnel..."
        kill "${SSH_TUNNEL_PID}" 2>/dev/null || true
        sleep 3
        ssh_remote "fuser -k ${REMOTE_VLLM_PORT}/tcp 2>/dev/null || true" 2>/dev/null || true
        sleep 2
        rm -f "${REMOTE_DOCKER_SOCKET}"
        ssh_tunnel &
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

echo "Pre-pulling Docker images (timeout ${PRE_PULL_TIMEOUT:-600}s)..."
timeout "${PRE_PULL_TIMEOUT:-600}" "${PYTHON_BIN}" -u scripts/pull_epoch_images_proxy.py all || {
    echo "WARNING: Image pre-pull incomplete (timeout or error). HGM will proceed."
    echo "Missing images will cause per-task failures that are handled gracefully."
}

echo "Starting MGM (fresh run unless paths.continue_from is set in config.yaml; A:B:C=0.1:0.45:0.45, max_task_evals=200)..."
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" hgm.py \
    --max_workers "${HGM_MAX_WORKERS}" \
    --self_improve_llm "${HGM_LLM_MODEL_ID}" \
    --downstream_llm "${HGM_LLM_MODEL_ID}" \
    --diagnose_llm "${HGM_LLM_MODEL_ID}" \
    --failed_pool_boost 1.0 \
    --self_improve_weight_a 0.1 \
    --self_improve_weight_b 0.45 \
    --self_improve_weight_c 0.45
