#!/usr/bin/env bash
# Lightweight remote-Docker / vLLM health probe for the Tencent VM.
#
# Run from the login node where you submit Slurm jobs. Supports both:
#   - SSH key-based auth (via ~/.ssh/config or default key)
#   - Password-based auth (via REMOTE_DOCKER_PASSWORD + sshpass)
#
# Usage (key-based):
#   bash New/remote_docker_health.sh
#
# Usage (password-based):
#   export REMOTE_DOCKER_PASSWORD='...'
#   bash New/remote_docker_health.sh

set -uo pipefail

REMOTE_HOST="${REMOTE_DOCKER_HOST:-43.157.32.135}"
REMOTE_USER="${REMOTE_DOCKER_USER:-ubuntu}"
INTERVAL="${HEALTH_INTERVAL:-30}"
SSH_TIMEOUT="${HEALTH_SSH_TIMEOUT:-10}"
SLOW_MS="${HEALTH_SLOW_MS:-3000}"

HAS_SSHPASS=0
HAS_PASSWORD=0
SSH_PASSWORD_HELPER=""

if [ -n "${REMOTE_DOCKER_PASSWORD:-}" ]; then
    HAS_PASSWORD=1
    if command -v sshpass >/dev/null 2>&1; then
        HAS_SSHPASS=1
    else
        SSH_PASSWORD_HELPER="$(mktemp "${TMPDIR:-/tmp}/hgm-health-askpass.XXXXXX")"
        chmod 700 "${SSH_PASSWORD_HELPER}"
        cat > "${SSH_PASSWORD_HELPER}" <<'EOF'
#!/bin/sh
printf '%s\n' "${REMOTE_DOCKER_PASSWORD}"
EOF
        export SSH_ASKPASS="${SSH_PASSWORD_HELPER}"
        export SSH_ASKPASS_REQUIRE=force
        export DISPLAY="${DISPLAY:-hgm-askpass:0}"
        trap 'rm -f "${SSH_PASSWORD_HELPER}"' EXIT
    fi
fi

ssh_opts=(
    -F /dev/null
    -o StrictHostKeyChecking=no
    -o UserKnownHostsFile=/dev/null
    -o LogLevel=ERROR
    -o ConnectTimeout="${SSH_TIMEOUT}"
    -o ServerAliveInterval=5
    -o ServerAliveCountMax=2
    -o NumberOfPasswordPrompts=1
    -o PreferredAuthentications=password
)

probe() {
    local cmd="$1"
    local start_ns end_ns ms output rc
    start_ns=$(date +%s%N)
    
    if [ "${HAS_SSHPASS}" = "1" ] && [ "${HAS_PASSWORD}" = "1" ]; then
        # Use sshpass for password-based auth
        output=$(SSHPASS="${REMOTE_DOCKER_PASSWORD}" timeout "${SSH_TIMEOUT}" \
            sshpass -e ssh "${ssh_opts[@]}" \
            "${REMOTE_USER}@${REMOTE_HOST}" "${cmd}" 2>&1)
    elif [ "${HAS_PASSWORD}" = "1" ]; then
        # Use SSH_ASKPASS helper for password-based auth
        output=$(setsid -w timeout "${SSH_TIMEOUT}" \
            ssh "${ssh_opts[@]}" \
            "${REMOTE_USER}@${REMOTE_HOST}" "${cmd}" </dev/null 2>&1)
    else
        # Use key-based auth (from ~/.ssh/config or default keys)
        output=$(timeout "${SSH_TIMEOUT}" \
            ssh -o StrictHostKeyChecking=no \
            "${REMOTE_USER}@${REMOTE_HOST}" "${cmd}" 2>&1)
    fi
    rc=$?
    end_ns=$(date +%s%N)
    ms=$(( (end_ns - start_ns) / 1000000 ))
    if [ $rc -eq 124 ]; then
        echo "TIMEOUT ${ms} ssh-timeout-${SSH_TIMEOUT}s"
    elif [ $rc -ne 0 ]; then
        echo "FAIL ${ms} rc=${rc} $(echo "${output}" | tr '\n' ' ' | cut -c1-180)"
    elif [ "${ms}" -ge "${SLOW_MS}" ]; then
        echo "SLOW ${ms} $(echo "${output}" | tr '\n' ' ' | cut -c1-120)"
    else
        echo "OK ${ms} $(echo "${output}" | tr '\n' ' ' | cut -c1-120)"
    fi
}

echo "# remote_docker_health.sh starting"
echo "#   host=${REMOTE_USER}@${REMOTE_HOST} interval=${INTERVAL}s slow_threshold=${SLOW_MS}ms"
echo "#   columns: timestamp_utc | check | status | latency_ms | detail"

while true; do
    ts=$(date -u +%Y-%m-%dT%H:%M:%SZ)

    docker_status=$(probe 'docker info --format "v={{.ServerVersion}} containers={{.Containers}}({{.ContainersRunning}}r) overlay-fs=$(stat -f -c %T /var/lib/docker 2>/dev/null || echo ?) disk=$(df -BG --output=avail /var/lib/docker 2>/dev/null | tail -1 | tr -d "G ")"')
    line="${ts} docker ${docker_status}"
    echo "${line}"
    case "${docker_status}" in
        OK*) ;;
        *) echo "${line}" >&2 ;;
    esac

    vllm_status=$(probe 'curl -s -o /dev/null -w "http=%{http_code}" --connect-timeout 5 http://127.0.0.1:8000/v1/models')
    line="${ts} vllm   ${vllm_status}"
    echo "${line}"
    case "${vllm_status}" in
        OK*http=200*) ;;
        *) echo "${line}" >&2 ;;
    esac

    sleep "${INTERVAL}"
done
