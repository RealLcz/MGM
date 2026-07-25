#!/bin/bash
#SBATCH --job-name=MGM-PG
#SBATCH --nodes=1
#SBATCH --ntasks=1
#SBATCH --cpus-per-task=16
#SBATCH --mem=256G
#SBATCH --gres=gpu:4
#SBATCH --time=168:00:00
#SBATCH --output=logs/mgm_polyglot_%j.out
#SBATCH --error=logs/mgm_polyglot_%j.err
#
# MGM on the Polyglot benchmark, using the same startup path as
# polyglot_scripts/hgm_polyglot.slurm.
#
# Submit from the repository root:
#   sbatch polyglot_scripts/mgm_polyglot.slurm
#
# Continue from a previous Polyglot HGM/MGM run:
#   CONTINUE_FROM=output_polyglot/20260503_123456 HGM_OUTPUT_DIR=output_polyglot/mgm_20260503_123456 sbatch polyglot_scripts/mgm_polyglot.slurm
#
# Debug-only GPU override; normal submissions should use --gres=gpu:4 and let Slurm choose:
#   sbatch --export=ALL,POLYGLOT_CUDA_VISIBLE_DEVICES=4,5,6,7 polyglot_scripts/mgm_polyglot.slurm

set -euo pipefail
cd "${SLURM_SUBMIT_DIR:-$PWD}"

export POLYGLOT_RUN_MODE="${POLYGLOT_RUN_MODE:-MGM}"
export SELF_IMPROVE_WEIGHT_A="${SELF_IMPROVE_WEIGHT_A:-0.1}"
export SELF_IMPROVE_WEIGHT_B="${SELF_IMPROVE_WEIGHT_B:-0.45}"
export SELF_IMPROVE_WEIGHT_C="${SELF_IMPROVE_WEIGHT_C:-0.45}"
export FAILED_POOL_BOOST="${FAILED_POOL_BOOST:-1.0}"
export HGM_CONFIG="${HGM_CONFIG:-polyglot_scripts/config_polyglot.yaml}"

exec bash polyglot_scripts/hgm_polyglot.slurm
