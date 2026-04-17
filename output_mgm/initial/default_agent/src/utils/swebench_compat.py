import os
from functools import cache

import requests

try:
    from swebench.harness.test_spec import TestSpec, make_test_spec
except ImportError:
    from swebench.harness.test_spec.test_spec import TestSpec, make_test_spec

from swebench.harness.constants import APPLY_PATCH_FAIL, APPLY_PATCH_PASS, KEY_INSTANCE_ID

try:
    from swebench.harness.constants import INSTANCE_IMAGE_BUILD_DIR, RUN_EVALUATION_LOG_DIR
except ImportError:
    from pathlib import Path

    INSTANCE_IMAGE_BUILD_DIR = Path("logs/build_images/instances")
    RUN_EVALUATION_LOG_DIR = Path("logs/run_evaluation")

from swebench.harness.grading import get_eval_report
from swebench.harness.utils import load_swebench_dataset, str2bool

try:
    from swebench.harness.utils import get_environment_yml, get_requirements
except ImportError:
    _SWE_BENCH_URL_RAW = "https://raw.githubusercontent.com/"

    try:
        from swebench.harness.constants import MAP_REPO_TO_ENV_YML_PATHS, MAP_REPO_TO_REQS_PATHS
    except ImportError:
        MAP_REPO_TO_ENV_YML_PATHS = {}
        MAP_REPO_TO_REQS_PATHS = {}

    @cache
    def _get_env_yml_by_commit(repo: str, commit: str, env_name: str) -> str:
        for req_path in MAP_REPO_TO_ENV_YML_PATHS.get(repo, ["environment.yml"]):
            url = os.path.join(_SWE_BENCH_URL_RAW, repo, commit, req_path)
            resp = requests.get(url)
            if resp.status_code == 200:
                lines = resp.text.split("\n")
                cleaned = []
                for line in lines:
                    if line.startswith("name:"):
                        cleaned.append(f"name: {env_name}")
                    else:
                        cleaned.append(line)
                return "\n".join(cleaned)
        raise ValueError(f"Could not find environment.yml for {repo}@{commit}")

    def get_environment_yml(instance, env_name: str) -> str:
        commit = instance.get("environment_setup_commit", instance.get("base_commit"))
        return _get_env_yml_by_commit(instance["repo"], commit, env_name)

    @cache
    def _get_reqs_by_commit(repo: str, commit: str) -> str:
        for req_path in MAP_REPO_TO_REQS_PATHS.get(repo, ["requirements.txt"]):
            url = os.path.join(_SWE_BENCH_URL_RAW, repo, commit, req_path)
            resp = requests.get(url)
            if resp.status_code == 200:
                return "\n".join(
                    line
                    for line in resp.text.split("\n")
                    if not line.strip().startswith(("-e .", "#", ".[test"))
                )
        raise ValueError(f"Could not find requirements.txt for {repo}@{commit}")

    def get_requirements(instance) -> str:
        commit = instance.get("environment_setup_commit", instance.get("base_commit"))
        return _get_reqs_by_commit(instance["repo"], commit)
