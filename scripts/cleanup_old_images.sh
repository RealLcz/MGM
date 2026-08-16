#!/usr/bin/env bash
# Remove old SWE-bench Apptainer instance .sif files from the local cache.

set -euo pipefail

IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${HF_HOME:-$HOME}/apptainer_images}"

echo "=== Cleaning up old SWE-bench Apptainer images in ${IMAGE_DIR} ==="
if [ -d "${IMAGE_DIR}" ]; then
    find "${IMAGE_DIR}" -maxdepth 1 -name 'sweb.eval.*.sif' -delete 2>/dev/null || true
    find "${IMAGE_DIR}" -maxdepth 1 -name 'sweb.eval.*.json' -delete 2>/dev/null || true
fi
echo "Done."
