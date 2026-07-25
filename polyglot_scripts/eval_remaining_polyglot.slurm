#!/bin/bash
#SBATCH --job-name=PG-EVAL-REM
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:4
#SBATCH --time=168:00:00
#SBATCH --output=logs/eval_remaining_polyglot_%j.out
#SBATCH --error=logs/eval_remaining_polyglot_%j.err
#
# Evaluate the remaining Polyglot small+medium tasks for selected evolved nodes.
#
# Defaults target the completed run and the two requested node IDs:
#   HGM_OUTPUT_DIR=output_polyglot/20260505_122548
#   EVAL_NODE_IDS="16 20"
#
# Submit from repo root:
#   sbatch polyglot_scripts/eval_remaining_polyglot.slurm
#
# Common overrides:
#   EVAL_NODE_IDS="16 20" EVAL_MAX_WORKERS=2 sbatch polyglot_scripts/eval_remaining_polyglot.slurm
#   EVAL_DRY_RUN=1 sbatch polyglot_scripts/eval_remaining_polyglot.slurm
#
# Remote Docker is enabled by default, matching hgm_polyglot.slurm.

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
export PYTHON_BIN="${PYTHON_BIN:-${CONDA_PREFIX}/bin/python}"
"${PYTHON_BIN}" -V

export HGM_OUTPUT_DIR="${HGM_OUTPUT_DIR:-output_polyglot/20260505_122548}"
export EVAL_NODE_IDS="${EVAL_NODE_IDS:-16 20}"
export EVAL_SMALL_JSON="${EVAL_SMALL_JSON:-polyglot/subsets/small.json}"
export EVAL_MEDIUM_JSON="${EVAL_MEDIUM_JSON:-polyglot/subsets/medium.json}"
export EVAL_INIT_AGENT_SRC="${EVAL_INIT_AGENT_SRC:-initial_polyglot/default_agent/src}"
export EVAL_MAX_WORKERS="${EVAL_MAX_WORKERS:-2}"
export EVAL_TIMEOUT="${EVAL_TIMEOUT:-3600}"
export DOCKER_API_TIMEOUT="${DOCKER_API_TIMEOUT:-7200}"

export HF_HOME="${HF_HOME:-$HOME/.cache/huggingface}"
export VLLM_MODEL_NAME="${VLLM_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
export SERVED_MODEL_NAME="${SERVED_MODEL_NAME:-Qwen/Qwen3.6-35B-A3B}"
if [ -n "${POLYGLOT_CUDA_VISIBLE_DEVICES:-}" ]; then
    export MGM_CUDA_VISIBLE_DEVICES="${POLYGLOT_CUDA_VISIBLE_DEVICES}"
fi
export VLLM_PORT="${VLLM_PORT:-8000}"
export TENSOR_PARALLEL_SIZE="${TENSOR_PARALLEL_SIZE:-4}"
export GPU_MEMORY_UTILIZATION="${GPU_MEMORY_UTILIZATION:-0.92}"
export VLLM_MAX_MODEL_LEN="${VLLM_MAX_MODEL_LEN:-112000}"
export ENABLE_REMOTE_DOCKER="${ENABLE_REMOTE_DOCKER:-1}"
export REMOTE_DOCKER_USER="${REMOTE_DOCKER_USER:-ubuntu}"
export REMOTE_DOCKER_HOST="${REMOTE_DOCKER_HOST:-43.131.5.182}"
export REMOTE_DOCKER_SOCKET="${REMOTE_DOCKER_SOCKET:-/tmp/docker-remote.sock}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-${SERVED_MODEL_NAME}}"

if [ -z "${TOOL_CALL_PARSER:-}" ]; then
    case "${VLLM_MODEL_NAME}" in
        *Qwen3-Coder*|*Qwen3*)  TOOL_CALL_PARSER="qwen3_coder" ;;
        *gemma-4*|*gemma4*)      TOOL_CALL_PARSER="functiongemma" ;;
        *)                       TOOL_CALL_PARSER="hermes" ;;
    esac
fi
export TOOL_CALL_PARSER

