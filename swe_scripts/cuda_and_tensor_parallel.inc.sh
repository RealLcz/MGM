# shellcheck shell=bash
# Source from swe_scripts/*.slurm after conda/env setup:
#   REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
#   # shellcheck source=/dev/null
#   . "${REPO_ROOT}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
#   cuda_and_tensor_parallel_setup
#
# Sets TENSOR_PARALLEL_SIZE from the GPUs allocated by Slurm.
#
# Overrides (optional):
#   TENSOR_PARALLEL_SIZE      — if already set in the environment, left unchanged.
#   MGM_SKIP_FOREIGN_GPU_CHECK=1 — skip the pre-flight check below (debug only).
#
# Pre-flight: if any GPU index in CUDA_VISIBLE_DEVICES has a *compute* process owned
# by another Linux user, exit with a clear message (avoids opaque vLLM/NCCL death).

cuda_and_tensor_parallel_setup() {
    # 1) CUDA_VISIBLE_DEVICES: trust Slurm; do not guess physical GPU IDs.
    if [ -n "${MGM_CUDA_VISIBLE_DEVICES:-}" ]; then
        export CUDA_VISIBLE_DEVICES="${MGM_CUDA_VISIBLE_DEVICES}"
    elif [ -z "${CUDA_VISIBLE_DEVICES:-}" ]; then
        if [ -n "${SLURM_STEP_GPUS:-}" ]; then
            export CUDA_VISIBLE_DEVICES="${SLURM_STEP_GPUS}"
        elif [ -n "${SLURM_JOB_GPUS:-}" ]; then
            export CUDA_VISIBLE_DEVICES="${SLURM_JOB_GPUS}"
        else
            echo "ERROR: CUDA_VISIBLE_DEVICES is not set and Slurm did not expose SLURM_STEP_GPUS/SLURM_JOB_GPUS." >&2
            echo "Run this script inside a Slurm GPU allocation instead of guessing physical GPU IDs." >&2
            exit 1
        fi
    fi
    echo "Using Slurm-allocated CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}"

    # 2) Optional: fail fast if other users hold GPUs we are about to use
    if [ "${MGM_SKIP_FOREIGN_GPU_CHECK:-0}" != "1" ] && command -v nvidia-smi >/dev/null 2>&1; then
        local ME gid pid u
        ME="$(id -un)"
        IFS=',' read -ra _mgm_gids <<< "${CUDA_VISIBLE_DEVICES}"
        for gid in "${_mgm_gids[@]}"; do
            gid="${gid//[[:space:]]/}"
            [ -n "${gid}" ] || continue
            while IFS= read -r pid; do
                pid="${pid//[[:space:]]/}"
                [ -n "${pid}" ] || continue
                case "${pid}" in *[^0-9]*) continue ;; esac
                [ -d "/proc/${pid}" ] || continue
                u="$(ps -o user= -p "${pid}" 2>/dev/null | tr -d ' ')"
                [ "${u}" = "${ME}" ] || {
                    echo "ERROR: physical GPU ${gid} is in use by pid=${pid} (user=${u}, you are ${ME})." >&2
                    echo "Fix: use a node with free GPUs, e.g. #SBATCH --exclusive, or a single-user GPU partition;" >&2
                    echo "      or request a node/partition whose allocated GPUs are free." >&2
                    echo "      To bypass only for debugging: MGM_SKIP_FOREIGN_GPU_CHECK=1" >&2
                    exit 1
                }
            done < <(nvidia-smi -i "${gid}" --query-compute-apps=pid --format=csv,noheader 2>/dev/null || true)
        done
    fi

    # 3) Tensor parallel size: largest power of 2 <= number of visible GPUs, unless preset.
    local NUM_VISIBLE_GPUS TP_AUTO
    NUM_VISIBLE_GPUS=$(echo "${CUDA_VISIBLE_DEVICES}" | tr ',' '\n' | grep -c '.')
    if [ "${NUM_VISIBLE_GPUS}" -lt 1 ]; then
        echo "ERROR: CUDA_VISIBLE_DEVICES is empty or invalid: ${CUDA_VISIBLE_DEVICES}" >&2
        exit 1
    fi
    if [ -z "${TENSOR_PARALLEL_SIZE:-}" ]; then
        TP_AUTO=1
        while [ $((TP_AUTO * 2)) -le "${NUM_VISIBLE_GPUS}" ]; do
            TP_AUTO=$((TP_AUTO * 2))
        done
        export TENSOR_PARALLEL_SIZE="${TP_AUTO}"
    fi
    echo "Visible GPUs: ${NUM_VISIBLE_GPUS}; using TENSOR_PARALLEL_SIZE=${TENSOR_PARALLEL_SIZE}"
}
