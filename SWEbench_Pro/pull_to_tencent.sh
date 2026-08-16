#!/bin/bash
# Pull the SWE-bench Pro subset images into the Tencent Cloud Docker daemon.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"

export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/swebench-pro-docker.sock}"
PYTHON_BIN="${PYTHON_BIN:-python}"

"${PYTHON_BIN}" -u SWEbench_Pro/pull_images.py \
    --subset "${SWE_PRO_SUBSET:-SWEbench_Pro/subsets/test.json}" \
    --remote-host "${REMOTE_DOCKER_HOST}" \
    --remote-user "${REMOTE_DOCKER_USER}" \
    --remote-socket "${REMOTE_DOCKER_SOCKET}" \
    "$@"

