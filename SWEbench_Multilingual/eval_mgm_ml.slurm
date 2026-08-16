#!/bin/bash
#SBATCH --job-name=SWE-ML-EVAL
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=512G
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --time=96:00:00
#SBATCH --output=SWEbench_Multilingual/logs/eval_mgm_ml_%j.out
#SBATCH --error=SWEbench_Multilingual/logs/eval_mgm_ml_%j.err
#
# Evaluate a Polyglot HGM/MGM node on the held-out SWE-bench Multilingual subset
# using local Apptainer (same-node vLLM; no remote Docker).
#
# Example:
#   sbatch --export=ALL,SWE_ML_NODE_PATH=/path/to/node,SWE_ML_MAX_WORKERS=2 \
#     SWEbench_Multilingual/eval_mgm_ml.slurm
#
# Env:
#   SWE_ML_AGENT_MODE     initial | node (default node)
#   SWE_ML_NODE_PATH        required when SWE_ML_AGENT_MODE=node
#   SWE_ML_SUBSET           default SWEbench_Multilingual/subsets/heldout_60.json
#   SWE_ML_OUTPUT_DIR       output directory
#   SWE_ML_MAX_WORKERS      parallel workers (default 10)
#   SWE_ML_INIT_AGENT_SRC   default initial_polyglot/default_agent/src
#   APPTAINER_IMAGE_DIR     default /mnt/vast/home/ym56kacy/jinhe/MendelGM/apptainer_images
#   APPTAINER_USE_HOST_NETWORK  default 0 (use node IP for vLLM)
#   VLLM_MODEL_NAME         default Qwen/Qwen3.6-35B-A3B

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p SWEbench_Multilingual/logs logs

VLLM_PID=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null && sleep 1 && kill -9 "${VLLM_PID}" 2>/dev/null || true
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate HGM
REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-${VLLM_MODEL_NAME}}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"
export VLLM_PORT="${VLLM_PORT:-8000}"
export VLLM_PRELOAD_HF_ASSETS="${VLLM_PRELOAD_HF_ASSETS:-auto}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-112000}"
export SWE_ML_AGENT_PIP_INDEX_URL="${SWE_ML_AGENT_PIP_INDEX_URL:-https://mirrors.cloud.tencent.com/pypi/simple}"
export SWE_ML_AGENT_PIP_CONSTRAINTS="${SWE_ML_AGENT_PIP_CONSTRAINTS:-SWEbench_Pro/agent_constraints.txt}"

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

HOST_IP="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="$(hostname -i 2>/dev/null | awk '{print $1}' || true)"
fi
if [ -z "${HOST_IP}" ]; then
    HOST_IP="127.0.0.1"
fi
export VLLM_HOST="${VLLM_HOST:-$HOST_IP}"

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
vllm_pre_start_cleanup

if [ "${SWE_ML_PREPULL:-1}" = "1" ]; then
    echo "Pre-pulling SWE-bench Multilingual Apptainer images (timeout ${PRE_PULL_TIMEOUT:-7200}s)..."
    timeout "${PRE_PULL_TIMEOUT:-7200}" "${PYTHON_BIN}" -u SWEbench_Multilingual/scripts/pull_images.py \
        "${SWE_ML_SUBSET:-SWEbench_Multilingual/subsets/heldout_60.json}" || {
        echo "WARNING: Multilingual image pre-pull incomplete. Missing images will be pulled per task."
    }
fi

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
    > "SWEbench_Multilingual/logs/vllm_ml_${SLURM_JOB_ID}.stdout.log" \
    2> "SWEbench_Multilingual/logs/vllm_ml_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT="${VLLM_MAX_WAIT:-1200}"
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process died. Check SWEbench_Multilingual/logs/vllm_ml_${SLURM_JOB_ID}.stderr.log" >&2
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
VERIFY_SIF="$(ls "${APPTAINER_IMAGE_DIR}"/swebench_sweb.eval.*_latest.sif 2>/dev/null | head -1 || true)"
if [ -z "${VERIFY_SIF}" ]; then
    VERIFY_SIF="$(ls "${APPTAINER_IMAGE_DIR}"/sweb.eval.x86_64.*_latest.sif 2>/dev/null | head -1 || true)"
fi
if [ -z "${VERIFY_SIF}" ] || [ ! -f "${VERIFY_SIF}" ]; then
    echo "ERROR: No sweb.eval Apptainer images under ${APPTAINER_IMAGE_DIR}. Run pull_images.py first." >&2
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

