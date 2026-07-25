#!/bin/bash
#SBATCH --job-name=HGM-CKPT
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=8
#SBATCH --mem=32G
#SBATCH --time=04:00:00
#SBATCH --output=logs/checkpoint_predictions_%j.out
#SBATCH --error=logs/checkpoint_predictions_%j.err
#
# Run make_report on an existing predictions folder and update node metadata.json.
#
# Example:
#   CHECKPOINT_NODE_ID=20260616_024835_287218 \
#   CHECKPOINT_PRED_SUBDIR=20260616_024835_287218_29 \
#   sbatch swe_scripts/checkpoint_predictions.slurm

set -euo pipefail

REPO_ROOT="${SLURM_SUBMIT_DIR:-$PWD}"
cd "${REPO_ROOT}"
mkdir -p logs

SSH_TUNNEL_PID=""
SSH_PASSWORD_HELPER=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${SSH_TUNNEL_PID}" ] && kill "${SSH_TUNNEL_PID}" 2>/dev/null
    [ -n "${SSH_PASSWORD_HELPER}" ] && rm -f "${SSH_PASSWORD_HELPER}"
    rm -f "${REMOTE_DOCKER_SOCKET:-}"
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate HGM
export PYTHON_BIN="${CONDA_PREFIX}/bin/python"
"${PYTHON_BIN}" -V
export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_mgm}"
export CHECKPOINT_NODE_ID="${CHECKPOINT_NODE_ID:?Set CHECKPOINT_NODE_ID}"
export CHECKPOINT_PRED_SUBDIR="${CHECKPOINT_PRED_SUBDIR:?Set CHECKPOINT_PRED_SUBDIR}"
export CHECKPOINT_NUM_EVAL_PROCS="${CHECKPOINT_NUM_EVAL_PROCS:-2}"

export ENABLE_REMOTE_DOCKER="${ENABLE_REMOTE_DOCKER:-1}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/docker-remote.sock}"
export REMOTE_DOCKER_PASSWORD="${REMOTE_DOCKER_PASSWORD:-@TXYfwq798798}"

if [ -n "${REMOTE_DOCKER_PASSWORD:-}" ]; then
    if command -v sshpass >/dev/null 2>&1; then
        echo "Remote Docker SSH password auth enabled via sshpass."
    else
        SSH_PASSWORD_HELPER="$(mktemp "${TMPDIR:-/tmp}/hgm-ssh-askpass.XXXXXX")"
        chmod 700 "${SSH_PASSWORD_HELPER}"
        cat > "${SSH_PASSWORD_HELPER}" <<'EOF'
#!/bin/sh
printf '%s\n' "${REMOTE_DOCKER_PASSWORD}"
EOF
        export SSH_ASKPASS="${SSH_PASSWORD_HELPER}"
        export SSH_ASKPASS_REQUIRE=force
        export DISPLAY="${DISPLAY:-hgm-askpass:0}"
        echo "Remote Docker SSH password auth enabled via SSH_ASKPASS."
    fi
fi

ssh_remote() {
    if [ -n "${REMOTE_DOCKER_PASSWORD:-}" ] && command -v sshpass >/dev/null 2>&1; then
        SSHPASS="${REMOTE_DOCKER_PASSWORD}" sshpass -e ssh \
            -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=5 \
            -o NumberOfPasswordPrompts=1 \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" \
            "$@"
    else
        ssh -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ConnectTimeout=5 \
            -o NumberOfPasswordPrompts=1 \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}" \
            "$@"
    fi
}

ssh_tunnel() {
    if [ -n "${REMOTE_DOCKER_PASSWORD:-}" ] && command -v sshpass >/dev/null 2>&1; then
        SSHPASS="${REMOTE_DOCKER_PASSWORD}" sshpass -e ssh -nNT \
            -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ServerAliveInterval=30 \
            -o ServerAliveCountMax=20 \
            -o ExitOnForwardFailure=yes \
            -o NumberOfPasswordPrompts=1 \
            -L "${REMOTE_DOCKER_SOCKET}:/var/run/docker.sock" \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}"
    else
        ssh -nNT -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ServerAliveInterval=30 \
            -o ServerAliveCountMax=20 \
            -o ExitOnForwardFailure=yes \
            -o NumberOfPasswordPrompts=1 \
            -L "${REMOTE_DOCKER_SOCKET}:/var/run/docker.sock" \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}"
    fi
}

if [ "${ENABLE_REMOTE_DOCKER}" = "1" ]; then
    echo "Setting up SSH tunnel to remote Docker on ${REMOTE_DOCKER_HOST}..."
    pkill -f "ssh.*${REMOTE_DOCKER_HOST}.*docker-remote" 2>/dev/null || true
    sleep 2
    rm -f "${REMOTE_DOCKER_SOCKET}"
    ssh_tunnel &
    SSH_TUNNEL_PID=$!
    disown "${SSH_TUNNEL_PID}" 2>/dev/null || true
    for i in $(seq 1 15); do
        if [ -S "${REMOTE_DOCKER_SOCKET}" ]; then break; fi
        sleep 1
    done
    if [ ! -S "${REMOTE_DOCKER_SOCKET}" ]; then
        echo "ERROR: SSH tunnel failed — ${REMOTE_DOCKER_SOCKET} not created" >&2
        exit 1
    fi
    export DOCKER_HOST="unix://${REMOTE_DOCKER_SOCKET}"
    echo "Remote Docker tunnel ready (pid=${SSH_TUNNEL_PID})."
    "${PYTHON_BIN}" - <<'PY'
import docker
client = docker.from_env()
print("Remote Docker ping:", client.ping())
PY
fi

echo "Checkpointing predictions for node=${CHECKPOINT_NODE_ID} subdir=${CHECKPOINT_PRED_SUBDIR}"
PYTHONUNBUFFERED=1 "${PYTHON_BIN}" scripts/checkpoint_node_predictions.py \
    --node-id "${CHECKPOINT_NODE_ID}" \
    --predictions-subdir "${CHECKPOINT_PRED_SUBDIR}" \
    --hgm-output-dir "${HGM_OUTPUT_DIR}" \
    --num-eval-procs "${CHECKPOINT_NUM_EVAL_PROCS}"

echo "checkpoint_predictions.slurm finished."