run_polyglot_remaining_eval() {
    PYTHONUNBUFFERED=1 "${PYTHON_BIN}" - <<'PY'
import json
import os
import sys
from pathlib import Path


def load_json(path: Path):
    with path.open(encoding="utf-8") as f:
        return json.load(f)


def dedupe(values):
    seen = set()
    out = []
    for value in values:
        if value not in seen:
            seen.add(value)
            out.append(value)
    return out


def load_last_hgm_snapshot(path: Path):
    if not path.exists():
        return {}
    text = path.read_text(encoding="utf-8")
    decoder = json.JSONDecoder()
    idx = 0
    last = {}
    while idx < len(text):
        while idx < len(text) and text[idx].isspace():
            idx += 1
        if idx >= len(text):
            break
        obj, idx = decoder.raw_decode(text, idx)
        last = obj
    return last


def split_ids(raw: str):
    tokens = raw.replace(",", " ").replace(":", " ").split()
    return [token for token in tokens if token]


def existing_statuses(meta, universe):
    perf = meta.get("overall_performance") or {}
    allowed = set(universe)
    statuses = {}
    for task_id in perf.get("total_submitted_ids") or []:
        if task_id in allowed:
            statuses[task_id] = "error"
    for task_id in perf.get("total_unresolved_ids") or []:
        if task_id in allowed:
            statuses[task_id] = "unresolved"
    for task_id in perf.get("total_emptypatch_ids") or []:
        if task_id in allowed:
            statuses[task_id] = "empty"
    for task_id in perf.get("total_resolved_ids") or []:
        if task_id in allowed:
            statuses[task_id] = "resolved"
    return statuses


def recompute_unique_performance(node_dir: Path, commit_id: str, universe, meta):
    statuses = existing_statuses(meta, universe)
    pred_dir = node_dir / "predictions" / f"{commit_id}_0"
    for task_id in universe:
        result_path = pred_dir / f"{task_id}.json"
        if not result_path.exists():
            continue
        try:
            result = load_json(result_path)
        except Exception:
            statuses[task_id] = "error"
            continue
        eval_result = result.get("eval_result")
        if eval_result == "resolved":
            statuses[task_id] = "resolved"
        elif eval_result == "empty_patch":
            statuses[task_id] = "empty"
        elif eval_result == "unresolved":
            statuses[task_id] = "unresolved"
        else:
            statuses[task_id] = "error"

    submitted = [task_id for task_id in universe if task_id in statuses]
    resolved = [task_id for task_id in submitted if statuses[task_id] == "resolved"]
    unresolved = [
        task_id for task_id in submitted if statuses[task_id] == "unresolved"
    ]
    empty = [task_id for task_id in submitted if statuses[task_id] == "empty"]
    error = [task_id for task_id in submitted if statuses[task_id] == "error"]

    report_files = []
    for path in sorted(node_dir.glob(f"{commit_id} *.json")):
        try:
            data = load_json(path)
        except Exception:
            continue
        if isinstance(data, dict) and "submitted_ids" in data:
            report_files.append(str(path.relative_to(node_dir)))

    total_submitted = len(submitted)
    return {
        "accuracy_score": len(resolved) / total_submitted if total_submitted else 0,
        "total_resolved_instances": len(resolved),
        "total_submitted_instances": total_submitted,
        "files": report_files,
        "total_unresolved_ids": unresolved,
        "total_emptypatch_ids": empty,
        "total_resolved_ids": resolved,
        "total_submitted_ids": submitted,
        "total_error_ids": error,
    }


repo_root = Path.cwd()
output_dir = Path(os.environ["HGM_OUTPUT_DIR"])
if not output_dir.is_absolute():
    output_dir = (repo_root / output_dir).resolve()

small_path = Path(os.environ["EVAL_SMALL_JSON"])
medium_path = Path(os.environ["EVAL_MEDIUM_JSON"])
init_src = Path(os.environ["EVAL_INIT_AGENT_SRC"])
if not small_path.is_absolute():
    small_path = repo_root / small_path
if not medium_path.is_absolute():
    medium_path = repo_root / medium_path
if not init_src.is_absolute():
    init_src = repo_root / init_src

universe = dedupe(load_json(small_path) + load_json(medium_path))
snapshot = load_last_hgm_snapshot(output_dir / "hgm_metadata.jsonl")
id_to_commit = {
    str(node.get("id")): node.get("commit_id")
    for node in snapshot.get("nodes", [])
    if node.get("id") is not None and node.get("commit_id")
}

requested = split_ids(os.environ["EVAL_NODE_IDS"])
resolved_nodes = []
for token in requested:
    commit_id = id_to_commit.get(token, token)
    meta_path = output_dir / commit_id / "metadata.json"
    if not meta_path.exists():
        raise SystemExit(
            f"Cannot resolve EVAL_NODE_IDS token {token!r}; missing {meta_path}"
        )
    resolved_nodes.append((token, commit_id))

print(f"Polyglot universe small+medium deduped tasks: {len(universe)}")
print(f"HGM_OUTPUT_DIR={output_dir}")
print("Requested nodes:")
for token, commit_id in resolved_nodes:
    meta = load_json(output_dir / commit_id / "metadata.json")
    perf = meta.get("overall_performance") or {}
    submitted = set(perf.get("total_submitted_ids") or [])
    remaining = [task_id for task_id in universe if task_id not in submitted]
    print(
        f"  {token} -> {commit_id}: submitted={len(submitted)} "
        f"resolved={perf.get('total_resolved_instances', 0)} "
        f"remaining={len(remaining)}"
    )
    for task_id in remaining:
        print(f"    {task_id}")

if os.environ.get("EVAL_DRY_RUN", "0") == "1":
    print("EVAL_DRY_RUN=1, not starting Docker/vLLM evaluation.")
    sys.exit(0)

if not init_src.is_dir():
    raise SystemExit(f"EVAL_INIT_AGENT_SRC not found: {init_src}")

import polyglot.harness as polyglot_harness_module
from polyglot.harness import harness as polyglot_harness
from utils.evo_utils import get_model_patch_paths

polyglot_harness_module.llm = os.environ["HGM_LLM_MODEL_ID"]
polyglot_harness_module.timeout = int(os.environ["EVAL_TIMEOUT"])

hgm_dir_rel = os.path.relpath(output_dir, repo_root)
max_workers = max(1, int(os.environ["EVAL_MAX_WORKERS"]))

for token, commit_id in resolved_nodes:
    node_dir = output_dir / commit_id
    meta_path = node_dir / "metadata.json"
    meta = load_json(meta_path)
    perf = meta.get("overall_performance") or {}
    submitted = set(perf.get("total_submitted_ids") or [])
    remaining = [task_id for task_id in universe if task_id not in submitted]

    if remaining:
        print(
            f"Starting remaining eval for node {token} ({commit_id}) on "
            f"{len(remaining)} tasks with max_workers={min(max_workers, len(remaining))}"
        )
        patch_paths = get_model_patch_paths(str(repo_root), hgm_dir_rel, commit_id)
        polyglot_harness(
            test_task_list=remaining,
            max_workers=min(max_workers, len(remaining)),
            model_name_or_path=commit_id,
            model_patch_paths=patch_paths,
            pred_dname=str(node_dir / "predictions"),
            output_dir=node_dir,
            skip_existing=True,
            init_agent_path=str(init_src),
        )
    else:
        print(f"Node {token} ({commit_id}) has no remaining tasks.")

    meta = load_json(meta_path)
    meta["overall_performance"] = recompute_unique_performance(
        node_dir, commit_id, universe, meta
    )
    with meta_path.open("w", encoding="utf-8") as f:
        json.dump(meta, f, indent=4)
    final_perf = meta["overall_performance"]
    print(
        f"Updated node {token} ({commit_id}): "
        f"{final_perf['total_resolved_instances']}/"
        f"{final_perf['total_submitted_instances']} "
        f"accuracy={final_perf['accuracy_score']:.6f}"
    )

print("Final ranking among requested nodes:")
ranking = []
for token, commit_id in resolved_nodes:
    perf = load_json(output_dir / commit_id / "metadata.json")["overall_performance"]
    ranking.append(
        (
            float(perf.get("accuracy_score") or 0),
            int(perf.get("total_resolved_instances") or 0),
            int(perf.get("total_submitted_instances") or 0),
            token,
            commit_id,
        )
    )
for score, resolved, submitted, token, commit_id in sorted(ranking, reverse=True):
    print(f"  node {token} ({commit_id}): {resolved}/{submitted} = {score:.6f}")
PY
}

