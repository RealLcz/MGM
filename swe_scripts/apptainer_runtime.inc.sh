# Apptainer runtime defaults for MendelGM swe_scripts / Slurm jobs.
# Source from repo root after cd to SLURM_SUBMIT_DIR:
#   . "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"

# Image and workspace locations (override for shared filesystems).
# Prefer jinhe (10T) over home (308G quota). cache_env.inc.sh sets HF_HOME when sourced first.
_JINHE_ROOT="${JINHE_ROOT:-$(dirname "${REPO_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}")}"
export HF_HOME="${HF_HOME:-${_JINHE_ROOT}/.cache/huggingface}"
export APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${REPO_ROOT:-${SLURM_SUBMIT_DIR:-$PWD}}/apptainer_images}"
unset _JINHE_ROOT
if [ -n "${SLURM_JOB_ID:-}" ]; then
    _APPTAINER_WS_BASE="${SLURM_TMPDIR:-${TMPDIR:-/tmp}}"
    export APPTAINER_WORKSPACE_ROOT="${APPTAINER_WORKSPACE_ROOT:-${_APPTAINER_WS_BASE}/apptainer-workspaces-${SLURM_JOB_ID}}"
else
    export APPTAINER_WORKSPACE_ROOT="${APPTAINER_WORKSPACE_ROOT:-${TMPDIR:-/tmp}/apptainer-workspaces}"
fi
export APPTAINER_CACHEDIR="${APPTAINER_CACHEDIR:-${APPTAINER_IMAGE_DIR}/cache}"
export APPTAINER_TMPDIR="${APPTAINER_TMPDIR:-${TMPDIR:-/tmp}}"

mkdir -p "${APPTAINER_IMAGE_DIR}" "${APPTAINER_WORKSPACE_ROOT}" "${APPTAINER_CACHEDIR}"

# vLLM runs on the same compute node as Apptainer task containers.
# Host network (--network host) requires root/fakeroot on many HPC clusters; default off.
# Without host network, containers cannot reach 127.0.0.1 on the host — use node IP instead.
export APPTAINER_USE_HOST_NETWORK="${APPTAINER_USE_HOST_NETWORK:-0}"
_apptainer_host_ip="$(hostname -I 2>/dev/null | awk '{print $1}' || true)"
if [ -z "${_apptainer_host_ip}" ]; then
    _apptainer_host_ip="$(hostname -i 2>/dev/null | awk '{print $1}' || true)"
fi
if [ "${APPTAINER_USE_HOST_NETWORK}" = "1" ]; then
    export VLLM_CONTAINER_HOST="${VLLM_CONTAINER_HOST:-127.0.0.1}"
else
    export VLLM_CONTAINER_HOST="${VLLM_CONTAINER_HOST:-${_apptainer_host_ip:-127.0.0.1}}"
fi
unset _apptainer_host_ip
export SWE_CONTAINER_NETWORK="${SWE_CONTAINER_NETWORK:-host}"
export SWE_PRO_CONTAINER_NETWORK="${SWE_PRO_CONTAINER_NETWORK:-host}"
export POLYGLOT_CONTAINER_NETWORK_MODE="${POLYGLOT_CONTAINER_NETWORK_MODE:-host}"
export APPTAINER_API_TIMEOUT="${APPTAINER_API_TIMEOUT:-7200}"

apptainer_runtime_verify() {
    if ! command -v apptainer >/dev/null 2>&1; then
        echo "ERROR: apptainer not found in PATH. Install Apptainer to run MendelGM." >&2
        return 1
    fi
    echo "Apptainer runtime: $(apptainer version 2>/dev/null | head -1)"
    echo "  APPTAINER_IMAGE_DIR=${APPTAINER_IMAGE_DIR}"
    echo "  APPTAINER_WORKSPACE_ROOT=${APPTAINER_WORKSPACE_ROOT}"
    return 0
}
