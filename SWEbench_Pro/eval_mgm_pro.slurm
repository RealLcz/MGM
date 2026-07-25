#!/bin/bash
#SBATCH --job-name=MGM-PRO-EVAL
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --time=96:00:00
#SBATCH --output=SWEbench_Pro/logs/eval_mgm_pro_%j.out
#SBATCH --error=SWEbench_Pro/logs/eval_mgm_pro_%j.err
#
# Generic MGM agent evaluation template for the 60-task SWE-bench Pro subset.
#
# Submit from repo root; choose exactly one agent with SWE_PRO_AGENT_MODE:
#   sbatch --export=ALL,SWE_PRO_AGENT_MODE=best SWEbench_Pro/eval_mgm_pro.slurm
#
# Smoke test one task:
#   sbatch --export=ALL,SWE_PRO_AGENT_MODE=best,SWE_PRO_LIMIT=1 SWEbench_Pro/eval_mgm_pro.slurm
#
# Evaluate the current best MGM node:
#   sbatch --export=ALL,SWE_PRO_AGENT_MODE=best SWEbench_Pro/eval_mgm_pro.slurm
#
# Evaluate the initial agent:
#   sbatch --export=ALL,SWE_PRO_AGENT_MODE=initial SWEbench_Pro/eval_mgm_pro.slurm
#
# Evaluate the Polyglot initial agent:
#   sbatch --export=ALL,SWE_PRO_AGENT_MODE=initial,SWE_PRO_INIT_AGENT_SRC=initial_polyglot/default_agent/src,VLLM_MODEL_NAME=Qwen/Qwen3.6-35B-A3B,SERVED_MODEL_NAME=Qwen/Qwen3.6-35B-A3B,HGM_LLM_MODEL_ID=Qwen/Qwen3.6-35B-A3B,TENSOR_PARALLEL_SIZE=4,GPU_MEMORY_UTILIZATION=0.92,VLLM_MAX_MODEL_LEN=112000,SWE_PRO_MAX_WORKERS=3 SWEbench_Pro/eval_mgm_pro.slurm
#
# Evaluate a specific node:
#   sbatch --export=ALL,SWE_PRO_AGENT_MODE=node,SWE_PRO_NODE_ID=20260413_113039_681369 SWEbench_Pro/eval_mgm_pro.slurm
#
# Do not pull missing SWE-bench Pro images during evaluation:
#   sbatch --export=ALL,SWE_PRO_PULL=never SWEbench_Pro/eval_mgm_pro.slurm
#
# Useful env:
#   SWE_PRO_AGENT_MODE     initial | best | node              required
#   SWE_PRO_NODE_ID        node folder under HGM_OUTPUT_DIR   required for mode=node
#   SWE_PRO_INIT_AGENT_SRC initial agent source dir           default: initial_swe/default_agent/src
#   SWE_PRO_AGENT_ENTRYPOINT
#                          auto | coding_agent.py | coding_agent_polyglot.py
#                                                               default: auto
#   SWE_PRO_OUTPUT_DIR     output directory                   default: by mode/node
#   SWE_PRO_LIMIT          first N tasks only                 default: unset/full 60
#   SWE_PRO_MAX_WORKERS    parallel task containers          default: 1
#   SWE_PRO_PULL           missing | never | always           default: missing
#   SWE_PRO_PREPULL        1 to pre-pull all subset images    default: 0
#   SWE_PRO_PRUNE_AFTER    1 to delete remote images for completed tasks after eval
#                                                               default: 0
#   SWE_PRO_AGENT_PIP_INDEX_URL
#                          pip index for agent deps           default: Tencent mirror
#   SWE_PRO_AGENT_PIP_CONSTRAINTS
#                          constraints for agent deps         default: SWEbench_Pro/agent_constraints.txt
#   HGM_OUTPUT_DIR         MGM output dir                     default: output_mgm
#   HGM_LLM_MODEL_ID       model name passed to agent         default: SERVED_MODEL_NAME
#   VLLM_MODEL_NAME        vLLM model to serve                default: Qwen/Qwen3-Coder-Next
#   VLLM_PRELOAD_HF_ASSETS 1|0|auto preload tokenizer/processor before vLLM default: auto
#   REMOTE_DOCKER_HOST     Tencent Cloud Docker host          default: 43.131.5.182
#   REMOTE_DOCKER_PASSWORD optional SSH password for VM

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p SWEbench_Pro/logs logs

