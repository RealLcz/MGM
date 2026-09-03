#!/bin/bash
#SBATCH --job-name=HGM-PG-INIT
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:4
#SBATCH --exclusive
#SBATCH --time=36:00:00
#SBATCH --output=logs/eval_polyglot_initial_%j.out
#SBATCH --error=logs/eval_polyglot_initial_%j.err
#
# Evaluate the Polyglot initial agent with a local vLLM server.
#
# This mirrors swe_scripts/eval_remaining.slurm:
# - vLLM runs on the Slurm node.
# - Polyglot task containers run through remote Docker by default.
# - An SSH reverse tunnel exposes vLLM to those containers.
#
# Submit examples:
#   sbatch polyglot_scripts/eval_initial_agent.slurm
#
#   POLYGLOT_TASK_SET=small EVAL_MAX_WORKERS=2 sbatch polyglot_scripts/eval_initial_agent.slurm
#
#   EVAL_TASK_IDS="python__beer-song,go__book-store" sbatch polyglot_scripts/eval_initial_agent.slurm
#
# Dry-run (list tasks only, no Docker / no eval):
#   EVAL_DRY_RUN=1 POLYGLOT_TASK_SET=medium+small sbatch polyglot_scripts/eval_initial_agent.slurm
#
# Env (optional):
#   VLLM_MODEL_NAME       - default Qwen/Qwen3.6-35B-A3B
#   SERVED_MODEL_NAME     - default VLLM_MODEL_NAME
#   HGM_LLM_MODEL_ID      - default SERVED_MODEL_NAME; model passed to the agent
#   INITIAL_AGENT_PATH    - default initial_polyglot/default_agent/src
#   HGM_OUTPUT_DIR        - default output_polyglot/initial_agent_qwen3_6_35b_a3b_<timestamp>
#   POLYGLOT_TASK_SET     - small, medium, medium+small, all, or a JSON file path
#   EVAL_TASK_IDS         - optional comma/colon/space separated task IDs; overrides POLYGLOT_TASK_SET
#   EVAL_MAX_WORKERS      - parallel Polyglot workers (default 2)
#   EVAL_TIMEOUT          - per-task seconds for the coding agent (default 3600)
#   DOCKER_API_TIMEOUT    - docker-py HTTP read timeout in seconds (default 7200)
#   VLLM_SKIP_PRE_CLEAN   - set to 1 to skip extra cleanup before vLLM
#   REMOTE_DOCKER_USER/HOST - remote Docker SSH target
#   REMOTE_DOCKER_PASSWORD - optional SSH password for the remote Docker host
#   PYTHON_BIN            - default: ${CONDA_PREFIX}/bin/python after conda activate HGM (vLLM from HGM)
REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

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
REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify
# vLLM and embedded Python snippets use the activated conda env (HGM) unless PYTHON_BIN is preset.
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${VLLM_MODEL_NAME}}"
export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-98304}"
export EVAL_MAX_WORKERS="${EVAL_MAX_WORKERS:-2}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-3600}"
export DOCKER_API_TIMEOUT="${DOCKER_API_TIMEOUT:-7200}"

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

if [ -z "${HGM_OUTPUT_DIR:-}" ]; then
    HGM_OUTPUT_DIR="output_polyglot/initial_agent_qwen3_6_35b_a3b_$(date +%Y%m%d_%H%M%S)"
fi
export HGM_OUTPUT_DIR
export INITIAL_AGENT_PATH="${INITIAL_AGENT_PATH:-initial_polyglot/default_agent/src}"
export POLYGLOT_TASK_SET="${POLYGLOT_TASK_SET:-medium+small}"
export EVAL_MODEL_NAME="${EVAL_MODEL_NAME:-initial_agent_Qwen3.6-35B-A3B}"

if [ "${EVAL_DRY_RUN:-0}" = "1" ]; then
    echo "EVAL_DRY_RUN=1 - listing Polyglot tasks only (no vLLM / no Docker)."
    "${PYTHON_BIN}" - <<'PY'
