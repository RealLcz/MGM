#!/bin/bash
#SBATCH --job-name=HGM-SWE-INIT
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=64
#SBATCH --mem=512G
#SBATCH --gres=gpu:8
#SBATCH --exclusive
#SBATCH --time=96:00:00
#SBATCH --output=logs/eval_swe_initial_%j.out
#SBATCH --error=logs/eval_swe_initial_%j.err
#
# Evaluate the SWE-bench initial agent (small.json + medium.json = 60 tasks).
#
# Submit:
#   sbatch swe_scripts/eval_initial_agent.slurm
#
#   EVAL_MAX_WORKERS=20 sbatch swe_scripts/eval_initial_agent.slurm
#
# Dry-run (list tasks only):
#   EVAL_DRY_RUN=1 sbatch swe_scripts/eval_initial_agent.slurm
#
# Env (optional):
#   VLLM_MODEL_NAME       - default Qwen/Qwen3.6-35B-A3B
#   INITIAL_AGENT_PATH    - default initial_swe/default_agent/src
#   HGM_OUTPUT_DIR        - default output_swe/initial_agent_qwen3_6_35b_a3b_<timestamp>
#   EVAL_MAX_WORKERS      - parallel harness workers (default: auto from GPU count, 8 GPU -> 8)
#   EVAL_RESUME_PRED_SUBDIR - resume into existing predictions/<name> (skip tasks with .json)
#   APPTAINER_USE_HOST_NETWORK - default 0 (HPC forbids host net; use node IP for vLLM)
#   VLLM_CONTAINER_HOST       - default node IP when host net off, else 127.0.0.1
#   EVAL_TIMEOUT          - per-task seconds (default 3600)
#   APPTAINER_IMAGE_DIR   - default ${REPO_ROOT}/apptainer_images

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
export APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${REPO_ROOT}/apptainer_images}"
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify

export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"
export INITIAL_AGENT_PATH="${INITIAL_AGENT_PATH:-initial_swe/default_agent/src}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-3600}"

if [ -z "${HGM_OUTPUT_DIR:-}" ]; then
    HGM_OUTPUT_DIR="output_swe/initial_agent_qwen3_6_35b_a3b_$(date +%Y%m%d_%H%M%S)"
fi
export HGM_OUTPUT_DIR
export EVAL_MODEL_NAME="${EVAL_MODEL_NAME:-initial_agent_Qwen3.6-35B-A3B}"

if [ "${EVAL_DRY_RUN:-0}" = "1" ]; then
    echo "EVAL_DRY_RUN=1 - listing SWE-bench tasks only."
    "${PYTHON_BIN}" - <<'PY'
import json

small = json.load(open("swe_bench/subsets/small.json"))
medium = json.load(open("swe_bench/subsets/medium.json"))
task_ids = list(dict.fromkeys(small + medium))
print(f"tasks={len(task_ids)}")
for task_id in task_ids:
    print(task_id)
PY
    exit 0
fi

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup

# Workers are Apptainer/API clients; all GPUs serve one vLLM (TP=N). Bottleneck is
# vLLM batching + host CPU/RAM, not GPU-per-worker. validation_20_worker.slurm
# stress-tested 20 workers on 8x H100; APPTAINER.md suggests cpus >= workers * 2.
NUM_VISIBLE_GPUS="$(echo "${CUDA_VISIBLE_DEVICES}" | tr ',' '\n' | grep -c '.')"
if [ -z "${EVAL_MAX_WORKERS:-}" ]; then
    case "${NUM_VISIBLE_GPUS}" in
        8) EVAL_MAX_WORKERS=8 ;;
        4) EVAL_MAX_WORKERS=8 ;;
        2) EVAL_MAX_WORKERS=4 ;;
        *) EVAL_MAX_WORKERS=2 ;;
    esac
fi
export EVAL_MAX_WORKERS

export VLLM_PORT="${VLLM_PORT:-8000}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.90}"
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
    > "logs/vllm_swe_initial_${SLURM_JOB_ID}.stdout.log" \
    2> "logs/vllm_swe_initial_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT=600
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process died. Check logs/vllm_swe_initial_${SLURM_JOB_ID}.stderr.log" >&2
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
    echo "  With host network disabled, set VLLM_CONTAINER_HOST to this node's IP (hostname -I)." >&2
    echo "  Or if your site allows it: APPTAINER_USE_HOST_NETWORK=1 VLLM_CONTAINER_HOST=127.0.0.1" >&2
    exit 1
