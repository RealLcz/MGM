# SWE-bench Pro Local Tools

This directory keeps the SWE-bench Pro support code separate from the existing
SWE-bench Verified and Polyglot harnesses.

## Images

Official SWE-bench Pro images are on Docker Hub:

```text
jefzda/sweap-images:<dockerhub_tag>
```

The `dockerhub_tag` comes from the HuggingFace dataset
`ScaleAI/SWE-bench_Pro`.

Pull this subset to the Tencent Cloud Docker daemon:

```bash
PYTHON_BIN="$HOME/mm/python311/bin/python" \
bash SWEbench_Pro/pull_to_tencent.sh
```

Useful options:

```bash
# Preview instance -> image mapping only.
python SWEbench_Pro/pull_images.py --print-only

# Pull with an explicit platform.
bash SWEbench_Pro/pull_to_tencent.sh --platform linux/amd64

# If SSH needs password auth and sshpass is installed:
REMOTE_DOCKER_PASSWORD='...' bash SWEbench_Pro/pull_to_tencent.sh
```

## Smoke Test

Check that the first few pulled images can start:

```bash
python SWEbench_Pro/smoke_test_images.py \
  --remote-host 43.131.5.182 \
  --limit 3
```

## Evaluation

Fetch the official run scripts/dockerfiles and generate local subset inputs:

```bash
bash SWEbench_Pro/sync_official_eval_assets.sh
```

Run the official gold patches through the remote Docker daemon:

```bash
bash SWEbench_Pro/eval_gold_on_tencent.sh --limit 1
```

Remove `--limit 1` to run the full 60-instance subset.

To evaluate model predictions, prepare a JSON file like:

```json
[
  {
    "instance_id": "instance_...",
    "patch": "diff --git ...",
    "prefix": "my_model"
  }
]
```

Then run:

```bash
python SWEbench_Pro/evaluate_patches_remote.py \
  --patch-path /path/to/predictions.json \
  --output-dir SWEbench_Pro/outputs/my_model_eval \
  --remote-host 43.131.5.182
```

Unlike the upstream local-Docker evaluator, `evaluate_patches_remote.py` copies
workspace files into containers through the Docker API. That means it works when
`DOCKER_HOST` points at the Tencent Cloud daemon through an SSH socket tunnel.

## Agent Evaluation

Run one selected agent:

```bash
conda activate HGM

python SWEbench_Pro/run_agent_eval.py \
  --agent initial \
  --remote-host 43.131.5.182 \
  --limit 1

python SWEbench_Pro/run_agent_eval.py \
  --agent best \
  --hgm-output-dir output_mgm \
  --remote-host 43.131.5.182 \
  --limit 1
```

By default, missing Docker images are pulled on demand. To force a pure
offline/already-pulled run, pass `--pull never`. To pre-pull all 60 images before
agent evaluation, run `bash SWEbench_Pro/pull_to_tencent.sh` first.

## Slurm

Cluster run with vLLM on the Slurm node and Docker on Tencent Cloud. This is a
generic single-agent template: choose the agent with `SWE_PRO_AGENT_MODE`.

```bash
sbatch --export=ALL,SWE_PRO_AGENT_MODE=best SWEbench_Pro/eval_mgm_pro.slurm
```

Smoke test one task:

```bash
sbatch --export=ALL,SWE_PRO_AGENT_MODE=best,SWE_PRO_LIMIT=1 SWEbench_Pro/eval_mgm_pro.slurm
```

Run the initial agent:

```bash
sbatch --export=ALL,SWE_PRO_AGENT_MODE=initial SWEbench_Pro/eval_mgm_pro.slurm
```

Run the Polyglot initial agent with `Qwen/Qwen3.6-35B-A3B`, 112k context,
and 3 workers:

```bash
sbatch --gres=gpu:4 --export=ALL,SWE_PRO_AGENT_MODE=initial,SWE_PRO_INIT_AGENT_SRC=initial_polyglot/default_agent/src,VLLM_MODEL_NAME=Qwen/Qwen3.6-35B-A3B,SERVED_MODEL_NAME=Qwen/Qwen3.6-35B-A3B,HGM_LLM_MODEL_ID=Qwen/Qwen3.6-35B-A3B,TENSOR_PARALLEL_SIZE=4,GPU_MEMORY_UTILIZATION=0.92,VLLM_MAX_MODEL_LEN=112000,SWE_PRO_MAX_WORKERS=3,SWE_PRO_OUTPUT_DIR=SWEbench_Pro/outputs/polyglot_initial_qwen3_6_35b_a3b SWEbench_Pro/eval_mgm_pro.slurm
```

The Slurm template installs copied agent dependencies with
`SWE_PRO_AGENT_PIP_INDEX_URL`; it defaults to the Tencent PyPI mirror so the
agent install does not inherit SWE-bench Pro images' `pypi-timemachine` index.
It also uses `SWEbench_Pro/agent_constraints.txt` to keep heavy Python packages
on wheels that old task-image Python/pip versions can install.
If you are rerunning after a failed dependency install, add `SWE_PRO_REDO=1`.

Run the current best MGM node:

```bash
sbatch --export=ALL,SWE_PRO_AGENT_MODE=best SWEbench_Pro/eval_mgm_pro.slurm
```

Run a specific node:

```bash
sbatch --export=ALL,SWE_PRO_AGENT_MODE=node,SWE_PRO_NODE_ID=20260413_113039_681369 SWEbench_Pro/eval_mgm_pro.slurm
```

Pre-pull all 60 images before evaluating:

```bash
sbatch --export=ALL,SWE_PRO_AGENT_MODE=best,SWE_PRO_PREPULL=1 SWEbench_Pro/eval_mgm_pro.slurm
```

The Slurm logs go under `SWEbench_Pro/logs/`; task results go under
`SWEbench_Pro/outputs/`.
