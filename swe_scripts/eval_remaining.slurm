#!/bin/bash
#SBATCH --job-name=SWE-BEST-EVAL
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=96
#SBATCH --mem=0
#SBATCH --gres=gpu:8
#SBATCH --partition=all
#SBATCH --exclude=cn34,cn35
#SBATCH --exclusive
#SBATCH --requeue
#SBATCH --time=96:00:00
#SBATCH --output=logs/eval_remaining_%j.out
#SBATCH --error=logs/eval_remaining_%j.err
#
# Run scripts/eval_node_on_remaining_tasks.py: vLLM + local Apptainer SWE-bench Verified-60 eval.
#
# Submit examples (export env in shell, then sbatch --export=ALL to avoid comma issues in task IDs):
#   export HGM_OUTPUT_DIR=output_mgm EVAL_NODE_ID=20260616_024835_287218 EVAL_FULL_VERIFIED=1
#   sbatch --job-name=SWE-MGM-BEST --export=ALL swe_scripts/eval_remaining.slurm
#
#   export HGM_OUTPUT_DIR=output_hgm EVAL_NODE_ID=20260604_123351_915920 EVAL_FULL_VERIFIED=1
#   export EVAL_RESUME_PRED_SUBDIR=20260604_123351_915920_34 EVAL_MAX_WORKERS=4
#   sbatch --job-name=SWE-HGM-BEST --export=ALL swe_scripts/eval_remaining.slurm
#
# Resume partial run (skip tasks that already have predictions/*.json):
#   export EVAL_FULL_VERIFIED=1 EVAL_RESUME_PRED_SUBDIR=<predictions_subdir> EVAL_MAX_WORKERS=4
#
# Dry-run (list tasks only, no eval):
#   EVAL_DRY_RUN=1 sbatch --export=ALL swe_scripts/eval_remaining.slurm

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs
export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_mgm}"

VLLM_PID=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null && sleep 1 && kill -9 "${VLLM_PID}" 2>/dev/null
}
trap cleanup EXIT INT TERM

CONDA_BASE="${CONDA_BASE:-/mnt/vast/home/ym56kacy/miniconda3}"
if [[ -f "${CONDA_BASE}/etc/profile.d/conda.sh" ]]; then
  # shellcheck source=/dev/null
  source "${CONDA_BASE}/etc/profile.d/conda.sh"
  conda activate HGM
  export PYTHON_BIN="${CONDA_PREFIX}/bin/python"
else
  export PYTHON_BIN="${CONDA_BASE}/envs/HGM/bin/python"
fi
"${PYTHON_BIN}" -V

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/swe_eval_dataset.inc.sh"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cache_env.inc.sh"
cache_env_setup
export APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${REPO_ROOT}/apptainer_images}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

if [ "${EVAL_FULL_VERIFIED:-0}" = "1" ]; then
    export EVAL_TASK_IDS="$("${PYTHON_BIN}" - <<'PY'
import json
small = json.load(open("swe_bench/subsets/small.json"))
medium = json.load(open("swe_bench/subsets/medium.json"))
print(",".join(dict.fromkeys(small + medium)))
PY
)"
    echo "EVAL_FULL_VERIFIED=1: loaded ${EVAL_TASK_IDS//,/ } tasks (count=$(echo "${EVAL_TASK_IDS}" | tr ',' '\n' | wc -l))"
fi

# If EVAL_INIT_AGENT_SRC is already set (e.g. pointing to a best_agent dir
# with evolution patches + fixes pre-applied), skip staging and use it directly.
# This avoids re-applying evolution patches on top of an already-evolved agent.
if [ -n "${EVAL_INIT_AGENT_SRC:-}" ] && [ -d "${EVAL_INIT_AGENT_SRC}" ]; then
    echo "Using pre-staged agent src: ${EVAL_INIT_AGENT_SRC} (skipping sync_initial_swe_agent)"
else
    AGENT_STAGE="${REPO_ROOT}/.cache/swe_eval_stage_${SLURM_JOB_ID:-manual}"
    rm -rf "${AGENT_STAGE}"
    echo "Staging agent src at ${AGENT_STAGE} (harness files only)..."
    INIT_AGENT_DEST="${AGENT_STAGE}" bash "${REPO_ROOT}/scripts/sync_initial_swe_agent.sh"
    export EVAL_INIT_AGENT_SRC="${AGENT_STAGE}"
fi
export EVAL_EXTRA_ARGS="--init-agent-src ${EVAL_INIT_AGENT_SRC}"
echo "Using init agent src: ${EVAL_INIT_AGENT_SRC}"

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
        with open(meta_path) as f:
            metadata = json.load(f)
    except Exception:
        continue
    perf = metadata.get("overall_performance") or {}
    submitted = int(perf.get("total_submitted_instances") or 0)
    resolved = int(perf.get("total_resolved_instances") or 0)
    if submitted <= 0:
        continue
    score = perf.get("accuracy_score")
    score = float(score) if score is not None else resolved / submitted
    candidate = (score, resolved, submitted, entry)
    if best is None or candidate > best:
        best = candidate

if best is None:
    raise SystemExit(f"No evaluated nodes found under {output_dir}")
print(best[3])
PY
)"
    echo "Selected EVAL_NODE_ID=${EVAL_NODE_ID}"
fi

if [ "${EVAL_DRY_RUN:-0}" = "1" ]; then
    echo "EVAL_DRY_RUN=1 - listing remaining tasks only."
    "${PYTHON_BIN}" scripts/eval_node_on_remaining_tasks.py \
        --node-id "${EVAL_NODE_ID}" \
        --hgm-output-dir "${HGM_OUTPUT_DIR}" \
        --dry-run
    exit 0
