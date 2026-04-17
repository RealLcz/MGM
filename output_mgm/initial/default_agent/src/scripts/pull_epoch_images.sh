#!/bin/bash
# Pull pre-built SWE-bench instance images from Epoch AI registry
# and retag them to match the local naming convention.
#
# Run this script DIRECTLY on the Tencent Cloud VM (where docker CLI is available).
#
# Usage:
#   bash pull_epoch_images.sh small      # 10 images (~1 GB)
#   bash pull_epoch_images.sh medium     # 50 images (~5 GB)
#   bash pull_epoch_images.sh all        # small+medium = 60 images
#   bash pull_epoch_images.sh verified   # all 500 SWE-bench Verified (~30 GB)
#
# Uses DaoCloud mirror (m.daocloud.io) to bypass China network issues with ghcr.io.
# Set GHCR_MIRROR=ghcr.io to pull directly without mirror.
#
# Images are persistent — pull once, reuse forever.

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
MAX_RETRIES=3
SUBSET="${1:-all}"

# Mirror selection: set GHCR_MIRROR env var to override
# Options: milu, nju, huawei, direct (no mirror)
MIRROR_NAME="${GHCR_MIRROR:-milu}"
case "${MIRROR_NAME}" in
    milu)    REGISTRY="ghcr.milu.moe/epoch-research" ;;
    nju)     REGISTRY="ghcr.nju.edu.cn/epoch-research" ;;
    huawei)  REGISTRY="swr.cn-north-4.myhuaweicloud.com/ddn-k8s/ghcr.io/epoch-research" ;;
    direct)  REGISTRY="ghcr.io/epoch-research" ;;
    *)       REGISTRY="${MIRROR_NAME}/epoch-research" ;;
esac

get_ids() {
    local subset="$1"
    case "${subset}" in
        small)
            echo "django__django-10973
django__django-11066
django__django-12754
django__django-15930
django__django-13279
django__django-16661
django__django-13346
django__django-10880
django__django-10999
django__django-11087"
            ;;
        medium)
            echo "django__django-11790
django__django-12050
django__django-12262
django__django-12713
sphinx-doc__sphinx-8475
sphinx-doc__sphinx-8721
sphinx-doc__sphinx-9320
sphinx-doc__sphinx-9698
django__django-11848
django__django-12774
sphinx-doc__sphinx-7748
sphinx-doc__sphinx-8035
sphinx-doc__sphinx-8548
sphinx-doc__sphinx-9229
django__django-11880
django__django-12143
django__django-12155
sphinx-doc__sphinx-9367
django__django-12209
django__django-11951
django__django-12193
django__django-12276
django__django-12304
django__django-9296
sphinx-doc__sphinx-10466
django__django-11999
django__django-12039
django__django-12273
django__django-12325
django__django-12406
sphinx-doc__sphinx-10673
sphinx-doc__sphinx-11510
sphinx-doc__sphinx-7757
sphinx-doc__sphinx-8265
sphinx-doc__sphinx-8551
sphinx-doc__sphinx-8638
django__django-11815
django__django-11885
django__django-12708
sphinx-doc__sphinx-7590
sphinx-doc__sphinx-7985
sphinx-doc__sphinx-8056
sphinx-doc__sphinx-9461
django__django-11964
django__django-12308
sphinx-doc__sphinx-10449
sphinx-doc__sphinx-7454
sphinx-doc__sphinx-8269
sphinx-doc__sphinx-9230
sphinx-doc__sphinx-9281"
            ;;
        all)
            get_ids small
            get_ids medium
            ;;
        verified)
            cat "${SCRIPT_DIR}/verified_instance_ids.txt"
            ;;
        *)
            echo "Usage: $0 [small|medium|all|verified]" >&2
            exit 1
            ;;
    esac
}

mapfile -t IDS < <(get_ids "${SUBSET}" | sort -u)
TOTAL=${#IDS[@]}

echo "=== Pulling ${TOTAL} images from Epoch AI registry (subset: ${SUBSET}) ==="
echo "    Mirror: ${MIRROR_NAME} -> ${REGISTRY}"
echo "    Retries per image: ${MAX_RETRIES}"
echo ""

SUCCESS=0
FAIL=0
SKIP=0
FAILED_IDS=()

for i in "${!IDS[@]}"; do
    ID="${IDS[$i]}"
    [ -z "${ID}" ] && continue
    IDX=$((i + 1))
    ID_LOWER="$(echo "${ID}" | tr '[:upper:]' '[:lower:]')"

    REMOTE_IMAGE="${REGISTRY}/swe-bench.eval.x86_64.${ID_LOWER}:latest"
    LOCAL_IMAGE="sweb.eval.x86_64.${ID_LOWER}:latest"

    # Skip if local image already exists
    if docker image inspect "${LOCAL_IMAGE}" >/dev/null 2>&1; then
        echo "[${IDX}/${TOTAL}] SKIP  ${ID}"
        SKIP=$((SKIP + 1))
        continue
    fi

    PULLED=false
    for attempt in $(seq 1 ${MAX_RETRIES}); do
        if [ "${attempt}" -eq 1 ]; then
            echo -n "[${IDX}/${TOTAL}] PULL  ${ID} ... "
        else
            echo -n "[${IDX}/${TOTAL}] RETRY ${attempt}/${MAX_RETRIES}  ${ID} ... "
        fi

        if docker pull "${REMOTE_IMAGE}" 2>&1 | tail -1; then
            docker tag "${REMOTE_IMAGE}" "${LOCAL_IMAGE}" 2>/dev/null
            docker rmi "${REMOTE_IMAGE}" >/dev/null 2>&1 || true
            echo "  -> OK"
            PULLED=true
            break
        else
            echo "  -> FAILED (attempt ${attempt}/${MAX_RETRIES})"
            if [ "${attempt}" -lt "${MAX_RETRIES}" ]; then
                sleep 5
            fi
        fi
    done

    if ${PULLED}; then
        SUCCESS=$((SUCCESS + 1))
    else
        FAIL=$((FAIL + 1))
        FAILED_IDS+=("${ID}")
    fi
done

echo ""
echo "=== Done ==="
echo "  Pulled:  ${SUCCESS}"
echo "  Skipped: ${SKIP}"
echo "  Failed:  ${FAIL}"
echo "  Total:   ${TOTAL}"

if [ "${FAIL}" -gt 0 ]; then
    echo ""
    echo "Failed images:"
    for fid in "${FAILED_IDS[@]}"; do
        echo "  - ${fid}"
    done
    echo ""
    echo "Re-run the script to retry failed images."
    exit 1
fi