import json
import os
import re

def load_ids(task_set):
    if os.path.isfile(task_set):
        with open(task_set, encoding="utf-8") as f:
            return json.load(f)
    if task_set == "small":
        paths = ["polyglot/subsets/small.json"]
    elif task_set == "medium":
        paths = ["polyglot/subsets/medium.json"]
    elif task_set in ("medium+small", "small+medium"):
        paths = ["polyglot/subsets/medium.json", "polyglot/subsets/small.json"]
    elif task_set == "all":
        with open("polyglot/polyglot_benchmark_metadata.json", encoding="utf-8") as f:
            return [entry["instance_id"] for entry in json.load(f)]
    else:
        raise SystemExit(f"Unknown POLYGLOT_TASK_SET={task_set!r}")
    ids = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            ids.extend(json.load(f))
    return ids

raw = os.environ.get("EVAL_TASK_IDS", "")
if raw.strip():
    task_ids = [part for part in re.split(r"[\s,:]+", raw.strip()) if part]
else:
    seen = set()
    task_ids = []
    for task_id in load_ids(os.environ["POLYGLOT_TASK_SET"]):
        if task_id not in seen:
            seen.add(task_id)
            task_ids.append(task_id)

print(f"tasks={len(task_ids)}")
for task_id in task_ids:
    print(task_id)
PY
    exit 0
fi

REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
if [ -n "${POLYGLOT_CUDA_VISIBLE_DEVICES:-}" ]; then
    export MGM_CUDA_VISIBLE_DEVICES="${POLYGLOT_CUDA_VISIBLE_DEVICES}"
elif [ -z "${MGM_CUDA_VISIBLE_DEVICES:-}" ]; then
    if [ -n "${SLURM_STEP_GPUS:-}" ]; then
        export CUDA_VISIBLE_DEVICES="${SLURM_STEP_GPUS}"
    elif [ -n "${SLURM_JOB_GPUS:-}" ]; then
        export CUDA_VISIBLE_DEVICES="${SLURM_JOB_GPUS}"
    fi
fi
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup
POLYGLOT_VISIBLE_GPUS="$(printf '%s\n' "${CUDA_VISIBLE_DEVICES}" | tr ',' '\n' | grep -c '.')"
if [ "${POLYGLOT_VISIBLE_GPUS}" -ne 4 ]; then
    echo "ERROR: Polyglot evaluation must use exactly 4 GPUs; got CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}" >&2
    echo "Submit with --gres=gpu:4 and optionally POLYGLOT_CUDA_VISIBLE_DEVICES=0,1,2,3." >&2
    exit 1
fi
if [ "${TENSOR_PARALLEL_SIZE}" -gt 4 ]; then
    echo "ERROR: TENSOR_PARALLEL_SIZE must be <= 4 for Polyglot 4-GPU jobs; got ${TENSOR_PARALLEL_SIZE}" >&2
    exit 1
fi

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

if false; then # Apptainer-only: remote Docker removed
    echo "Setting up SSH tunnel to remote Docker on ${REMOTE_DOCKER_HOST}..."

    pkill -f "ssh.*${REMOTE_DOCKER_HOST}.*docker-polyglot-initial" 2>/dev/null || true
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
        echo "ERROR: SSH tunnel failed - ${REMOTE_DOCKER_SOCKET} not created" >&2
        exit 1
    fi

    export DOCKER_HOST="unix://${REMOTE_DOCKER_SOCKET}"
    echo "Remote Docker tunnel ready (pid=${SSH_TUNNEL_PID})."
    echo "vLLM reverse tunnel: VM:${REMOTE_VLLM_PORT} -> localhost:${VLLM_PORT}"
fi