SSH_TUNNEL_PID=""
VLLM_PID=""
SSH_PASSWORD_HELPER=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null && sleep 1 && kill -9 "${VLLM_PID}" 2>/dev/null || true
    [ -n "${SSH_TUNNEL_PID}" ] && kill "${SSH_TUNNEL_PID}" 2>/dev/null || true
    [ -n "${SSH_PASSWORD_HELPER}" ] && rm -f "${SSH_PASSWORD_HELPER}" || true
    rm -f "${REMOTE_DOCKER_SOCKET:-}" || true
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate HGM
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3-Coder-Next}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${VLLM_MODEL_NAME}}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"
export VLLM_PORT="${VLLM_PORT:-8000}"
export VLLM_PRELOAD_HF_ASSETS="${VLLM_PRELOAD_HF_ASSETS:-auto}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-262144}"
export ENABLE_REMOTE_DOCKER="${ENABLE_REMOTE_DOCKER:-1}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/swebench-pro-docker.sock}"
export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_mgm}"
export SWE_PRO_AGENT_PIP_INDEX_URL="${SWE_PRO_AGENT_PIP_INDEX_URL:-https://mirrors.cloud.tencent.com/pypi/simple}"
export SWE_PRO_AGENT_PIP_CONSTRAINTS="${SWE_PRO_AGENT_PIP_CONSTRAINTS:-SWEbench_Pro/agent_constraints.txt}"

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

if [ -z "${TOOL_CALL_PARSER:-}" ]; then
    case "${VLLM_MODEL_NAME}" in
        *Qwen3-Coder*|*Qwen3*)  TOOL_CALL_PARSER="qwen3_coder" ;;
        *gemma-4*|*gemma4*)      TOOL_CALL_PARSER="functiongemma" ;;
        *)                       TOOL_CALL_PARSER="hermes" ;;
    esac
fi
export TOOL_CALL_PARSER

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
export REMOTE_VLLM_PORT="${REMOTE_VLLM_PORT:-${VLLM_PORT}}"
export VLLM_CONTAINER_HOST="${VLLM_CONTAINER_HOST:-127.0.0.1}"

if [ "${ENABLE_REMOTE_DOCKER}" = "1" ]; then
    echo "Setting up SSH tunnel to remote Docker on ${REMOTE_DOCKER_HOST}..."
    pkill -f "ssh.*${REMOTE_DOCKER_HOST}.*swebench-pro-docker" 2>/dev/null || true
    ssh_remote "fuser -k ${REMOTE_VLLM_PORT}/tcp 2>/dev/null || true" 2>/dev/null || true
    sleep 2

    rm -f "${REMOTE_DOCKER_SOCKET}"
    ssh_tunnel &
    SSH_TUNNEL_PID=$!
    for _ in $(seq 1 15); do
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

    "${PYTHON_BIN}" - <<'PY'
import docker

client = docker.from_env()
print("Remote Docker ping:", client.ping())
print("Remote Docker version:", client.version().get("Version"))
PY
fi

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
vllm_pre_start_cleanup

PRELOAD_HF_ASSETS="${VLLM_PRELOAD_HF_ASSETS}"
if [ "${PRELOAD_HF_ASSETS}" = "auto" ]; then
    case "${VLLM_MODEL_NAME}" in
        *Qwen3.6*|*Qwen2-VL*|*Qwen2.5-VL*|*Qwen3-VL*|*VL*) PRELOAD_HF_ASSETS=1 ;;
        *) PRELOAD_HF_ASSETS=0 ;;
    esac
fi
if [ "${PRELOAD_HF_ASSETS}" = "1" ]; then
    echo "Preloading HF tokenizer/processor for ${VLLM_MODEL_NAME}..."
    "${PYTHON_BIN}" - <<'PY'
import os

from transformers import AutoConfig, AutoProcessor, AutoTokenizer

