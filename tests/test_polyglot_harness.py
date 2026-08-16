import os
import sys
import tempfile
import types
from pathlib import Path


def _install_harness_import_stubs():
    sys.modules.setdefault("git", types.ModuleType("git"))

    docker_build = types.ModuleType("polyglot.docker_build")
    docker_build.build_container = None
    docker_build.build_env_images = None
    docker_build.cleanup_container = None
    sys.modules["polyglot.docker_build"] = docker_build

    test_spec = types.ModuleType("polyglot.test_spec")
    test_spec.make_test_spec = None
    sys.modules["polyglot.test_spec"] = test_spec

    prompt = types.ModuleType("prompts.testrepo_prompt")
    prompt.get_test_description = lambda *args, **kwargs: ""
    sys.modules["prompts.testrepo_prompt"] = prompt

    swe_utils = types.ModuleType("swe_bench.utils")
    for name in (
        "copy_from_container",
        "copy_to_container",
        "log_container_output",
        "remove_existing_container",
        "safe_log",
        "setup_logger",
    ):
        setattr(swe_utils, name, lambda *args, **kwargs: None)
    sys.modules["swe_bench.utils"] = swe_utils

    docker_utils = types.ModuleType("utils.docker_utils")
    docker_utils.docker_from_env = lambda: None
    sys.modules["utils.docker_utils"] = docker_utils

    llm = types.ModuleType("llm")
    llm.DEFAULT_LLM_MODEL = "test-model"
    llm.llm_container_env = lambda: {}
    llm.uses_vllm_model = lambda model: False
    sys.modules["llm"] = llm


def test_sanitize_agent_workspace_command_hides_hidden_sources():
    _install_harness_import_stubs()
    from polyglot.harness import get_sanitize_agent_workspace_cmd

    cmd = get_sanitize_agent_workspace_cmd()

    assert "rm -rf /polyglot" in cmd
    assert "Refusing unsafe eval" in cmd
    assert "set -eu" in cmd
    assert "set -eux" not in cmd
    assert "rm -rf .git" in cmd
    assert "git init -q" in cmd
    assert "git rev-parse HEAD" in cmd


def test_extract_commit_hash_ignores_shell_trace():
    _install_harness_import_stubs()
    from polyglot.harness import extract_commit_hash

    output = b"\n1380057508e27086242bd5db78096da63b032634\n+ git rev-parse HEAD\n"

    assert extract_commit_hash(output) == "1380057508e27086242bd5db78096da63b032634"


def test_result_record_adds_stable_status_fields():
    _install_harness_import_stubs()
    from polyglot.harness import result_record

    resolved = result_record("task", "model", "diff", [], "resolved", True)
    empty = result_record("task", "model", "", [], "empty_patch", True)

    assert resolved["resolved"] is True
    assert resolved["empty_patch"] is False
    assert empty["resolved"] is False
    assert empty["empty_patch"] is True
    assert resolved["schema_version"] == 3


def test_runtime_env_exposes_testbed_python_and_host_rust():
    _install_harness_import_stubs()
    import polyglot.harness as harness

    old_which = harness.shutil.which
    old_env = {
        key: os.environ.get(key)
        for key in ("HOME", "CARGO_HOME", "RUSTUP_HOME")
    }
    try:
        harness.shutil.which = lambda name: "/host/.cargo/bin/cargo"
        os.environ["HOME"] = "/host"
        os.environ.pop("CARGO_HOME", None)
        os.environ.pop("RUSTUP_HOME", None)

        env = harness.polyglot_runtime_env(
            {"instance_id": "rust__accumulate", "language": "rust"}
        )
        setup = harness.polyglot_runtime_setup_cmd(
            {"instance_id": "rust__accumulate", "language": "rust"}
        )
    finally:
        harness.shutil.which = old_which
        for key, value in old_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    assert env["PATH"].split(":")[0] == "/opt/miniconda3/envs/testbed/bin"
    assert "/host/.cargo/bin" in env["PATH"].split(":")
    assert env["CARGO_HOME"] == "/host/.cargo"
    assert env["RUSTUP_HOME"] == "/host/.rustup"
    assert "export CARGO_HOME=/host/.cargo" in setup
    assert "export RUSTUP_HOME=/host/.rustup" in setup


def test_load_reusable_model_patch_skips_empty_patch():
    _install_harness_import_stubs()
    from polyglot.harness import load_reusable_model_patch

    with tempfile.TemporaryDirectory() as dname:
        tmp_path = Path(dname)
        (tmp_path / "python__pov.json").write_text(
            '{"model_patch":"diff --git a/a b/a\\n","proposed_model_patches":["x"]}'
        )
        (tmp_path / "rust__accumulate.json").write_text('{"model_patch":""}')

        reused = load_reusable_model_patch(tmp_path, "python__pov")

        assert reused["model_patch"].startswith("diff --git")
        assert reused["proposed_model_patches"] == ["x"]
        assert load_reusable_model_patch(tmp_path, "rust__accumulate") is None


def test_test_suite_task_uses_test_file_and_hides_reference_answer():
    _install_harness_import_stubs()
    from polyglot.harness import (
        hidden_test_patch_for_eval,
        is_test_suite_task,
        model_patch_target_files,
        test_suite_scaffold_patch,
    )

    entry = {
        "instance_id": "go__counter",
        "problem_statement": "Design a test suite for a counter.",
        "files": {
            "solution": ["counter.go"],
            "test": ["counter_test.go"],
            "example": [".meta/example.go"],
        },
        "test_patch": "\n".join(
            [
                "diff --git a/.meta/example.go b/.meta/example.go",
                "--- /dev/null",
                "+++ b/.meta/example.go",
                "@@ -0,0 +1 @@",
                "+reference answer",
                "diff --git a/counter_test.go b/counter_test.go",
                "--- /dev/null",
                "+++ b/counter_test.go",
                "@@ -0,0 +1 @@",
                "+package counter",
                "diff --git a/maker.go b/maker.go",
                "--- /dev/null",
                "+++ b/maker.go",
                "@@ -0,0 +1 @@",
                "+package counter",
            ]
        ),
    }

    assert is_test_suite_task(entry) is True
    assert model_patch_target_files(entry) == ["counter_test.go"]
    scaffold = test_suite_scaffold_patch(entry)
    eval_patch = hidden_test_patch_for_eval(entry)
    assert ".meta/example.go" not in scaffold
    assert "counter_test.go" in scaffold
    assert "maker.go" in scaffold
    assert eval_patch == ""