fi

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.85}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-262144}"

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="$(hostname -i 2>/dev/null | awk '{print $1}' || true)"
fi
if [ -z "${HOST_IP}" ]; then
    HOST_IP="127.0.0.1"
fi
export VLLM_HOST="${VLLM_HOST:-$HOST_IP}"
# VLLM_CONTAINER_HOST is set by apptainer_runtime.inc.sh (node IP unless host network on).
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
    pkill -9 -P "${pid}" 2>/dev/null || true
    kill -9 "${pid}" 2>/dev/null || true
done
sleep 5

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
vllm_pre_start_cleanup

echo "Pre-pulling Apptainer images (timeout ${PRE_PULL_TIMEOUT:-7200}s)..."
timeout "${PRE_PULL_TIMEOUT:-7200}" "${PYTHON_BIN}" -u scripts/pull_epoch_images.py all || {
    echo "WARNING: Image pre-pull incomplete. Missing images will be pulled per task."
}

pkill -u "$(id -un)" -f "VLLM::" 2>/dev/null || true
pkill -u "$(id -un)" -f "vllm.v1" 2>/dev/null || true
sleep 3

wait_gpu_memory_clear() {
    local max_wait="${1:-120}"
    local waited=0
    local total_used=0
    while [ "${waited}" -lt "${max_wait}" ]; do
        if ! command -v nvidia-smi >/dev/null 2>&1; then
            return 0
        fi
        total_used="$(nvidia-smi --query-gpu=memory.used --format=csv,noheader,nounits 2>/dev/null | awk '{s+=$1} END {print s+0}')"
        if [ "${total_used:-0}" -eq 0 ]; then
            echo "All GPUs report 0 MiB used before vLLM start."
            return 0
        fi
        echo "Waiting for GPU memory to clear (${total_used} MiB total used, ${waited}s/${max_wait}s)..."
        pkill -u "$(id -un)" -f "VLLM::" 2>/dev/null || true
        pkill -u "$(id -un)" -f "vllm\.entrypoints\.openai\.api_server" 2>/dev/null || true
        sleep 5
        waited=$((waited + 5))
    done
    echo "ERROR: GPUs still have ${total_used} MiB allocated after ${max_wait}s" >&2
    nvidia-smi 2>/dev/null || true
    return 1
}
wait_gpu_memory_clear "${VLLM_GPU_CLEAR_WAIT:-120}"

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
    --gdn-prefill-backend triton \
    --enable-auto-tool-choice \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    --dtype auto \
    --trust-remote-code \
    > "logs/vllm_eval_${SLURM_JOB_ID}.stdout.log" 2> "logs/vllm_eval_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT="${VLLM_MAX_WAIT:-900}"
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process died. Check logs/vllm_eval_${SLURM_JOB_ID}.stderr.log" >&2
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

echo "Verifying vLLM reachable from Apptainer (host_net=${APPTAINER_USE_HOST_NETWORK}, vllm_host=${VLLM_CONTAINER_HOST})..."
VERIFY_SIF="${APPTAINER_IMAGE_DIR}/sweb.eval.x86_64.django__django-10973_latest.sif"
if [ ! -f "${VERIFY_SIF}" ]; then
    VERIFY_SIF="$(ls "${APPTAINER_IMAGE_DIR}"/sweb.eval.x86_64.django__django-10973*.sif 2>/dev/null | head -1 || true)"
fi
if [ -z "${VERIFY_SIF}" ] || [ ! -f "${VERIFY_SIF}" ]; then
    echo "ERROR: Cannot find django-10973 SIF under ${APPTAINER_IMAGE_DIR}" >&2
    exit 1
fi
NET_ARGS=()
if [ "${APPTAINER_USE_HOST_NETWORK}" = "1" ]; then
    NET_ARGS=(--net --network host)
fi
if ! apptainer exec "${NET_ARGS[@]}" "${VERIFY_SIF}" \
    curl -sf --connect-timeout 5 "http://${VLLM_CONTAINER_HOST}:${VLLM_PORT}/v1/models" | head -c 200; then
    echo "ERROR: Apptainer container cannot reach vLLM at ${VLLM_CONTAINER_HOST}:${VLLM_PORT}" >&2
    exit 1
fi
echo ""
echo "Apptainer -> vLLM connectivity OK."

export EVAL_MAX_WORKERS="${EVAL_MAX_WORKERS:-4}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-7200}"
export APPTAINER_API_TIMEOUT="${APPTAINER_API_TIMEOUT:-7200}"
export EVAL_RESUME_PRED_SUBDIR="${EVAL_RESUME_PRED_SUBDIR:-}"

echo "Starting eval_node_on_remaining_tasks for node=${EVAL_NODE_ID}"
echo "  HGM_OUTPUT_DIR=${HGM_OUTPUT_DIR}  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}"
echo "  EVAL_TIMEOUT=${EVAL_TIMEOUT}  SWE_EVAL_DATASET=${SWE_EVAL_DATASET}"
echo "  EVAL_RESUME_PRED_SUBDIR=${EVAL_RESUME_PRED_SUBDIR:-<none>}  TMPDIR=${TMPDIR}"

# shellcheck disable=SC2086
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" scripts/eval_node_on_remaining_tasks.py \
    --node-id "${EVAL_NODE_ID}" \
    --hgm-output-dir "${HGM_OUTPUT_DIR}" \
    --llm "${HGM_LLM_MODEL_ID}" \
    --max-workers "${EVAL_MAX_WORKERS}" \
    --eval-timeout "${EVAL_TIMEOUT}" \
    ${EVAL_EXTRA_ARGS:-}

echo "eval_remaining.slurm finished."