if [ "${EVAL_DRY_RUN:-0}" = "1" ]; then
    run_polyglot_remaining_eval
    exit 0
fi

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

REPO_ROOT_CUDA="${SLURM_SUBMIT_DIR:-$PWD}"
# shellcheck source=/dev/null
. "${REPO_ROOT_CUDA}/swe_scripts/cuda_and_tensor_parallel.inc.sh"
cuda_and_tensor_parallel_setup
POLYGLOT_VISIBLE_GPUS="$(printf '%s\n' "${CUDA_VISIBLE_DEVICES}" | tr ',' '\n' | grep -c '.')"
if [ "${POLYGLOT_VISIBLE_GPUS}" -ne 4 ]; then
    echo "ERROR: Polyglot evaluation must use exactly 4 GPUs; got CUDA_VISIBLE_DEVICES=${CUDA_VISIBLE_DEVICES}" >&2
    exit 1
fi
if [ "${TENSOR_PARALLEL_SIZE}" -gt 4 ]; then
    echo "ERROR: TENSOR_PARALLEL_SIZE must be <= 4 for Polyglot jobs; got ${TENSOR_PARALLEL_SIZE}" >&2
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

    for _ in $(seq 1 15); do
        if [ -S "${REMOTE_DOCKER_SOCKET}" ]; then break; fi
        sleep 1
    done
    if [ ! -S "${REMOTE_DOCKER_SOCKET}" ]; then
        echo "ERROR: SSH tunnel failed; ${REMOTE_DOCKER_SOCKET} not created" >&2
        exit 1
    fi

    export DOCKER_HOST="unix://${REMOTE_DOCKER_SOCKET}"
    echo "Remote Docker tunnel ready (pid=${SSH_TUNNEL_PID})."
    echo "vLLM reverse tunnel: VM:${REMOTE_VLLM_PORT} -> localhost:${VLLM_PORT}"