model = os.environ["VLLM_MODEL_NAME"]
kwargs = {"trust_remote_code": True}
AutoConfig.from_pretrained(model, **kwargs)
AutoTokenizer.from_pretrained(model, **kwargs)
AutoProcessor.from_pretrained(model, **kwargs)
print("HF tokenizer/processor preload complete.")
PY
fi

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
    > "SWEbench_Pro/logs/vllm_pro_${SLURM_JOB_ID}.stdout.log" \
    2> "SWEbench_Pro/logs/vllm_pro_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT="${VLLM_MAX_WAIT:-600}"
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process (pid=${VLLM_PID}) died. Check SWEbench_Pro/logs/vllm_pro_${SLURM_JOB_ID}.stderr.log" >&2
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
        for _ in $(seq 1 10); do
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

echo "Preparing SWE-bench Pro official assets..."
SWE_PRO_SYNC_ASSETS="${SWE_PRO_SYNC_ASSETS:-1}"
if [ "${SWE_PRO_SYNC_ASSETS}" = "1" ]; then
    "${PYTHON_BIN}" -u SWEbench_Pro/make_eval_inputs.py \
        --subset "${SWE_PRO_SUBSET:-SWEbench_Pro/subsets/test.json}" \
        --output-dir "${SWE_PRO_DATA_DIR:-SWEbench_Pro/data}"
    if [ ! -d "SWEbench_Pro/upstream/run_scripts" ]; then
        bash SWEbench_Pro/sync_official_eval_assets.sh
    fi
fi

if [ "${SWE_PRO_PREPULL:-0}" = "1" ]; then
    echo "Pre-pulling SWE-bench Pro images..."
    timeout "${SWE_PRO_PREPULL_TIMEOUT:-7200}" "${PYTHON_BIN}" -u SWEbench_Pro/pull_images.py \
        --subset "${SWE_PRO_SUBSET:-SWEbench_Pro/subsets/test.json}" \
        --platform "${SWE_PRO_DOCKER_PLATFORM:-linux/amd64}" || {
        echo "WARNING: SWE-bench Pro image pre-pull incomplete. Evaluation will continue."
    }
fi

SWE_PRO_AGENT_MODE="${SWE_PRO_AGENT_MODE:-}"
if [ -z "${SWE_PRO_AGENT_MODE}" ]; then
    cat >&2 <<'EOF'
ERROR: SWE_PRO_AGENT_MODE is required.

Choose one agent explicitly, for example:
  sbatch --export=ALL,SWE_PRO_AGENT_MODE=initial SWEbench_Pro/eval_mgm_pro.slurm
  sbatch --export=ALL,SWE_PRO_AGENT_MODE=best SWEbench_Pro/eval_mgm_pro.slurm
  sbatch --export=ALL,SWE_PRO_AGENT_MODE=node,SWE_PRO_NODE_ID=20260413_113039_681369 SWEbench_Pro/eval_mgm_pro.slurm
EOF
    exit 1
fi
SWE_PRO_MAX_WORKERS="${SWE_PRO_MAX_WORKERS:-1}"
SWE_PRO_PULL="${SWE_PRO_PULL:-missing}"
SWE_PRO_AGENT_TIMEOUT="${SWE_PRO_AGENT_TIMEOUT:-3600}"
SWE_PRO_EVAL_TIMEOUT="${SWE_PRO_EVAL_TIMEOUT:-7200}"
export SWE_PRO_EVAL_TIMEOUT
EVAL_OUTPUT_DIR=""

COMMON_ARGS=(
    --subset "${SWE_PRO_SUBSET:-SWEbench_Pro/subsets/test.json}"
    --upstream-dir "${SWE_PRO_UPSTREAM_DIR:-SWEbench_Pro/upstream}"
    --hgm-output-dir "${HGM_OUTPUT_DIR}"
    --init-agent-src "${SWE_PRO_INIT_AGENT_SRC:-initial_swe/default_agent/src}"
    --agent-entrypoint "${SWE_PRO_AGENT_ENTRYPOINT:-auto}"
    --llm "${HGM_LLM_MODEL_ID}"
    --max-workers "${SWE_PRO_MAX_WORKERS}"
    --agent-timeout "${SWE_PRO_AGENT_TIMEOUT}"
    --eval-timeout "${SWE_PRO_EVAL_TIMEOUT}"
    --docker-timeout "${DOCKER_API_TIMEOUT:-7200}"
    --agent-pip-index-url "${SWE_PRO_AGENT_PIP_INDEX_URL}"
    --agent-pip-constraints "${SWE_PRO_AGENT_PIP_CONSTRAINTS}"
    --pull "${SWE_PRO_PULL}"
)

