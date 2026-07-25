#!/bin/bash
# Fetch the official SWE-bench Pro evaluation assets under SWEbench_Pro/upstream.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
UPSTREAM_DIR="${SWE_PRO_UPSTREAM_DIR:-${SCRIPT_DIR}/upstream}"
REPO_URL="${SWE_PRO_REPO_URL:-https://github.com/scaleapi/SWE-bench_Pro-os.git}"

if [ -d "${UPSTREAM_DIR}/.git" ]; then
    echo "Updating ${UPSTREAM_DIR}"
    git -C "${UPSTREAM_DIR}" pull --ff-only
else
    echo "Cloning ${REPO_URL} -> ${UPSTREAM_DIR}"
    git clone --depth 1 "${REPO_URL}" "${UPSTREAM_DIR}"
fi

PYTHON_BIN="${PYTHON_BIN:-python}"
"${PYTHON_BIN}" -u "${SCRIPT_DIR}/make_eval_inputs.py" \
    --subset "${SWE_PRO_SUBSET:-${SCRIPT_DIR}/subsets/test.json}" \
    --output-dir "${SWE_PRO_DATA_DIR:-${SCRIPT_DIR}/data}"

echo "Official assets ready:"
echo "  upstream: ${UPSTREAM_DIR}"
echo "  data:     ${SWE_PRO_DATA_DIR:-${SCRIPT_DIR}/data}"

