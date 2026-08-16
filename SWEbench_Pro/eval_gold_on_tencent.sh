#!/bin/bash
# Run a SWE-bench Pro gold-patch evaluation through the Tencent Cloud Docker daemon.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

if [ ! -d "${SCRIPT_DIR}/upstream/run_scripts" ]; then
    "${SCRIPT_DIR}/sync_official_eval_assets.sh"
fi

if [ ! -f "${SCRIPT_DIR}/data/gold_patches.json" ]; then
    PYTHON_BIN="${PYTHON_BIN:-python}" "${PYTHON_BIN}" -u "${SCRIPT_DIR}/make_eval_inputs.py"
fi

export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/swebench-pro-docker.sock}"
PYTHON_BIN="${PYTHON_BIN:-python}"

"${PYTHON_BIN}" -u SWEbench_Pro/evaluate_patches_remote.py \
    --patch-path SWEbench_Pro/data/gold_patches.json \
    --output-dir SWEbench_Pro/outputs/gold_eval \
    --remote-host "${REMOTE_DOCKER_HOST}" \
    --remote-user "${REMOTE_DOCKER_USER}" \
    --remote-socket "${REMOTE_DOCKER_SOCKET}" \
    "$@"

