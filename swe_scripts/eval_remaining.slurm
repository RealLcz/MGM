#!/bin/bash
#SBATCH --job-name=HGM-EVAL-REM
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --time=96:00:00
#SBATCH --output=logs/eval_remaining_%j.out
#SBATCH --error=logs/eval_remaining_%j.err
#
# Run scripts/eval_node_on_remaining_tasks.py on the Slurm node: vLLM here,
# Docker for SWE-bench like hgm.slurm (remote socket tunnel by default).
#
# SWE evaluation uses Docker (see swe_bench/harness.py). On HPC compute nodes
# there is usually no local Docker daemon; this script mirrors hgm.slurm:
# ENABLE_REMOTE_DOCKER=1 forwards a remote host's docker.sock and reverse-tunnels
# vLLM so containers can call the model.
#
# Submit examples:
#   # EVAL_NODE_ID is optional; by default the script selects the best node in output_mgm.
#   sbatch eval_remaining.slurm
#
#   EVAL_NODE_ID=20260413_113039_681369 EVAL_MAX_WORKERS=1 sbatch eval_remaining.slurm
#
# Dry-run (list tasks only, no Docker / no eval):
#   EVAL_NODE_ID=... EVAL_DRY_RUN=1 sbatch eval_remaining.slurm
#
# Env (optional):
#   EVAL_NODE_ID          — node folder name (default: auto-select best in HGM_OUTPUT_DIR)
#   HGM_OUTPUT_DIR        — default output_mgm
#   EVAL_MAX_WORKERS      — parallel SWE workers (default 1; 4c8G Docker VM: use 1–2)
#   EVAL_TIMEOUT          — per-task seconds for harness (default 7200)
#   DOCKER_API_TIMEOUT    — docker-py HTTP read timeout in seconds (default 7200)
#   EVAL_TASK_IDS         — optional comma/colon/space separated task IDs to rerun
#   EVAL_EXTRA_ARGS       — extra args to the Python script (quoted)
#   EVAL_DRY_RUN          — set to 1 to only print remaining instance ids
#   VLLM_SKIP_PRE_CLEAN   — set to 1 to skip extra cleanup before vLLM (default: run cleanup)
#   REMOTE_DOCKER_USER/HOST — remote Docker SSH target (default: ubuntu@43.131.5.182)
#   REMOTE_DOCKER_PASSWORD — optional SSH password for the remote Docker host

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs
export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_mgm}"

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
export PYTHON_BIN="${PYTHON_BIN:-$HOME/mm/python311/bin/python}"
"${PYTHON_BIN}" -V

if [ -z "${EVAL_NODE_ID:-}" ]; then
    echo "Selecting best EVAL_NODE_ID from ${HGM_OUTPUT_DIR}..."
    EVAL_NODE_ID="$("${PYTHON_BIN}" - "${HGM_OUTPUT_DIR}" <<'PY'
import json
import os
import sys

output_dir = sys.argv[1]
best = None

for entry in sorted(os.listdir(output_dir)):
    node_dir = os.path.join(output_dir, entry)
    meta_path = os.path.join(node_dir, "metadata.json")
    if entry == "initial" or not os.path.isfile(meta_path):
        continue
    try:
        with open(meta_path, encoding="utf-8") as f:
            perf = (json.load(f).get("overall_performance") or {})
    except (OSError, json.JSONDecodeError):
        continue

    submitted = int(perf.get("total_submitted_instances") or 0)
    resolved = int(perf.get("total_resolved_instances") or 0)
    if submitted <= 0:
        continue
    score = perf.get("accuracy_score")
    if score is None:
        score = resolved / submitted
    candidate = (float(score), resolved, submitted, entry)
    if best is None or candidate > best:
        best = candidate

if best is None:
    raise SystemExit(f"No evaluated node metadata found under {output_dir}")

print(best[3])
PY
)"
    if [ -z "${EVAL_NODE_ID}" ]; then
        echo "ERROR: set EVAL_NODE_ID or provide evaluated node metadata under ${HGM_OUTPUT_DIR}" >&2
        exit 1
    fi
    export EVAL_NODE_ID
    echo "Auto-selected EVAL_NODE_ID=${EVAL_NODE_ID} from ${HGM_OUTPUT_DIR}"
fi

if [ "${EVAL_DRY_RUN:-0}" = "1" ]; then
    echo "EVAL_DRY_RUN=1 — listing remaining tasks only (no vLLM / no Docker)."
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" scripts/eval_node_on_remaining_tasks.py \
        --node-id "${EVAL_NODE_ID}" \
        --hgm-output-dir "${HGM_OUTPUT_DIR}" \
        --dry-run
    exit 0
fi

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3-Coder-Next}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3-Coder-Next}"
REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-196608}"
export HGM_MAX_WORKERS="${HGM_MAX_WORKERS:-2}"
export ENABLE_REMOTE_DOCKER="${ENABLE_REMOTE_DOCKER:-1}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/docker-remote.sock}"

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

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="$(hostname -i 2>/dev/null | awk '{print $1}' || true)"
fi
if [ -z "${HOST_IP}" ]; then
    HOST_IP="127.0.0.1"
fi
export VLLM_HOST="${VLLM_HOST:-$HOST_IP}"
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
export REMOTE_VLLM_PORT="${REMOTE_VLLM_PORT:-${VLLM_PORT}}"

if [ "${ENABLE_REMOTE_DOCKER}" = "1" ]; then
    echo "Setting up SSH tunnel to remote Docker on ${REMOTE_DOCKER_HOST}..."

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

    echo "Verifying remote Docker socket..."
    "${PYTHON_BIN}" - <<'PY'
import docker

client = docker.from_env()
print("Remote Docker ping:", client.ping())
print("Remote Docker version:", client.version().get("Version"))
PY
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
    > "logs/vllm_eval_${SLURM_JOB_ID}.stdout.log" 2> "logs/vllm_eval_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT=600
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process (pid=${VLLM_PID}) died. Check logs/vllm_eval_${SLURM_JOB_ID}.stderr.log" >&2
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

export EVAL_MAX_WORKERS="${EVAL_MAX_WORKERS:-1}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-7200}"
export DOCKER_API_TIMEOUT="${DOCKER_API_TIMEOUT:-7200}"

echo "Starting eval_node_on_remaining_tasks for node=${EVAL_NODE_ID}"
echo "  HGM_OUTPUT_DIR=${HGM_OUTPUT_DIR}  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}  DOCKER_API_TIMEOUT=${DOCKER_API_TIMEOUT}  EVAL_TIMEOUT=${EVAL_TIMEOUT}  VLLM_MAX_MODEL_LEN=${VLLM_MAX_MODEL_LEN}"

# shellcheck disable=SC2086
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" scripts/eval_node_on_remaining_tasks.py \
    --node-id "${EVAL_NODE_ID}" \
    --hgm-output-dir "${HGM_OUTPUT_DIR}" \
    --llm "${HGM_LLM_MODEL_ID}" \
    --max-workers "${EVAL_MAX_WORKERS}" \
    --eval-timeout "${EVAL_TIMEOUT}" \
    ${EVAL_EXTRA_ARGS:-}

echo "eval_remaining.slurm finished."
