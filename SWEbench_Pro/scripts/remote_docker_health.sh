#!/usr/bin/env bash
# Lightweight remote-Docker / vLLM health probe for the Tencent VM.
#
# Pings the remote Docker daemon and (optionally) the vLLM reverse-tunnel
# endpoint every $INTERVAL seconds, logs one line per probe with a UTC
# timestamp, latency in ms, and a STATUS token. Any non-OK status (slow,
# timeout, http error, daemon down) is also echoed to stderr so it shows
# up immediately in any `tail -f` you have open.
#
# Run on the login node where you submit Slurm jobs (not on the Slurm
# compute node). Requires REMOTE_DOCKER_PASSWORD in the environment for
# sshpass-based auth, matching what eval_mgm_pro.slurm uses.
#
# Usage:
#   export REMOTE_DOCKER_PASSWORD='...'
#   nohup bash SWEbench_Pro/scripts/remote_docker_health.sh \
#     > SWEbench_Pro/logs/remote_docker_health.log 2>&1 &
#
# Stop with: pkill -f remote_docker_health.sh

set -uo pipefail

REMOTE_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
REMOTE_USER="${REMOTE_DOCKER_USER:-ubuntu}"
INTERVAL="${HEALTH_INTERVAL:-30}"
SSH_TIMEOUT="${HEALTH_SSH_TIMEOUT:-10}"
SLOW_MS="${HEALTH_SLOW_MS:-3000}"

if [ -z "${REMOTE_DOCKER_PASSWORD:-}" ]; then
    echo "ERROR: REMOTE_DOCKER_PASSWORD env var not set." >&2
    exit 2
fi

# Use sshpass when available; otherwise fall back to the SSH_ASKPASS helper
# pattern that eval_mgm_pro.slurm already uses (writes the password to a
# temp script, points SSH_ASKPASS at it, ssh reads it via that helper).
HAS_SSHPASS=0
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
    if [ "${HAS_SSHPASS}" = "1" ]; then
        output=$(SSHPASS="${REMOTE_DOCKER_PASSWORD}" timeout "${SSH_TIMEOUT}" \
            sshpass -e ssh "${ssh_opts[@]}" \
            "${REMOTE_USER}@${REMOTE_HOST}" "${cmd}" 2>&1)
    else
        output=$(setsid -w timeout "${SSH_TIMEOUT}" \
            ssh "${ssh_opts[@]}" \
            "${REMOTE_USER}@${REMOTE_HOST}" "${cmd}" </dev/null 2>&1)
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

    # 1. docker daemon health
    docker_status=$(probe 'docker info --format "v={{.ServerVersion}} containers={{.Containers}}({{.ContainersRunning}}r) overlay-fs=$(stat -f -c %T /var/lib/docker 2>/dev/null || echo ?) disk=$(df -BG --output=avail /var/lib/docker 2>/dev/null | tail -1 | tr -d "G ")"')
    line="${ts} docker ${docker_status}"
    echo "${line}"
    case "${docker_status}" in
        OK*) ;;
        *) echo "${line}" >&2 ;;
    esac

    # 2. vLLM tunnel reachability from VM (only when SLURM_JOB_ID job is up,
    # but cheap to probe regardless)
    vllm_status=$(probe 'curl -s -o /dev/null -w "http=%{http_code}" --connect-timeout 5 http://127.0.0.1:8000/v1/models')
    line="${ts} vllm   ${vllm_status}"
    echo "${line}"
    case "${vllm_status}" in
        OK*http=200*) ;;
        *) echo "${line}" >&2 ;;
    esac

    sleep "${INTERVAL}"
done