# Slurm runs a copy under /var/spool/slurmd/.../ - use submit dir, not BASH_SOURCE.
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
    > "logs/vllm_polyglot_initial_${SLURM_JOB_ID}.stdout.log" 2> "logs/vllm_polyglot_initial_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT=600
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process (pid=${VLLM_PID}) died. Check logs/vllm_polyglot_initial_${SLURM_JOB_ID}.stderr.log" >&2
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

if false; then # Apptainer-only: remote Docker removed
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

mkdir -p "${HGM_OUTPUT_DIR}" "${HGM_OUTPUT_DIR}/predictions"

echo "Starting Polyglot initial-agent evaluation"
echo "  output_dir=${HGM_OUTPUT_DIR}"
echo "  initial_agent_path=${INITIAL_AGENT_PATH}"
echo "  model=${HGM_LLM_MODEL_ID}"
echo "  task_set=${POLYGLOT_TASK_SET}  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}  EVAL_TIMEOUT=${EVAL_TIMEOUT}"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" - <<'PY'
import json
import os
import re
from pathlib import Path

import polyglot.harness as polyglot_harness


def load_task_ids(task_set):
    if os.path.isfile(task_set):
        with open(task_set, encoding="utf-8") as f:
            return json.load(f)
    if task_set == "small":
        paths = ["polyglot/subsets/small.json"]
    elif task_set == "medium":
        paths = ["polyglot/subsets/medium.json"]
    elif task_set in ("medium+small", "small+medium"):
        paths = ["polyglot/subsets/medium.json", "polyglot/subsets/small.json"]
    elif task_set == "all":
        with open("polyglot/polyglot_benchmark_metadata.json", encoding="utf-8") as f:
            return [entry["instance_id"] for entry in json.load(f)]
    else:
        raise SystemExit(f"Unknown POLYGLOT_TASK_SET={task_set!r}")

    ids = []
    for path in paths:
        with open(path, encoding="utf-8") as f:
            ids.extend(json.load(f))
    return ids


def dedupe(ids):
    seen = set()
    out = []
    for task_id in ids:
        if task_id not in seen:
            seen.add(task_id)
            out.append(task_id)
    return out


raw_ids = os.environ.get("EVAL_TASK_IDS", "")
if raw_ids.strip():
    task_ids = dedupe(part for part in re.split(r"[\s,:]+", raw_ids.strip()) if part)
else:
    task_ids = dedupe(load_task_ids(os.environ["POLYGLOT_TASK_SET"]))

init_agent_path = os.environ["INITIAL_AGENT_PATH"]
default_init_agent_path = "initial_polyglot/default_agent/src"
if not os.path.isdir(init_agent_path):
    if init_agent_path == default_init_agent_path:
        from utils.docker_utils import copy_src_files

        print(f"{init_agent_path} does not exist; creating it from the current repo snapshot.")
        copy_src_files(init_agent_path, build_image=False)
    else:
        raise SystemExit(f"INITIAL_AGENT_PATH does not exist: {init_agent_path}")

out_dir = Path(os.environ["HGM_OUTPUT_DIR"])
pred_dir = out_dir / "predictions"
out_dir.mkdir(parents=True, exist_ok=True)
pred_dir.mkdir(parents=True, exist_ok=True)

polyglot_harness.llm = os.environ["HGM_LLM_MODEL_ID"]
polyglot_harness.timeout = int(os.environ["EVAL_TIMEOUT"])

print(f"Submitting {len(task_ids)} Polyglot tasks.")
polyglot_harness.harness(
    dataset_path="polyglot/polyglot_benchmark_metadata.json",
    test_task_list=task_ids,
    max_workers=min(int(os.environ["EVAL_MAX_WORKERS"]), max(1, len(task_ids))),
    model_name_or_path=os.environ["EVAL_MODEL_NAME"],
    model_patch_paths=None,
    pred_dname=str(pred_dir),
    output_dir=str(out_dir),
    skip_existing=os.environ.get("EVAL_SKIP_EXISTING", "1") != "0",
    init_agent_path=init_agent_path,
)
PY

echo "eval_initial_agent.slurm finished."