fi

echo "Starting vLLM server..."

JOB_ID="${SLURM_JOB_ID:-manual}"
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
    > "logs/vllm_eval_polyglot_${JOB_ID}.stdout.log" 2> "logs/vllm_eval_polyglot_${JOB_ID}.stderr.log" &
VLLM_PID=$!

VLLM_WAIT=0
VLLM_MAX_WAIT=600
until "${PYTHON_BIN}" -c "import sys, urllib.request; urllib.request.urlopen('http://${VLLM_HOST}:${VLLM_PORT}/v1/models', timeout=2); sys.exit(0)" >/dev/null 2>&1; do
    if ! kill -0 "${VLLM_PID}" 2>/dev/null; then
        echo "ERROR: vLLM process (pid=${VLLM_PID}) died. Check logs/vllm_eval_polyglot_${JOB_ID}.stderr.log" >&2
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
        for _ in $(seq 1 10); do
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

echo "Starting Polyglot remaining evaluation."
echo "  HGM_OUTPUT_DIR=${HGM_OUTPUT_DIR}"
echo "  EVAL_NODE_IDS=${EVAL_NODE_IDS}"
echo "  EVAL_MAX_WORKERS=${EVAL_MAX_WORKERS}"
echo "  EVAL_TIMEOUT=${EVAL_TIMEOUT}"
echo "  DOCKER_API_TIMEOUT=${DOCKER_API_TIMEOUT}"
echo "  model=${HGM_LLM_MODEL_ID}"

run_polyglot_remaining_eval

echo "eval_remaining_polyglot.slurm finished."
