#!/usr/bin/env bash
# Apptainer-based cleanup / prep before a local MGM run (replaces docker system prune).
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${REPO_ROOT}"

# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"

echo "Removing stale SWE-bench Apptainer instance images..."
bash scripts/cleanup_old_images.sh

eval "$(conda shell.bash hook)"
conda activate HGM 2>/dev/null || conda activate agent 2>/dev/null || true

export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" hgm.py