if [ -n "${SWE_PRO_AGENT_PIP_EXTRA_INDEX_URL:-}" ]; then
    COMMON_ARGS+=(--agent-pip-extra-index-url "${SWE_PRO_AGENT_PIP_EXTRA_INDEX_URL}")
fi
if [ -n "${SWE_PRO_AGENT_PIP_TRUSTED_HOST:-}" ]; then
    COMMON_ARGS+=(--agent-pip-trusted-host "${SWE_PRO_AGENT_PIP_TRUSTED_HOST}")
fi
if [ -n "${SWE_PRO_AGENT_PIP_TIMEOUT:-}" ]; then
    COMMON_ARGS+=(--agent-pip-timeout "${SWE_PRO_AGENT_PIP_TIMEOUT}")
fi

if [ -n "${SWE_PRO_LIMIT:-}" ]; then
    COMMON_ARGS+=(--limit "${SWE_PRO_LIMIT}")
fi
if [ -n "${SWE_PRO_TASK_ID:-}" ]; then
    COMMON_ARGS+=(--task-id "${SWE_PRO_TASK_ID}")
fi
if [ -n "${SWE_PRO_DOCKER_PLATFORM:-}" ]; then
    COMMON_ARGS+=(--docker-platform "${SWE_PRO_DOCKER_PLATFORM}")
fi
if [ "${SWE_PRO_REDO:-0}" = "1" ]; then
    COMMON_ARGS+=(--redo)
fi

run_initial() {
    EVAL_OUTPUT_DIR="${SWE_PRO_OUTPUT_DIR:-SWEbench_Pro/outputs/initial}"
    echo "Evaluating SWE-bench Pro MGM initial agent..."
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u SWEbench_Pro/run_agent_eval.py \
        --agent initial \
        --output-dir "${EVAL_OUTPUT_DIR}" \
        "${COMMON_ARGS[@]}"
}

run_best() {
    EVAL_OUTPUT_DIR="${SWE_PRO_OUTPUT_DIR:-SWEbench_Pro/outputs/best}"
    echo "Evaluating SWE-bench Pro MGM best agent..."
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u SWEbench_Pro/run_agent_eval.py \
        --agent best \
        --output-dir "${EVAL_OUTPUT_DIR}" \
        "${COMMON_ARGS[@]}"
}

run_node() {
    if [ -z "${SWE_PRO_NODE_ID:-}" ]; then
        echo "ERROR: SWE_PRO_NODE_ID is required when SWE_PRO_AGENT_MODE=node" >&2
        exit 1
    fi
    EVAL_OUTPUT_DIR="${SWE_PRO_OUTPUT_DIR:-SWEbench_Pro/outputs/node_${SWE_PRO_NODE_ID}}"
    echo "Evaluating SWE-bench Pro MGM node ${SWE_PRO_NODE_ID}..."
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u SWEbench_Pro/run_agent_eval.py \
        --agent node \
        --node-id "${SWE_PRO_NODE_ID}" \
        --output-dir "${EVAL_OUTPUT_DIR}" \
        "${COMMON_ARGS[@]}"
}

case "${SWE_PRO_AGENT_MODE}" in
    initial) run_initial ;;
    best)    run_best ;;
    node)    run_node ;;
    *)
        echo "ERROR: unknown SWE_PRO_AGENT_MODE=${SWE_PRO_AGENT_MODE}; expected initial|best|node" >&2
        exit 1
        ;;
esac

if [ "${SWE_PRO_PRUNE_AFTER:-0}" = "1" ]; then
    echo "Pruning remote Docker images for completed tasks in ${EVAL_OUTPUT_DIR}..."
    IMAGE_MAP="${SWE_PRO_DATA_DIR:-SWEbench_Pro/data}/image_map.json" \
    OUTPUT_DIR="${EVAL_OUTPUT_DIR}" \
        bash SWEbench_Pro/scripts/remote_prune_cached.sh || {
        echo "WARNING: remote Docker prune failed. Continuing so eval result remains available." >&2
    }
fi

echo "eval_mgm_pro.slurm finished."
