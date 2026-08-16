import inspect
import os
import re
from functools import cache
from typing import Any

import requests

try:
    from swebench.harness.test_spec import TestSpec, make_test_spec
except ImportError:
    from swebench.harness.test_spec.test_spec import TestSpec, make_test_spec

from swebench.harness.constants import APPLY_PATCH_FAIL, APPLY_PATCH_PASS, KEY_INSTANCE_ID


# --- Pin a Python<3.10-compatible pip in generated env build scripts ----------
#
# Some SWE-bench environments create a `python=3.9` (or older) conda env and then
# run `pip install ...`. conda pulls the newest pip available, and pip >= 25.1
# uses `@dataclass(slots=True)`, a parameter that only exists on Python 3.10+.
# On a 3.9 env pip then fails to even import with:
#   TypeError: dataclass() got an unexpected keyword argument 'slots'
# which makes the whole environment image build fail (non-zero exit 1).
#
# A broken pip cannot fix itself (`python -m pip install ...` also crashes), so
# we pin pip during `conda create` instead. We patch `make_env_script_list` at
# the swebench level so every consumer -- including the internal calls made by
# `build_env_images` -- produces the same (pinned) script and the same image
# hash. Only sub-3.10 envs are touched, keeping the image-cache churn minimal.
_PIP_PIN_SPEC = os.getenv("SWE_PIP_PIN", "pip<25.1")
_CONDA_CREATE_PY = re.compile(r"(conda create\b[^\n]*?python=3\.(\d+))")


def _pin_pip_in_env_lines(lines):
    patched = []
    for line in lines:
        match = _CONDA_CREATE_PY.search(line)
        if (
            match
            and int(match.group(2)) < 10
            and "pip<" not in line
            and "pip=" not in line
            and "pip>" not in line
        ):
            line = _CONDA_CREATE_PY.sub(
                rf'\1 "{_PIP_PIN_SPEC}"', line, count=1
            )
        patched.append(line)
    return patched


def _install_pip_pin_patch():
    try:
        from swebench.harness.test_spec import create_scripts as _create_scripts
    except ImportError:  # pragma: no cover - layout fallback
        return

    if getattr(_create_scripts.make_env_script_list, "_pip_pin_patched", False):
        return

    _orig_make_env = _create_scripts.make_env_script_list

    def _patched_make_env_script_list(*args, **kwargs):
        return _pin_pip_in_env_lines(_orig_make_env(*args, **kwargs))

    _patched_make_env_script_list._pip_pin_patched = True
    _create_scripts.make_env_script_list = _patched_make_env_script_list

    # `test_spec.py` does `from ... import make_env_script_list`, binding the
    # name into its own module namespace, so patch that reference too.
    try:
        from swebench.harness.test_spec import test_spec as _ts_mod

        _ts_mod.make_env_script_list = _patched_make_env_script_list
    except ImportError:  # pragma: no cover - layout fallback
        pass


_install_pip_pin_patch()

try:
    from swebench.harness.constants import INSTANCE_IMAGE_BUILD_DIR, RUN_EVALUATION_LOG_DIR
except ImportError:
    from pathlib import Path

    INSTANCE_IMAGE_BUILD_DIR = Path("logs/build_images/instances")
    RUN_EVALUATION_LOG_DIR = Path("logs/run_evaluation")

from swebench.harness.grading import get_eval_report as _get_eval_report_impl
from swebench.harness.utils import load_swebench_dataset, str2bool

_EVAL_REPORT_PARAMS = set(inspect.signature(_get_eval_report_impl).parameters)


def get_eval_report(
    *,
    test_spec: TestSpec,
    prediction: dict[str, str],
    log_path: str | os.PathLike[str] | None = None,
    test_log_path: str | os.PathLike[str] | None = None,
    include_tests_status: bool = True,
) -> dict[str, Any]:
    """Call swebench grading across API renames (log_path -> test_log_path in 4.x)."""
    path = test_log_path if test_log_path is not None else log_path
    if path is None:
        raise TypeError("get_eval_report() requires test_log_path or log_path")
    path_str = os.fspath(path)
    kwargs: dict[str, Any] = {
        "test_spec": test_spec,
        "prediction": prediction,
        "include_tests_status": include_tests_status,
    }
    if "test_log_path" in _EVAL_REPORT_PARAMS:
        kwargs["test_log_path"] = path_str
    elif "log_path" in _EVAL_REPORT_PARAMS:
        kwargs["log_path"] = path_str
    else:
        raise RuntimeError("Unsupported swebench get_eval_report signature")
    return _get_eval_report_impl(**kwargs)

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
