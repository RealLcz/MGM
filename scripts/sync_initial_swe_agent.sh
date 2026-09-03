#!/bin/bash
# Sync repo-root coding agent sources into an agent src tree (harness copy set only).
#
# By default stages from git HEAD (committed versions) so evolution patches
# apply on the same base they were created against.  Working-tree modifications
# to llm.py / llm_withtools.py / etc. would otherwise leak into the eval base
# and corrupt the reconstructed evolved agent.
#
# Set INIT_AGENT_USE_WORKTREE=1 to stage from the working tree instead (debug).
#
# Run from MendelGM repo root:
#   bash scripts/sync_initial_swe_agent.sh
#   INIT_AGENT_DEST=/path/to/stage bash scripts/sync_initial_swe_agent.sh

set -euo pipefail
REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
DEST="${INIT_AGENT_DEST:-${REPO_ROOT}/initial_swe/default_agent/src}"
USE_WORKTREE="${INIT_AGENT_USE_WORKTREE:-0}"

mkdir -p "${DEST}"

# ---------------------------------------------------------------------------
# Helper: copy a single file from git HEAD (preferred) or working tree.
# ---------------------------------------------------------------------------
copy_agent_file() {
    local rel="$1"
    local src="${REPO_ROOT}/${rel}"
    local dst="${DEST}/${rel}"

    if [ "${USE_WORKTREE}" = "1" ]; then
        cp -f "${src}" "${dst}"
        return
    fi

    # Use git HEAD version if the file is tracked; fall back to working tree.
    if git -C "${REPO_ROOT}" cat-file -e "HEAD:${rel}" 2>/dev/null; then
        mkdir -p "$(dirname "${dst}")"
        git -C "${REPO_ROOT}" show "HEAD:${rel}" > "${dst}"
    elif [ -f "${src}" ]; then
        mkdir -p "$(dirname "${dst}")"
        cp -f "${src}" "${dst}"
    else
        echo "WARNING: ${rel} not found in git HEAD or working tree" >&2
    fi
}

# ---------------------------------------------------------------------------
# Helper: sync a directory from git HEAD (preferred) or working tree.
# Excludes are passed as additional arguments.
# ---------------------------------------------------------------------------
sync_agent_dir() {
    local rel_dir="$1"
    shift  # remaining args are exclude patterns (basename)
    local src_dir="${REPO_ROOT}/${rel_dir}"
    local dst_dir="${DEST}/${rel_dir}"

    if [ "${USE_WORKTREE}" = "1" ] || ! git -C "${REPO_ROOT}" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
        # Working-tree rsync with excludes
        local rsync_excludes=()
        for ex in "$@"; do
            rsync_excludes+=(--exclude "${ex}")
        done
        rsync -a --delete --exclude '__pycache__' --exclude '*.pyc' \
            "${rsync_excludes[@]}" "${src_dir}/" "${dst_dir}/"
        return
    fi

    # git HEAD version: list tracked files, apply excludes, checkout each.
    mkdir -p "${dst_dir}"
    rm -rf "${dst_dir}"
    mkdir -p "${dst_dir}"

    local excludes="$*"
    while IFS= read -r tracked_file; do
        # tracked_file is relative to repo root, e.g. "tools/foo.py"
        local basename
        basename="$(basename "${tracked_file}")"
        local skip=0
        for ex in ${excludes}; do
            if [ "${basename}" = "${ex}" ]; then
                skip=1
                break
            fi
        done
        [ "${skip}" = "1" ] && continue
        # Only sync files under rel_dir
        case "${tracked_file}" in
            "${rel_dir}"/*)
                mkdir -p "${dst_dir}/$(dirname "${tracked_file#${rel_dir}/}")"
                git -C "${REPO_ROOT}" show "HEAD:${tracked_file}" > "${dst_dir}/${tracked_file#${rel_dir}/}"
                ;;
        esac
    done < <(git -C "${REPO_ROOT}" ls-tree -r --name-only HEAD "${rel_dir}/" 2>/dev/null)
}

# ---------------------------------------------------------------------------
# Stage individual agent files
# ---------------------------------------------------------------------------
for f in coding_agent.py coding_agent_polyglot.py llm.py llm_withtools.py \
    config.py config.yaml tree.py requirements.txt pytest.ini LICENSE README.md; do
    copy_agent_file "${f}"
done

# ---------------------------------------------------------------------------
# Stage directories (from git HEAD, with excludes)
# ---------------------------------------------------------------------------
sync_agent_dir "tools"
sync_agent_dir "utils" "docker_utils.py" "evo_utils.py"
sync_agent_dir "prompts" "self_improvement_prompt.py" "diagnose_improvement_prompt.py"
sync_agent_dir "tests"

# ---------------------------------------------------------------------------
# Clean up any runtime artifacts that should never live under agent src
# ---------------------------------------------------------------------------
for junk in apptainer_images apptainer output_swe output_e2e_real_vllm \
    podman-service.log APPTAINER.md; do
    rm -rf "${DEST}/${junk}"
done
find "${DEST}" -maxdepth 1 -type f -name 'initial_agent_*.json' -delete 2>/dev/null || true

echo "Synced initial SWE agent -> ${DEST} (source: $([ "${USE_WORKTREE}" = "1" ] && echo "worktree" || echo "git HEAD"))"
