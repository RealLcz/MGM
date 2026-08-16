# shellcheck shell=bash
# Redirect runtime caches off the 308G home quota to jinhe (10T filesystem).
# Source after REPO_ROOT is set:
#   . "${REPO_ROOT}/swe_scripts/cache_env.inc.sh"
#   cache_env_setup

cache_env_setup() {
    local _jinhe_root="${JINHE_ROOT:-$(dirname "${REPO_ROOT:-$PWD}")}"
    export JINHE_ROOT="${_jinhe_root}"
    export JINHE_CACHE_ROOT="${JINHE_CACHE_ROOT:-${_jinhe_root}/.cache}"
    mkdir -p "${JINHE_CACHE_ROOT}"

    export XDG_CACHE_HOME="${XDG_CACHE_HOME:-${JINHE_CACHE_ROOT}}"
    export HF_HOME="${HF_HOME:-${JINHE_CACHE_ROOT}/huggingface}"
    export TORCH_HOME="${TORCH_HOME:-${JINHE_CACHE_ROOT}/torch}"
    export TRITON_CACHE_DIR="${TRITON_CACHE_DIR:-${JINHE_CACHE_ROOT}/triton}"
    export VLLM_CACHE_ROOT="${VLLM_CACHE_ROOT:-${JINHE_CACHE_ROOT}/vllm}"
    export VLLM_CONFIG_ROOT="${VLLM_CONFIG_ROOT:-${JINHE_CACHE_ROOT}/vllm_config}"
    export TRANSFORMERS_CACHE="${TRANSFORMERS_CACHE:-${HF_HOME}/hub}"
    export VLLM_NO_USAGE_STATS="${VLLM_NO_USAGE_STATS:-1}"

    if [ -n "${SLURM_JOB_ID:-}" ]; then
        # Always isolate scratch per job. Prefer jinhe (large FS) over node /tmp: concurrent
        # writable Apptainer sandboxes can exhaust small local tmp and trigger SIGKILL.
        export TMPDIR="${SWE_EVAL_TMPDIR:-${JINHE_CACHE_ROOT}/swe_tmp/${SLURM_JOB_ID}}"
        mkdir -p "${TMPDIR}"
        # flashinfer JIT writes under $FLASHINFER_WORKSPACE_BASE/.cache/flashinfer
        export FLASHINFER_WORKSPACE_BASE="${TMPDIR}/flashinfer"
    else
        export FLASHINFER_WORKSPACE_BASE="${FLASHINFER_WORKSPACE_BASE:-${_jinhe_root}}"
    fi

    mkdir -p \
        "${HF_HOME}" \
        "${TORCH_HOME}" \
        "${TRITON_CACHE_DIR}" \
        "${VLLM_CACHE_ROOT}" \
        "${VLLM_CONFIG_ROOT}" \
        "${FLASHINFER_WORKSPACE_BASE}/.cache/flashinfer"

    echo "Cache env: JINHE_CACHE_ROOT=${JINHE_CACHE_ROOT}"
    echo "  HF_HOME=${HF_HOME}"
    echo "  FLASHINFER_WORKSPACE_BASE=${FLASHINFER_WORKSPACE_BASE}"
    echo "  XDG_CACHE_HOME=${XDG_CACHE_HOME}"
    echo "  TMPDIR=${TMPDIR:-/tmp}"
}
