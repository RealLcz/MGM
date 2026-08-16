# Shared SWE-bench Verified-60 grading dataset (must match eval_initial_agent.slurm).
# Verified-60 task IDs come from swe_bench/subsets/{small,medium}.json; grading uses the
# full SWE-bench test split so results are comparable to the successful initial Apptainer run.
SWE_EVAL_DATASET="${SWE_EVAL_DATASET:-princeton-nlp/SWE-bench}"
export SWE_EVAL_DATASET
