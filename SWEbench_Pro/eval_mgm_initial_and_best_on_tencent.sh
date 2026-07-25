#!/bin/bash
# Evaluate the MGM initial agent and current best MGM node on SWE-bench Pro.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

PYTHON_BIN="${PYTHON_BIN:-python}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/swebench-pro-docker.sock}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-Qwen/Qwen3-Coder-Next}"

if [ ! -d "${SCRIPT_DIR}/upstream/run_scripts" ]; then
    "${SCRIPT_DIR}/sync_official_eval_assets.sh"
fi

COMMON_ARGS=(
    --remote-host "${REMOTE_DOCKER_HOST}"
    --remote-user "${REMOTE_DOCKER_USER}"
    --remote-socket "${REMOTE_DOCKER_SOCKET}"
    --llm "${HGM_LLM_MODEL_ID}"
)

"${PYTHON_BIN}" -u SWEbench_Pro/run_agent_eval.py \
    --agent initial \
    --output-dir SWEbench_Pro/outputs/mgm_initial \
    "${COMMON_ARGS[@]}" \
    "$@"

"${PYTHON_BIN}" -u SWEbench_Pro/run_agent_eval.py \
    --agent best \
    --hgm-output-dir "${HGM_OUTPUT_DIR:-output_mgm}" \
    --output-dir SWEbench_Pro/outputs/mgm_best \
    "${COMMON_ARGS[@]}" \
    "$@"

