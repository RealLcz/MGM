#!/bin/bash
#SBATCH --job-name=MGM-DS
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=128G
#SBATCH --time=96:00:00
#SBATCH --output=logs/hgm_deepseek_%j.out
#SBATCH --error=logs/hgm_deepseek_%j.err

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"
mkdir -p logs

eval "$(conda shell.bash hook)"
conda activate HGM
export PYTHON_BIN="${PYTHON_BIN:-$HOME/mm/python311/bin/python}"
"${PYTHON_BIN}" -V

export HGM_MAX_WORKERS="${HGM_MAX_WORKERS:-2}"
export HGM_LLM_MODEL_ID="${HGM_LLM_MODEL_ID:-deepseek/deepseek-chat-v3.1}"

if [ -z "${OpenRouter_API_KEY:-}" ]; then
    echo "OpenRouter_API_KEY is not set."
    echo "Please export OpenRouter_API_KEY before sbatch."
    exit 1
fi

"${PYTHON_BIN}" hgm.py \
    --max_workers "${HGM_MAX_WORKERS}" \
    --self_improve_llm "${HGM_LLM_MODEL_ID}" \
    --downstream_llm "${HGM_LLM_MODEL_ID}" \
    --diagnose_llm "${HGM_LLM_MODEL_ID}"
