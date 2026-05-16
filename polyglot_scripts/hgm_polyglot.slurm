#!/bin/bash
#SBATCH --job-name=HGM-PG
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:4
#SBATCH --time=168:00:00
#SBATCH --output=logs/hgm_polyglot_%j.out
#SBATCH --error=logs/hgm_polyglot_%j.err
#
# 启动 HGM 在 Polyglot benchmark 上进化（仿 swe_scripts/hgm.slurm）：
# - 在计算节点上起 vLLM
# - 默认 ENABLE_REMOTE_DOCKER=1：SSH 转发远端 Docker，反转 vLLM 供容器内访问
# - hgm.py 带 --polyglot，任务集来自 polyglot/subsets（见 hgm.py）
#
# 在仓库根目录提交：
#   sbatch polyglot_scripts/hgm_polyglot.slurm
#
# 指定输出目录与配置：
#   HGM_OUTPUT_DIR=output_polyglot/my_run  HGM_CONFIG=polyglot_scripts/config_polyglot.yaml sbatch polyglot_scripts/hgm_polyglot.slurm
#
# 从某次运行继续（加载树与 init_evaluated_tasks 等）：
#   HGM_OUTPUT_DIR=output_polyglot/new_run  CONTINUE_FROM=/path/to/prior/output_dir  sbatch polyglot_scripts/hgm_polyglot.slurm
# （会覆盖 --continue_from null）
#
# 仅覆盖预算示例：
#   HGM_MAX_TASK_EVALS=100 sbatch polyglot_scripts/hgm_polyglot.slurm
#
# 调试时手动覆盖 GPU；正常提交时建议只用 --gres=gpu:4，交给 Slurm 分配:
#   sbatch --export=ALL,POLYGLOT_CUDA_VISIBLE_DEVICES=4,5,6,7 polyglot_scripts/hgm_polyglot.slurm
#
# 环境变量（与 swe 版一致，可选）:
#   HF_HOME, VLLM_MODEL_NAME, HGM_MAX_WORKERS, ENABLE_REMOTE_DOCKER, REMOTE_DOCKER_*, PYTHON_BIN 等
#   PYTHON_BIN 默认使用 conda activate HGM 后的 ${CONDA_PREFIX}/bin/python（与 HGM 内 vLLM 一致）
#

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

SSH_TUNNEL_PID=""
VLLM_PID=""
SSH_PASSWORD_HELPER=""
cleanup() {
    echo "Cleaning up..."
    [ -n "${VLLM_PID}" ] && kill "${VLLM_PID}" 2>/dev/null && sleep 1 && kill -9 "${VLLM_PID}" 2>/dev/null
    [ -n "${SSH_TUNNEL_PID}" ] && kill "${SSH_TUNNEL_PID}" 2>/dev/null
    [ -n "${SSH_PASSWORD_HELPER}" ] && rm -f "${SSH_PASSWORD_HELPER}"
    rm -f "${REMOTE_DOCKER_SOCKET:-}"
}
trap cleanup EXIT INT TERM

eval "$(conda shell.bash hook)"
conda activate HGM
# vLLM and hgm.py use the activated conda env (HGM) unless PYTHON_BIN is preset.
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
if [ -n "${POLYGLOT_CUDA_VISIBLE_DEVICES:-}" ]; then
    export MGM_CUDA_VISIBLE_DEVICES="${POLYGLOT_CUDA_VISIBLE_DEVICES}"
fi
export VLLM_PORT="${VLLM_PORT:-8000}"
export TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-4}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-81920}"
export HGM_MAX_WORKERS="${HGM_MAX_WORKERS:-4}"
export ENABLE_REMOTE_DOCKER="${ENABLE_REMOTE_DOCKER:-1}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/docker-remote.sock}"

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
            -R "${REMOTE_VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}"
    else
        ssh -nNT -F /dev/null \
            -o StrictHostKeyChecking=no \
            -o ServerAliveInterval=30 \
            -o ServerAliveCountMax=20 \
            -o ExitOnForwardFailure=yes \
            -o NumberOfPasswordPrompts=1 \
            -L "${REMOTE_DOCKER_SOCKET}:/var/run/docker.sock" \
            -R "${REMOTE_VLLM_PORT}:127.0.0.1:${VLLM_PORT}" \
            "${REMOTE_DOCKER_USER}@${REMOTE_DOCKER_HOST}"
    fi
}

HOST_IP="$(hostname -I | awk '{print $1}')"
if [ -z "${HOST_IP}" ]; then
    HOST_IP="$(hostname -i | awk '{print $1}')"
fi
if [ -z "${HOST_IP}" ]; then
    HOST_IP="127.0.0.1"
fi
export VLLM_HOST="${VLLM_HOST:-$HOST_IP}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"

