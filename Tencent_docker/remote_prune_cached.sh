#!/usr/bin/env bash
# Delete remote Docker images for instances already completed.
# Uses IMAGE_MAP and OUTPUT_DIR to determine which cached images can be removed.

set -euo pipefail

IMAGE_MAP="${IMAGE_MAP:-SWEbench_Pro/data/image_map.json}"
OUTPUT_DIR="${OUTPUT_DIR:-SWEbench_Pro/outputs/polyglot_initial_qwen3_6_35b_a3b}"
REMOTE_HOST="${REMOTE_DOCKER_HOST:-43.157.32.135}"
REMOTE_USER="${REMOTE_DOCKER_USER:-ubuntu}"
DRY_RUN="${DRY_RUN:-0}"

if [ -z "${REMOTE_DOCKER_PASSWORD:-}" ] && [ "${DRY_RUN}" != "1" ]; then
    echo "ERROR: REMOTE_DOCKER_PASSWORD env var required (or set DRY_RUN=1)." >&2
    exit 2
fi

TAGS_FILE="$(mktemp)"
trap 'rm -f "${TAGS_FILE}"' EXIT

python3 - "${IMAGE_MAP}" "${OUTPUT_DIR}" >"${TAGS_FILE}" <<'PY'
import json, sys, os, glob
image_map_path, output_dir = sys.argv[1], sys.argv[2]
raw = json.load(open(image_map_path))
cached = set()
for d in glob.glob(os.path.join(output_dir, "instance_*/")):
    inst = os.path.basename(d.rstrip("/\n")).removeprefix("instance_")
    if os.path.exists(os.path.join(d, "summary.json")):
        cached.add(inst)
seen = set()
for r in raw:
    iid = r["instance_id"].removeprefix("instance_") if r["instance_id"].startswith("instance_") else r["instance_id"]
    if iid in cached:
        for key in ("source_image", "local_alias"):
            tag = r.get(key)
            if tag and tag not in seen:
                seen.add(tag); print(tag)
PY

n_tags=$(wc -l < "${TAGS_FILE}")
echo "Tags marked for removal: ${n_tags}"
if [ "${n_tags}" -eq 0 ]; then
    echo "Nothing to do."
    exit 0
fi

if [ "${DRY_RUN}" = "1" ]; then
    echo "--- DRY_RUN: would delete ---"
    cat "${TAGS_FILE}"
    exit 0
fi

ASKPASS="$(mktemp)"; chmod 700 "${ASKPASS}"
cat >"${ASKPASS}" <<'EOF'
#!/bin/sh
printf '%s\n' "${REMOTE_DOCKER_PASSWORD}"
EOF
trap 'rm -f "${TAGS_FILE}" "${ASKPASS}"' EXIT

remote_cmd='set +e; freed_before=$(df -BG --output=avail /var/lib/docker | tail -1 | tr -dc 0-9); n_ok=0; n_skip=0;
while IFS= read -r tag; do
    [ -z "$tag" ] && continue
    if docker image inspect "$tag" >/dev/null 2>&1; then
        if docker rmi --force "$tag" >/dev/null 2>&1; then
            n_ok=$((n_ok+1))
        else
            echo "  FAIL to remove: $tag" >&2
        fi
    else
        n_skip=$((n_skip+1))
    fi
done
freed_after=$(df -BG --output=avail /var/lib/docker | tail -1 | tr -dc 0-9)
echo "removed=$n_ok skipped=$n_skip avail_before=${freed_before}G avail_after=${freed_after}G freed=$((freed_after-freed_before))G"
docker system df'

DISPLAY=hgm:0 SSH_ASKPASS_REQUIRE=force SSH_ASKPASS="${ASKPASS}" \
setsid -w ssh -o StrictHostKeyChecking=no -o NumberOfPasswordPrompts=1 \
    "${REMOTE_USER}@${REMOTE_HOST}" "${remote_cmd}" <"${TAGS_FILE}"
