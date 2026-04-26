# shellcheck shell=bash
# Source this file from swe_scripts/*.slurm immediately before launching vllm
# (use SLURM_SUBMIT_DIR, not BASH_SOURCE — Slurm runs a copy from /var/spool/slurmd/...):
#   REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
#   . "${REPO_ROOT}/swe_scripts/vllm_pre_start_cleanup.inc.sh"
#   vllm_pre_start_cleanup
#
# Frees the API port, 8001 (vLLM TCPStore / NCCL coordination when 8000 is busy), and
# best-effort kills stale vLLM-related processes owned by the current Linux user (including
# GPU PIDs from nvidia-smi). Does not kill other users' jobs.
#
# Set VLLM_SKIP_PRE_CLEAN=1 to skip (debugging only).

vllm_pre_start_cleanup() {
    if [ "${VLLM_SKIP_PRE_CLEAN:-0}" = "1" ]; then
        echo "VLLM_SKIP_PRE_CLEAN=1 — skipping pre-vLLM cleanup."
        return 0
    fi

    echo "=== Pre-vLLM cleanup (stale vLLM / common ports; current user only) ==="

    pkill -u "$(id -un)" -f "vllm\.entrypoints\.openai\.api_server" 2>/dev/null || true
    pkill -u "$(id -un)" -f "VLLM::" 2>/dev/null || true
    sleep 2

    for p in "${VLLM_PORT:-8000}" 8001; do
        fuser -k -9 "${p}/tcp" 2>/dev/null || true
        if command -v ss >/dev/null 2>&1; then
            for pid in $(ss -tlnp "sport = :${p}" 2>/dev/null | grep -oP 'pid=\K[0-9]+' 2>/dev/null | sort -u); do
                [ -n "${pid}" ] || continue
                echo "Killing process ${pid} still listening on :${p}"
                pkill -9 -P "${pid}" 2>/dev/null || true
                kill -9 "${pid}" 2>/dev/null || true
            done
        fi
    done
    sleep 2

    if command -v nvidia-smi >/dev/null 2>&1; then
        ME="$(id -un)"
        for pid in $(nvidia-smi --query-compute-apps=pid --format=csv,noheader 2>/dev/null | tr -d ' \r' | sort -u); do
            [ -n "${pid}" ] || continue
            case "${pid}" in
                *[^0-9]*) continue ;;
            esac
            [ -d "/proc/${pid}" ] || continue
            puser=$(ps -o user= -p "${pid}" 2>/dev/null | tr -d ' ')
            [ "${puser}" = "${ME}" ] || continue
            _cmd=$(tr '\0' ' ' < "/proc/${pid}/cmdline" 2>/dev/null || true)
            if echo "${_cmd}" | grep -qiE 'vllm|VLLM::|openai\.api_server'; then
                echo "Killing own stale GPU process pid=${pid}"
                kill -9 "${pid}" 2>/dev/null || true
            fi
        done
    fi
    sleep 2
    echo "=== nvidia-smi (after pre-vLLM cleanup) ==="
    nvidia-smi 2>/dev/null || true
    echo "=== end pre-vLLM cleanup ==="
}
