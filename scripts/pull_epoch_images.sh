#!/usr/bin/env bash
# Deprecated wrapper — use Python Apptainer pull script instead.
set -euo pipefail
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/.." && pwd)"
cd "${REPO_ROOT}"
eval "$(conda shell.bash hook)" 2>/dev/null || true
conda activate HGM 2>/dev/null || true
# shellcheck source=/dev/null
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
exec python -u scripts/pull_epoch_images.py "${1:-all}"