SWE_ML_AGENT_MODE="${SWE_ML_AGENT_MODE:-node}"
SWE_ML_INIT_AGENT_SRC="${SWE_ML_INIT_AGENT_SRC:-initial_polyglot/default_agent/src}"
if [ ! -f "${SWE_ML_INIT_AGENT_SRC}/coding_agent_polyglot.py" ]; then
    echo "ERROR: polyglot initial agent not found at ${SWE_ML_INIT_AGENT_SRC}/coding_agent_polyglot.py" >&2
    exit 1
fi

if [ "${SWE_ML_AGENT_MODE}" = "node" ] && [ -z "${SWE_ML_NODE_PATH:-}" ] && [ -z "${SWE_ML_NODE_ID:-}" ]; then
    echo "ERROR: set SWE_ML_NODE_PATH or SWE_ML_NODE_ID when SWE_ML_AGENT_MODE=node" >&2
    exit 1
fi

SWE_ML_SUBSET="${SWE_ML_SUBSET:-SWEbench_Multilingual/subsets/heldout_60.json}"
SWE_ML_MAX_WORKERS="${SWE_ML_MAX_WORKERS:-10}"
SWE_ML_AGENT_TIMEOUT="${SWE_ML_AGENT_TIMEOUT:-3600}"
SWE_ML_EVAL_TIMEOUT="${SWE_ML_EVAL_TIMEOUT:-7200}"
SWE_ML_PULL="${SWE_ML_PULL:-missing}"

COMMON_ARGS=(
    --agent "${SWE_ML_AGENT_MODE}"
    --subset "${SWE_ML_SUBSET}"
    --init-agent-src "${SWE_ML_INIT_AGENT_SRC}"
    --llm "${HGM_LLM_MODEL_ID}"
    --max-workers "${SWE_ML_MAX_WORKERS}"
    --agent-timeout "${SWE_ML_AGENT_TIMEOUT}"
    --eval-timeout "${SWE_ML_EVAL_TIMEOUT}"
    --container-timeout "${APPTAINER_API_TIMEOUT:-7200}"
    --agent-pip-index-url "${SWE_ML_AGENT_PIP_INDEX_URL}"
    --agent-pip-constraints "${SWE_ML_AGENT_PIP_CONSTRAINTS}"
    --pull "${SWE_ML_PULL}"
)

if [ "${SWE_ML_AGENT_MODE}" = "node" ]; then
    if [ -n "${SWE_ML_NODE_PATH:-}" ]; then
        NODE_BASENAME="$(basename "${SWE_ML_NODE_PATH}")"
        EVAL_OUTPUT_DIR="${SWE_ML_OUTPUT_DIR:-SWEbench_Multilingual/outputs/node_${NODE_BASENAME}}"
        COMMON_ARGS+=(--node-path "${SWE_ML_NODE_PATH}")
    else
        EVAL_OUTPUT_DIR="${SWE_ML_OUTPUT_DIR:-SWEbench_Multilingual/outputs/node_${SWE_ML_NODE_ID}}"
        COMMON_ARGS+=(--node-id "${SWE_ML_NODE_ID}" --hgm-output-dir "${HGM_OUTPUT_DIR:-output_polyglot}")
    fi
else
    EVAL_OUTPUT_DIR="${SWE_ML_OUTPUT_DIR:-SWEbench_Multilingual/outputs/polyglot_initial_qwen3_6_35b_a3b}"
fi

if [ -n "${SWE_ML_LIMIT:-}" ]; then
    COMMON_ARGS+=(--limit "${SWE_ML_LIMIT}")
fi
if [ "${SWE_ML_REDO:-0}" = "1" ]; then
    COMMON_ARGS+=(--redo)
fi

echo "Evaluating SWE-bench Multilingual held-out subset (Apptainer)..."
echo "  agent_mode=${SWE_ML_AGENT_MODE}"
echo "  init_agent_src=${SWE_ML_INIT_AGENT_SRC}"
echo "  subset=${SWE_ML_SUBSET}"
echo "  output=${EVAL_OUTPUT_DIR}"
echo "  workers=${SWE_ML_MAX_WORKERS}"
echo "  apptainer_image_dir=${APPTAINER_IMAGE_DIR}"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" -u SWEbench_Multilingual/run_agent_eval.py \
    --output-dir "${EVAL_OUTPUT_DIR}" \
    "${COMMON_ARGS[@]}"

echo "eval_mgm_ml.slurm finished."