if [ -z "${TOOL_CALL_PARSER:-}" ]; then
    case "${VLLM_MODEL_NAME}" in
        *Qwen3-Coder*|*Qwen3*)  TOOL_CALL_PARSER="qwen3_coder" ;;
        *gemma-4*|*gemma4*)      TOOL_CALL_PARSER="functiongemma" ;;
        *)                       TOOL_CALL_PARSER="hermes" ;;
    esac
fi
export TOOL_CALL_PARSER

REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup
POLYGLOT_VISIBLE_GPUS="$(printf '%s\n' "${CUDA_VISIBLE_DEVICES}" | tr ',' '\n' | grep -c '.')"
if [ "${POLYGLOT_VISIBLE_GPUS}" -ne 4 ]; then
    echo "ERROR: Polyglot evaluation must use exactly 4 GPUs; got CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}" >&2
    echo "Submit with --gres=gpu:4 and let Slurm set CUDA_VISIBLE_DEVICES, or override POLYGLOT_CUDA_VISIBLE_DEVICES only for debugging." >&2
    exit 1
fi
if [ "${TENSOR_PARALLEL_SIZE}" -gt 4 ]; then
    echo "ERROR: TENSOR_PARALLEL_SIZE must be <= 4 for Polyglot 4-GPU jobs; got ${TENSOR_PARALLEL_SIZE}" >&2
    exit 1
fi

echo "Cleaning up any stale processes on port ${VLLM_PORT}..."
fuser -k -9 "${VLLM_PORT}/tcp" 2>/dev/null || true
for pid in $(ss -tlnp "sport = :${VLLM_PORT}" 2>/dev/null | grep -oP 'pid=\K[0-9]+' | sort -u); do
    echo "Killing stale process ${pid} and its children on port ${VLLM_PORT}"
    pkill -9 -P "${pid}" 2>/dev/null || true
    kill -9 "${pid}" 2>/dev/null || true
done
sleep 5
if ss -tln "sport = :${VLLM_PORT}" 2>/dev/null | grep -q ":${VLLM_PORT}"; then
    echo "WARNING: port ${VLLM_PORT} still occupied, trying alternate port $((VLLM_PORT + 1))..."
    export VLLM_PORT=$((VLLM_PORT + 1))
    echo "Using port ${VLLM_PORT} instead"
fi
export REMOTE_VLLM_PORT="${REMOTE_VLLM_PORT:-${VLLM_PORT}}"

if [ "${ENABLE_REMOTE_DOCKER}" = "1" ]; then
    echo "Setting up SSH tunnel to remote Docker on ${REMOTE_DOCKER_HOST}..."

    pkill -f "ssh.*${REMOTE_DOCKER_HOST}.*docker-remote" 2>/dev/null || true
    ssh_remote "fuser -k ${REMOTE_VLLM_PORT}/tcp 2>/dev/null || true" 2>/dev/null || true
    sleep 2

    rm -f "${REMOTE_DOCKER_SOCKET}"
    ssh_tunnel &
    SSH_TUNNEL_PID=$!

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
    echo "vLLM reverse tunnel: VM:${REMOTE_VLLM_PORT} -> localhost:${VLLM_PORT}"
fi

echo "Starting vLLM server..."

"${PYTHON_BIN}" -m vllm.entrypoints.openai.api_server \
    --host 0.0.0.0 \
    --port "${VLLM_PORT}" \
    --model "${VLLM_MODEL_NAME}" \
    --served-model-name "${SERVED_MODEL_NAME}" \
    --tensor-parallel-size "${TENSOR_PARALLEL_SIZE}" \
    --gpu-memory-utilization "${GPU_MEMORY_UTILIZATION}" \
    --max-model-len "${VLLM_MAX_MODEL_LEN}" \
    --enforce-eager \
    --enable-auto-tool-choice \
    --tool-call-parser "${TOOL_CALL_PARSER}" \
    --dtype auto \
    --trust-remote-code \
    > "logs/vllm_polyglot_${SLURM_JOB_ID}.stdout.log" 2> "logs/vllm_polyglot_${SLURM_JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT=600
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process (pid=${VLLM_PID}) died. Check logs/vllm_polyglot_${SLURM_JOB_ID}.stderr.log" >&2
        exit 1
    fi
    VLLM_WAIT=$((VLLM_WAIT + 5))
    if [ "${VLLM_WAIT}" -ge "${VLLM_MAX_WAIT}" ]; then
        echo "ERROR: vLLM did not become ready within ${VLLM_MAX_WAIT}s" >&2
        exit 1
    fi
    sleep 5
done

echo "vLLM server is ready (took ~${VLLM_WAIT}s)."

