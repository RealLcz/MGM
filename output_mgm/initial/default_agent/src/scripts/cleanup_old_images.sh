#!/bin/bash
# Clean up old locally-built SWE-bench images on the Tencent Cloud VM.
# Run this BEFORE pull_epoch_images.sh to free disk space.
#
# This removes:
# - Old instance images (sweb.eval.*)
# - Old env images (sweb.env.*)
# - Old base images (sweb.base.*)
# - Dangling (untagged) images
#
# Usage: bash cleanup_old_images.sh

set -euo pipefail

echo "=== Cleaning up old SWE-bench Docker images ==="

echo "Removing old instance images (sweb.eval.*)..."
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^sweb\.eval\.' | xargs -r docker rmi -f 2>/dev/null || true

echo "Removing old env images (sweb.env.*)..."
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^sweb\.env\.' | xargs -r docker rmi -f 2>/dev/null || true

echo "Removing old base images (sweb.base.*)..."
docker images --format '{{.Repository}}:{{.Tag}}' | grep '^sweb\.base\.' | xargs -r docker rmi -f 2>/dev/null || true

echo "Removing dangling images..."
docker image prune -f 2>/dev/null || true

echo "Removing stopped containers..."
docker container prune -f 2>/dev/null || true

echo ""
echo "=== Current disk usage ==="
df -h / 2>/dev/null || true
echo ""
docker system df 2>/dev/null || true
echo ""
echo "Done."