fi
echo ""
echo "Apptainer -> vLLM connectivity OK."

echo "Verifying vLLM inference (chat/completions smoke test)..."
if ! "${PYTHON_BIN}" -c "
import json, os, urllib.request
host = os.environ.get('VLLM_HOST', '127.0.0.1')
port = os.environ.get('VLLM_PORT', '8000')
model = os.environ.get('SERVED_MODEL_NAME', 'Qwen/Qwen3.6-35B-A3B')
payload = json.dumps({
    'model': model,
    'messages': [{'role': 'user', 'content': 'Reply with exactly: ok'}],
    'max_tokens': 8,
    'temperature': 0,
}).encode()
req = urllib.request.Request(
    f'http://{host}:{port}/v1/chat/completions',
    data=payload,
    headers={'Content-Type': 'application/json'},
    method='POST',
)
with urllib.request.urlopen(req, timeout=120) as resp:
    body = json.loads(resp.read().decode())
print('vLLM inference OK:', body['choices'][0]['message']['content'][:80])
"; then
    echo "ERROR: vLLM chat/completions failed (check disk quota / flashinfer cache)" >&2
    exit 1
fi

mkdir -p "${HGM_OUTPUT_DIR}" "${HGM_OUTPUT_DIR}/predictions"

echo "Starting SWE-bench initial-agent evaluation"
echo "  output_dir=${HGM_OUTPUT_DIR}"
echo "  initial_agent_path=${INITIAL_AGENT_PATH}"
echo "  model=${HGM_LLM_MODEL_ID}"
echo "  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}  EVAL_TIMEOUT=${EVAL_TIMEOUT}"
echo "  EVAL_RESUME_PRED_SUBDIR=${EVAL_RESUME_PRED_SUBDIR:-<none>}"
echo "  APPTAINER_USE_HOST_NETWORK=${APPTAINER_USE_HOST_NETWORK}  VLLM_CONTAINER_HOST=${VLLM_CONTAINER_HOST}  VLLM_PORT=${VLLM_PORT}"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" - <<'PY'
import json
import os
from pathlib import Path

import swe_bench.harness as swe_harness
from swe_bench.harness import harness

small = json.load(open("swe_bench/subsets/small.json"))
medium = json.load(open("swe_bench/subsets/medium.json"))
task_ids = list(dict.fromkeys(small + medium))

init_agent_path = os.environ["INITIAL_AGENT_PATH"]
if not os.path.isdir(init_agent_path):
    raise SystemExit(f"INITIAL_AGENT_PATH does not exist: {init_agent_path}")

out_dir = Path(os.environ["HGM_OUTPUT_DIR"])
pred_dir = out_dir / "predictions"
out_dir.mkdir(parents=True, exist_ok=True)
pred_dir.mkdir(parents=True, exist_ok=True)

swe_harness.llm = os.environ["HGM_LLM_MODEL_ID"]
swe_harness.timeout = int(os.environ["EVAL_TIMEOUT"])

print(f"Submitting {len(task_ids)} SWE-bench tasks.")
out_dirs = harness(
    test_task_list=task_ids,
    max_workers=min(int(os.environ["EVAL_MAX_WORKERS"]), max(1, len(task_ids))),
    model_name_or_path=os.environ["EVAL_MODEL_NAME"],
    pred_dname=str(pred_dir),
    init_agent_path=init_agent_path,
)
print("Harness output dirs:", out_dirs)

from swe_bench.report import make_report

for out_dname in out_dirs:
    print(f"Running make_report on {out_dname} ...")
    make_report(
        dnames=[str(out_dname)],
        run_ids=[os.environ["EVAL_MODEL_NAME"]],
        dataset_name="princeton-nlp/SWE-bench",
        output_dir=str(out_dir),
        num_eval_procs=min(int(os.environ["EVAL_MAX_WORKERS"]), 10),
    )
    report_path = out_dir / f"{os.environ['EVAL_MODEL_NAME']}_report.json"
    if report_path.exists():
        report = json.loads(report_path.read_text())
        resolved = report.get("resolved", [])
        print(f"Report: {len(resolved)}/{len(task_ids)} resolved")
PY

echo "eval_initial_agent.slurm finished."