if [ "${ENABLE_REMOTE_DOCKER}" = "1" ]; then
    echo "Verifying vLLM reverse tunnel from VM..."
    TUNNEL_OK=0
    for attempt in $(seq 1 3); do
        VM_RESULT=$(ssh_remote "curl -s -o /dev/null -w '%{http_code}' --connect-timeout 5 http://127.0.0.1:${REMOTE_VLLM_PORT}/v1/models" 2>/dev/null || echo "000")
        if [ "${VM_RESULT}" = "200" ]; then
            TUNNEL_OK=1
            echo "Reverse tunnel verified: VM can reach vLLM (attempt ${attempt})."
            break
        fi
        echo "Reverse tunnel check failed (attempt ${attempt}/3, HTTP ${VM_RESULT}). Restarting tunnel..."
        kill "${SSH_TUNNEL_PID}" 2>/dev/null || true
        sleep 3
        ssh_remote "fuser -k ${REMOTE_VLLM_PORT}/tcp 2>/dev/null || true" 2>/dev/null || true
        sleep 2
        rm -f "${REMOTE_DOCKER_SOCKET}"
        ssh_tunnel &
        SSH_TUNNEL_PID=$!
        for i in $(seq 1 10); do
            if [ -S "${REMOTE_DOCKER_SOCKET}" ]; then break; fi
            sleep 1
        done
        export DOCKER_HOST="unix://${REMOTE_DOCKER_SOCKET}"
        sleep 2
    done
    if [ "${TUNNEL_OK}" -ne 1 ]; then
        echo "ERROR: vLLM reverse tunnel could not be established after 3 attempts" >&2
        exit 1
    fi
fi

# 默认：每次 job 一个带时间戳的子目录，避免与 SWE 的 output_mgm 混用
if [ -z "${HGM_OUTPUT_DIR:-}" ]; then
    HGM_OUTPUT_DIR="output_polyglot/$(date +%Y%m%d_%H%M%S)"
fi
export HGM_OUTPUT_DIR

HGM_CONFIG="${HGM_CONFIG:-polyglot_scripts/config_polyglot.yaml}"
HGM_MAX_TASK_EVALS="${HGM_MAX_TASK_EVALS:-200}"
POLYGLOT_RUN_MODE="${POLYGLOT_RUN_MODE:-HGM}"
SELF_IMPROVE_WEIGHT_A="${SELF_IMPROVE_WEIGHT_A:-1.0}"
SELF_IMPROVE_WEIGHT_B="${SELF_IMPROVE_WEIGHT_B:-0.0}"
SELF_IMPROVE_WEIGHT_C="${SELF_IMPROVE_WEIGHT_C:-0.0}"
FAILED_POOL_BOOST="${FAILED_POOL_BOOST:-1.0}"

# 新跑：--continue_from null 覆盖主 config.yaml 里可能存在的 continue_from
# 续跑：设置 CONTINUE_FROM=/path/to/prior_output，并自己指定 HGM_OUTPUT_DIR 为新的或同一目录
CONTINUE_FLAG=(--continue_from null)
if [ -n "${CONTINUE_FROM:-}" ]; then
    CONTINUE_FLAG=(--continue_from "${CONTINUE_FROM}")
fi

echo "Starting ${POLYGLOT_RUN_MODE} — Polyglot benchmark"
echo "  config=${HGM_CONFIG}  output_dir=${HGM_OUTPUT_DIR}"
echo "  max_task_evals=${HGM_MAX_TASK_EVALS}  continue_from: ${CONTINUE_FROM:-<fresh, null>}"
echo "  strategy weights A:B:C=${SELF_IMPROVE_WEIGHT_A}:${SELF_IMPROVE_WEIGHT_B}:${SELF_IMPROVE_WEIGHT_C}"
echo "  initial agent: initial_polyglot/ (hgm 内根据 polyglot 标志选择)"

PYTHONUNBUFFERED=1 "${PYTHON_BIN}" hgm.py \
    --config "${HGM_CONFIG}" \
    --polyglot \
    --output_dir "${HGM_OUTPUT_DIR}" \
    "${CONTINUE_FLAG[@]}" \
    --max_task_evals "${HGM_MAX_TASK_EVALS}" \
    --max_workers "${HGM_MAX_WORKERS}" \
    --eval_random_level 0 \
    --failed_pool_boost "${FAILED_POOL_BOOST}" \
    --self_improve_weight_a "${SELF_IMPROVE_WEIGHT_A}" \
    --self_improve_weight_b "${SELF_IMPROVE_WEIGHT_B}" \
    --self_improve_weight_c "${SELF_IMPROVE_WEIGHT_C}" \
    --self_improve_llm "${HGM_LLM_MODEL_ID}" \
    --downstream_llm "${HGM_LLM_MODEL_ID}" \
    --diagnose_llm "${HGM_LLM_MODEL_ID}"

echo "hgm_polyglot.slurm finished."
