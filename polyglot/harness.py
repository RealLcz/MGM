# This file is adapted from https://github.com/jennyzzt/dgm.

from __future__ import annotations

import argparse
import datetime
import json
import os
import re
import shlex
import shutil
import tempfile
from concurrent.futures import (ProcessPoolExecutor, ThreadPoolExecutor,
                                as_completed)
from enum import Enum
from pathlib import Path

from polyglot.constants import MAP_REPO_VERSION_TO_SPECS, TEST_COMMANDS
from polyglot.docker_build import (build_container, build_env_images,
                                   cleanup_container)
from polyglot.test_spec import make_test_spec
from prompts.testrepo_prompt import get_test_description
from swe_bench.utils import (copy_from_container, copy_to_container,
                             log_container_output, remove_existing_container,
                             safe_log, setup_logger)
from utils.docker_utils import docker_from_env
from utils.git_utils import remove_patch_by_files

from llm import DEFAULT_LLM_MODEL, llm_container_env, uses_vllm_model

llm = DEFAULT_LLM_MODEL
timeout = 1800  # seconds
CONTAINER_TOOLCHAIN_PATH = os.environ.get(
    "POLYGLOT_CONTAINER_PATH",
    "/opt/miniconda3/envs/testbed/bin:/usr/local/go/bin:/root/.cargo/bin:"
    "/usr/local/cargo/bin:/opt/miniconda3/bin:/usr/local/sbin:/usr/local/bin:"
    "/usr/sbin:/usr/bin:/sbin:/bin",
)
AGENT_WORKSPACE_SANITIZE_ENV = "POLYGLOT_SANITIZE_AGENT_WORKSPACE"


def resolve_host_agent_python() -> str | None:
    """Host-built agent venv Python, auto-mounted into Apptainer/Docker containers."""
    for key in ("POLYGLOT_HOST_AGENT_PYTHON", "SWE_ML_HOST_AGENT_PYTHON"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None


def _task_cache_name(entry) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", entry["instance_id"])


def _join_path_entries(entries) -> str:
    seen = set()
    kept = []
    for entry in entries:
        if not entry:
            continue
        for part in str(entry).split(":"):
            part = part.strip()
            if part and part not in seen:
                seen.add(part)
                kept.append(part)
    return ":".join(kept)


def host_rust_env() -> dict[str, str]:
    cargo_bin = shutil.which("cargo")
    cargo_home = os.environ.get("CARGO_HOME")
    rustup_home = os.environ.get("RUSTUP_HOME")
    home = os.environ.get("HOME")

    if cargo_bin and not cargo_home:
        cargo_home = str(Path(cargo_bin).resolve().parent.parent)
    if home:
        cargo_home = cargo_home or str(Path(home) / ".cargo")
        rustup_home = rustup_home or str(Path(home) / ".rustup")

    env = {}
    if cargo_home:
        env["CARGO_HOME"] = cargo_home
    if rustup_home:
        env["RUSTUP_HOME"] = rustup_home
    if cargo_bin:
        env["POLYGLOT_HOST_CARGO_BIN"] = str(Path(cargo_bin).resolve().parent)
    return env


def container_path() -> str:
    rust_env = host_rust_env()
    return _join_path_entries(
        [
            CONTAINER_TOOLCHAIN_PATH,
            rust_env.get("POLYGLOT_HOST_CARGO_BIN"),
            os.environ.get("PATH", ""),
        ]
    )


def polyglot_runtime_env(entry) -> dict[str, str | None]:
    cache_root = f"/tmp/polyglot-cache/{_task_cache_name(entry)}"
    gradle_opts = " ".join(
        part
        for part in [
            os.getenv("GRADLE_OPTS", "").strip(),
            "-Dorg.gradle.daemon=false",
            "-Dorg.gradle.caching=false",
            "-Dorg.gradle.vfs.watch=false",
        ]
        if part
    )
    env_vars = llm_container_env()
    java_tool_options = " ".join(
        part
        for part in [
            os.getenv("JAVA_TOOL_OPTIONS", "").strip(),
            f"-Duser.home={cache_root}/home",
        ]
        if part
    )
    env_vars.update(
        {
            "PATH": container_path(),
            "POLYGLOT_HOME": f"{cache_root}/home",
            "GOROOT": "/usr/local/go",
            "GOPATH": f"{cache_root}/go",
            "GOCACHE": f"{cache_root}/go-build",
            "GOMODCACHE": f"{cache_root}/go-mod",
            "NPM_CONFIG_CACHE": f"{cache_root}/npm",
            "npm_config_cache": f"{cache_root}/npm",
            "GRADLE_USER_HOME": f"{cache_root}/gradle",
            "POLYGLOT_GRADLE_PROJECT_CACHE": f"{cache_root}/gradle-project",
            "GRADLE_OPTS": gradle_opts,
            "JAVA_TOOL_OPTIONS": java_tool_options,
        }
    )
    env_vars.update(host_rust_env())
    return env_vars


def polyglot_runtime_setup_cmd(entry, clean_testbed: bool = False) -> str:
    env_vars = polyglot_runtime_env(entry)

    def export_line(key: str) -> str:
        value = env_vars[key]
        return f"export {key}={shlex.quote(str(value))}"

    lines = [
        "set -e",
        export_line("PATH"),
        export_line("POLYGLOT_HOME"),
        export_line("GOROOT"),
        export_line("GOPATH"),
        export_line("GOCACHE"),
        export_line("GOMODCACHE"),
        export_line("NPM_CONFIG_CACHE"),
        export_line("npm_config_cache"),
        export_line("GRADLE_USER_HOME"),
        export_line("POLYGLOT_GRADLE_PROJECT_CACHE"),
        export_line("GRADLE_OPTS"),
        export_line("JAVA_TOOL_OPTIONS"),
        *(
            [export_line("CARGO_HOME")]
            if env_vars.get("CARGO_HOME")
            else []
        ),
        *(
            [export_line("RUSTUP_HOME")]
            if env_vars.get("RUSTUP_HOME")
            else []
        ),
        'export HOME="$POLYGLOT_HOME"',
        'mkdir -p "$HOME" "$GOPATH" "$GOCACHE" "$GOMODCACHE" '
        '"$NPM_CONFIG_CACHE" "$GRADLE_USER_HOME" "$POLYGLOT_GRADLE_PROJECT_CACHE"',
    ]
    if clean_testbed:
        lines.extend(
            [
                "if [ -d /testbed ]; then",
                "  rm -rf /testbed/build /testbed/.gradle /testbed/target "
                "/testbed/.pytest_cache /testbed/.cache /testbed/.npm",
                "  [ -L /testbed/node_modules ] && rm -f /testbed/node_modules || true",
                "  [ -L /testbed/package-lock.json ] && rm -f /testbed/package-lock.json || true",
                "  find /testbed -type d -name __pycache__ -prune -exec rm -rf {} +",
                "fi",
            ]
        )
    return "\n".join(lines)


def polyglot_preflight_cmd(entry) -> str:
    checks = {
        "go": "command -v go && go version",
        "javascript": "command -v node && node --version && command -v npm && npm --version",
        "java": "command -v java && java -version && test -x /testbed/gradlew",
        "rust": "command -v cargo && cargo --version",
        "cpp": "command -v cmake && command -v make",
        "python": (
            "command -v python && python --version && "
            "command -v pytest && pytest --version"
        ),
    }
    return "\n".join(
        [
            polyglot_runtime_setup_cmd(entry, clean_testbed=True),
            checks.get(entry["language"], "true"),
        ]
    )


def get_eval_script(commands, entry=None):
    lines = ["#!/bin/bash", "set -uxo pipefail"]
    if entry is not None:
        lines.append(polyglot_runtime_setup_cmd(entry, clean_testbed=True))
    lines.extend(commands)
    return "\n".join(lines) + "\n"


def get_apply_patch_cmd(patch_path):
    return (
        f"if [ ! -s {shlex.quote(patch_path)} ]; then "
        "true; "
        "else "
        f"git apply --whitespace=nowarn --recount {shlex.quote(patch_path)} || "
        f"patch --batch --fuzz=5 -p1 < {shlex.quote(patch_path)}; "
        "fi"
    )


def get_sanitize_agent_workspace_cmd() -> str:
    """Hide hidden tests and replace full repo history with a base-only git repo."""
    return "\n".join(
        [
            "set -eu",
            "rm -rf /polyglot || true",
            "if [ -e /polyglot ]; then",
            "  echo 'Refusing unsafe eval: /polyglot is still visible to the agent.' >&2",
            "  exit 86",
            "fi",
            "git config --global --add safe.directory /testbed || true",
            "cd /testbed",
            "rm -rf .git",
            "git init -q",
            "git config user.email polyglot-eval@example.invalid",
            "git config user.name 'Polyglot Eval'",
            "git add -A",
            "git commit -q --allow-empty -m eval-base",
            "git rev-parse HEAD",
        ]
    )


def should_sanitize_agent_workspace() -> bool:
    return os.environ.get(AGENT_WORKSPACE_SANITIZE_ENV, "1") != "0"


def result_record(
    instance_id,
    model_name_or_path,
    model_patch,
    proposed_model_patches,
    eval_result,
    success,
    error=None,
    schema_version=3,
):
    record = {
        "instance_id": instance_id,
        "model_name_or_path": model_name_or_path,
        "model_patch": model_patch,
        "proposed_model_patches": proposed_model_patches,
        "eval_result": eval_result,
        "resolved": eval_result == "resolved",
        "empty_patch": eval_result == "empty_patch",
        "success": success,
        "schema_version": schema_version,
    }
    if error is not None:
        record["error"] = str(error)
    return record


def is_test_suite_task(entry) -> bool:
    problem_statement = (entry.get("problem_statement") or "").lower()
    return (
        "design a test suite" in problem_statement
        or ".meta/.skip_tests" in (entry.get("test_patch") or "")
    )


def model_patch_target_files(entry) -> list[str]:
    file_info = entry.get("files", {})
    key = "test" if is_test_suite_task(entry) else "solution"
    target_files = file_info.get(key, [])
    if not target_files:
        raise RuntimeError(
            f"No {key} files configured for {entry['instance_id']}"
        )
    return target_files


def shell_file_args(paths: list[str]) -> str:
    return " ".join(shlex.quote(path) for path in paths)


def solution_file_args(entry) -> str:
    return shell_file_args(model_patch_target_files(entry))


def refresh_model_patch_cmd(base_commit: str, entry) -> str:
    """Diff only valid answer files; never intent-to-add build/ artifacts.

    For test-suite tasks we restrict the diff to the declared test files
    (the agent is supposed to write tests, not solution code). For normal
    solution tasks we diff the whole tree against base, because the agent
    may legitimately create helper modules or split code across new files —
    restricting to entry["files"]["solution"] would silently drop those
    edits and produce an incomplete model_patch (a cause of unresolved
    polyglot cases).
    """
    diff_path_args = ""
    if is_test_suite_task(entry):
        diff_path_args = f" -- {solution_file_args(entry)}"
    return (
        "git -C /testbed reset HEAD -- . 2>/dev/null || true; "
        "rm -rf /testbed/build /testbed/.gradle /testbed/target; "
        "[ -L /testbed/node_modules ] && rm -f /testbed/node_modules || true; "
        "[ -L /testbed/package-lock.json ] && rm -f /testbed/package-lock.json || true; "
        "printf '\\nnode_modules/\\n__pycache__/\\n*.pyc\\n.pytest_cache/\\n"
        "target/\\nbuild/\\n.gradle/\\n.npm/\\n.cache/\\ndist/\\n*.o\\n' "
        ">> /testbed/.git/info/exclude && "
        f"git -C /testbed diff --binary {base_commit}{diff_path_args} "
        "> /hgm/model_patch.diff"
    )


def extract_last_nonempty_line(output: bytes | str) -> str:
    if isinstance(output, bytes):
        text = output.decode("utf-8", errors="replace")
    else:
        text = output
    lines = [line.strip() for line in text.splitlines() if line.strip()]
    return lines[-1] if lines else ""


def extract_commit_hash(output: bytes | str) -> str:
    if isinstance(output, bytes):
        text = output.decode("utf-8", errors="replace")
    else:
        text = output
    commits = [
        line.strip()
        for line in text.splitlines()
        if re.fullmatch(r"[0-9a-fA-F]{40}", line.strip())
    ]
    return commits[-1] if commits else ""


def patch_files_from_diff(patch_str: str) -> set[str]:
    files = set()
    for line in patch_str.splitlines():
        if not line.startswith("diff --git "):
            continue
        parts = line.split()
        if len(parts) >= 4 and parts[2].startswith("a/") and parts[3].startswith("b/"):
            files.add(parts[3][2:])
    return files


def remove_patch_blocks_for_files(
    patch_str: str,
    excluded_files: set[str] | list[str] | tuple[str, ...],
    excluded_prefixes: tuple[str, ...] = (),
) -> str:
    excluded = set(excluded_files)
    lines = patch_str.splitlines(keepends=True)
    kept = []
    include_block = True

    for line in lines:
        if line.startswith("diff --git "):
            parts = line.split()
            path = ""
            if len(parts) >= 4 and parts[3].startswith("b/"):
                path = parts[3][2:]
            include_block = path not in excluded and not any(
                path.startswith(prefix) for prefix in excluded_prefixes
            )
        if include_block:
            kept.append(line)

    patch = "".join(kept)
    if patch and not patch.endswith("\n"):
        patch += "\n"
    return patch


def test_suite_scaffold_patch(entry) -> str:
    if not is_test_suite_task(entry):
        return ""
    return remove_patch_blocks_for_files(
        entry.get("test_patch") or "",
        excluded_files=[],
        excluded_prefixes=(".meta/",),
    )


def hidden_test_patch_for_eval(entry) -> str:
    patch = entry.get("test_patch") or ""
    excluded_files = set(model_patch_target_files(entry))
    if is_test_suite_task(entry):
        excluded_files.update(patch_files_from_diff(patch))
    return remove_patch_blocks_for_files(patch, excluded_files=excluded_files)


def write_patch_text_to_container(container, patch_text: str, dest_path: str, prefix: str):
    """Copy patch text through a temporary host file outside prediction outputs."""
    fd, test_patch_path = tempfile.mkstemp(
        prefix=prefix, suffix=".diff"
    )
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(patch_text)
        copy_to_container(container, test_patch_path, dest_path)
    finally:
        try:
            os.remove(test_patch_path)
        except FileNotFoundError:
            pass


def load_reusable_model_patch(reuse_patch_dir, instance_id):
    if not reuse_patch_dir:
        return None
    result_path = Path(reuse_patch_dir) / f"{instance_id}.json"
    if not result_path.exists():
        return None
    try:
        result = json.loads(result_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    model_patch = result.get("model_patch")
    if not isinstance(model_patch, str) or not model_patch.strip():
        return None
    return {
        "model_patch": model_patch,
        "proposed_model_patches": result.get("proposed_model_patches") or [],
        "source": str(result_path),
    }


def copy_test_patch_to_container(container, entry):
    write_patch_text_to_container(
        container,
        hidden_test_patch_for_eval(entry),
        "/hgm/test_patch.diff",
        f"{entry['instance_id']}.test.",
    )


def copy_scaffold_patch_to_container(container, entry):
    write_patch_text_to_container(
        container,
        test_suite_scaffold_patch(entry),
        "/hgm/scaffold_patch.diff",
        f"{entry['instance_id']}.scaffold.",
    )


def test_commands_for_entry(entry) -> list[str]:
    if entry.get("instance_id") == "go__counter":
        return [
            "set -e",
            "COUNTER_IMPL=4 go test ./...",
            "for impl in 1 2 3; do "
            "if COUNTER_IMPL=$impl go test ./... >/tmp/counter_impl_${impl}.log 2>&1; then "
            "cat /tmp/counter_impl_${impl}.log; "
            "echo \"bad counter implementation ${impl} passed the submitted tests\"; "
            "exit 1; "
            "else "
            "cat /tmp/counter_impl_${impl}.log; "
            "fi; "
            "done",
        ]
    return TEST_COMMANDS[entry["language"]]


# Continuous liveness monitoring: if the agent produces no new stdout for this
# many consecutive seconds, it is considered stalled and killed for retry.
#
# This catches two classes of bugs observed on JavaScript polyglot tasks:
#   1. Import-stage hangs: agent stuck in `import openai` / SSL / DNS init
#      inside an Apptainer container — produces zero output indefinitely.
#   2. First-API-call hangs: agent prints "Using DeepSeek API" (passing a
#      naive startup probe) but then blocks forever inside
#      `client.chat.completions.create()` because the DeepSeek API accepted
#      the request but never streamed a response (HTTP timeout=240s did not
#      fire). This produced 21 `exit code 124` errors on the 2026-07-03 run.
#
# 600s is chosen because DeepSeek API's deepseek-v4-pro can take 5+ minutes
# to respond on complex tool-call requests with reasoning. 300s was too short
# and caused false stall kills even though the API was actively processing
# (confirmed: DeepSeek platform showed ~4M tokens consumed, but 8 tasks were
# killed as stall on the 2026-07-04 run). 600s gives the API enough room
# while still catching genuine hangs.
AGENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("AGENT_IDLE_TIMEOUT_SECONDS", "600"))
AGENT_MAX_ATTEMPTS = int(os.environ.get("AGENT_MAX_ATTEMPTS", "3"))


def run_agent_with_startup_retry(
    container,
    env_vars,
    cmd,
    logger,
    entry,
    diff_base_commit,
    model_patch_paths,
    init_agent_path,
    test_description,
    instance_id,
    chat_history_file_container,
    timeout,
    client,
    run_id,
):
    """Run the coding agent, retrying when it stalls during startup.

    Some Apptainer container instances intermittently hang during Python
    import (openai/anthropic SSL or DNS init), producing zero stdout for the
    entire EVAL_TIMEOUT and yielding empty model_patch files. This wrapper
    detects that condition — agent exits with timeout AND produced no chat
    history — and retries on a fresh container after re-applying patches.

    To avoid waiting the full EVAL_TIMEOUT on each stalled attempt, the agent
    is launched via an in-container wrapper that monitors stdout activity
    continuously. If the agent produces no new output for
    AGENT_IDLE_TIMEOUT_SECONDS (e.g. 300s), it is killed and retried on a
    fresh container. This catches both import-stage hangs AND first-API-call
    hangs — the latter is the bug that produced 21 `exit code 124` errors on
    JavaScript polyglot tasks where the agent printed "Using DeepSeek API"
    (passing the old startup probe) but then blocked indefinitely inside
    client.chat.completions.create() because the DeepSeek API accepted the
    request but never streamed a response (HTTP layer timeout=240s did not
    fire, so the agent burned the full 2400s EVAL_TIMEOUT).
    """
    attempts = 0
    while True:
        attempts += 1
        safe_log(f"Running the agent (attempt {attempts}/{AGENT_MAX_ATTEMPTS})")

        # Build a wrapper command that runs the agent under continuous
        # liveness monitoring. The wrapper:
        #   1. Launches the agent in the background, stdout→output_file.
        #   2. Polls every 1s: if the file size hasn't grown for
        #      AGENT_IDLE_TIMEOUT_SECONDS consecutive seconds, kills the
        #      agent (exit 130 = stall) and retries.
        #   3. Otherwise waits for the agent to finish and forwards its
        #      exit code (the outer `timeout` in cmd still enforces
        #      EVAL_TIMEOUT as a hard cap).
        agent_cmd_str = " ".join(shlex.quote(str(c)) for c in cmd)
        idle = AGENT_IDLE_TIMEOUT_SECONDS
        # Wrapper runs the agent in a SEPARATE process group (setsid) so that
        # kill -KILL on the agent PID does not also kill the wrapper shell.
        # Without setsid, kill -KILL could propagate to the wrapper (same
        # process group), preventing it from executing `exit 130` and causing
        # docker exec to report exit code -9 instead of 130 — which the retry
        # logic below does not recognize as a stall.
        # Wrapper monitors BOTH stdout (output_file) AND the agent's chat
        # history .md file. The agent's real work (LLM calls, tool execution)
        # is logged via safe_log() to the .md file, NOT to stdout — stdout
        # only gets create_client()'s "Using DeepSeek API" print. Without
        # monitoring the .md file, the wrapper falsely detects stall after
        # 300s of stdout silence even though the agent is actively calling
        # the API and consuming tokens (confirmed: DeepSeek platform showed
        # ~4M tokens consumed on the 2026-07-04 run, but 16 tasks were
        # falsely killed as "stall").
        md_file = chat_history_file_container
        wrapper = (
            "output_file=$(mktemp); "
            f"setsid {agent_cmd_str} > \"$output_file\" 2>&1 & "
            "agent_pid=$!; "
            f"md_file={shlex.quote(md_file)}; "
            "last_stdout=0; last_md=0; idle_secs=0; "
            "while kill -0 $agent_pid 2>/dev/null; do "
            "  cur_stdout=$(stat -c %s \"$output_file\" 2>/dev/null || echo 0); "
            "  cur_md=$(stat -c %s \"$md_file\" 2>/dev/null || echo 0); "
            "  if [ \"$cur_stdout\" -gt \"$last_stdout\" ] || [ \"$cur_md\" -gt \"$last_md\" ]; then "
            "    idle_secs=0; "
            "  else "
            "    idle_secs=$((idle_secs + 1)); "
            "  fi; "
            "  last_stdout=$cur_stdout; last_md=$cur_md; "
            f"  if [ $idle_secs -ge {idle} ]; then "
            f"    echo \"AGENT_STALL: no new output for {idle}s (stdout=$cur_stdout md=$cur_md), killing agent\" >&2; "
            "    kill -TERM -- -$agent_pid 2>/dev/null; "
            "    sleep 3; kill -KILL -- -$agent_pid 2>/dev/null; "
            "    cat \"$output_file\"; rm -f \"$output_file\"; exit 130; "
            "  fi; "
            "  sleep 1; "
            "done; "
            "wait $agent_pid 2>/dev/null; rc=$?; "
            "cat \"$output_file\"; rm -f \"$output_file\"; exit $rc"
        )
        exec_result = container.exec_run(
            ["/bin/bash", "-c", wrapper],
            environment=env_vars,
            workdir="/testbed/",
        )
        # NOTE: raise_error=False is critical here — log_container_output
        # defaults to raising on non-zero exit codes, which would bypass the
        # stall-retry logic below and let the exception fall through to
        # process_entry's generic except block (producing an incomplete result
        # instead of a retry). This was the bug that caused the 2026-07-03
        # fix_idle_monitor run to report 10 exit-130 failures with zero
        # retries despite AGENT_STALL being correctly detected.
        log_container_output(exec_result, raise_error=False)
        exit_code = exec_result.exit_code

        # Exit 130 = stall (wrapper killed agent after no output for idle secs).
        # Exit -9/137 = SIGKILL (fallback when setsid unavailable and kill
        #   propagated to wrapper; still a stall, just without clean exit 130).
        # Exit 130 = stall (wrapper killed agent after no output for idle secs).
        # Exit 124/-15 = outer timeout (agent ran the full EVAL_TIMEOUT).
        # Exit -9/137 = SIGKILL (external kill, NOT a stall — could be OOM or
        #   leftover pkill from a previous attempt. Retrying on -9 caused a
        #   bug where attempt 2/3 were killed instantly by residual signals
        #   from attempt 1's pkill, then misclassified as stall, wasting all
        #   3 attempts in ~5 seconds. Only retry on explicit 130.)
        is_stall = exit_code == 130
        if not is_stall or attempts >= AGENT_MAX_ATTEMPTS:
            return exec_result, attempts, container, diff_base_commit

        # Agent stalled (no new output for idle secs) → likely stuck in
        # import, SSL init, DNS, or a hung API call. Reset the testbed in the
        # SAME container (cheaper and more reliable than rebuilding) and retry.
        safe_log(
            f"Agent stalled (no new output for {idle}s) on attempt {attempts}; "
            f"resetting testbed and retrying."
        )
        try:
            # NOTE: Do NOT pkill -f coding_agent.py here — the wrapper already
            # killed the agent process group (kill -- -$agent_pid), and a broad
            # pkill would race with the next attempt's agent startup. A short
            # sleep lets any I/O cleanup finish before attempt 2 starts.
            container.exec_run(
                ["sleep", "3"],
                environment=env_vars, workdir="/",
            )
            # Reset testbed to the sanitized base commit.
            container.exec_run(
                f"git -C /testbed reset --hard {shlex.quote(diff_base_commit)} && "
                "git -C /testbed clean -fdx",
                environment=env_vars, workdir="/",
            )
            # Re-run preflight to restore runtime env (PATH, caches, etc.).
            container.exec_run(
                ["/bin/sh", "-c", polyglot_preflight_cmd(entry)],
                environment=env_vars, workdir="/",
            )
            # Re-apply model patches if any (they were applied before the
            # first agent run and may have been cleaned by `git clean -fdx`).
            if model_patch_paths:
                for model_patch_path in model_patch_paths:
                    copy_to_container(container, model_patch_path, "/hgm/parent_patch.txt")
                    apply_cmd = get_apply_patch_cmd("/hgm/parent_patch.txt")
                    container.exec_run(
                        ["/bin/sh", "-c", apply_cmd], environment=env_vars, workdir="/hgm"
                    )
            # Re-inject `import json` after testbed reset + patch re-apply.
            # The reset wiped /hgm/coding_agent.py changes, and patch re-apply
            # may again silently drop the `import json` hunk (see comment in
            # process_entry). Without this, attempt 2+ crashes with NameError.
            agent_py = resolve_host_agent_python() or "python"
            container.exec_run(
                [agent_py, "-c",
                 "p='/hgm/coding_agent.py'; t=open(p).read(); "
                 "open(p,'w').write(t) if 'import json' in t else "
                 "open(p,'w').write(t.replace('import argparse','import argparse\\nimport json',1))"],
                environment=env_vars, workdir="/hgm",
            )
        except Exception as e:
            safe_log(f"Failed to reset testbed for retry: {e}")
            if attempts >= AGENT_MAX_ATTEMPTS:
                return exec_result, attempts, container, diff_base_commit
            continue


def process_entry(
    entry,
    out_dname,
    model_name_or_path,
    model_patch_paths,
    skip_existing=True,
    init_agent_path=".",
    reuse_patch_dir=None,
):
    """
    Process a single dataset entry. This function encapsulates the main processing logic
    for each entry to make it suitable for parallel execution.
    """
    instance_id = entry["instance_id"]
    problem_statement = entry["problem_statement"]
    base_commit = entry["base_commit"]
    chat_history_file = out_dname / (instance_id + ".md")
    out_fname = out_dname / (instance_id + ".json")
    eval_file = out_dname / f"{instance_id}_eval.sh"
    eval_result_file = out_dname / f"{instance_id}_eval.md"

    # Skip if output result already exists
    if out_fname.exists() and skip_existing:
        print(f"Skipping existing entry {instance_id}")
        with open(out_fname) as f:
            result = json.loads(f.read())
        return result

    # Initialize container as None to avoid UnboundLocalError
    container = None
    logger = None
    client = None

    try:
        # Create and start the Docker container
        client = docker_from_env()
        run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        # Set up thread-specific logger
        logger = setup_logger(str(out_dname / f"{instance_id}_docker.log"))
        nocache = True
        test_spec = make_test_spec(entry)
        # Remove any existing container with the same name
        container_name = test_spec.get_instance_container_name(run_id)
        remove_existing_container(client, container_name)
        # Now create and start the container
        container = build_container(
            test_spec, client, run_id, logger, nocache, force_rebuild=False
        )
        container.start()

        # Copy the necessary files and requirements to the container
        copy_to_container(
            container,
            os.path.join(init_agent_path, "coding_agent_polyglot.py"),
            "/hgm/coding_agent.py",
        )
        copy_to_container(
            container,
            os.path.join(init_agent_path, "requirements.txt"),
            "/hgm/requirements.txt",
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "pytest.ini"), "/hgm/pytest.ini"
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "tools/"), "/hgm/tools/"
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "utils/"), "/hgm/utils/"
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "tests/"), "/hgm/tests/"
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "prompts/"), "/hgm/prompts/"
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "llm.py"), "/hgm/llm.py"
        )
        copy_to_container(
            container,
            os.path.join(init_agent_path, "llm_withtools.py"),
            "/hgm/llm_withtools.py",
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "LICENSE"), "/hgm/LICENSE"
        )
        copy_to_container(
            container, os.path.join(init_agent_path, "README.md"), "/hgm/README.md"
        )
        chat_history_file_container = f"/hgm/{chat_history_file.name}"

        # See the checked repo
        exec_result = container.exec_run("ls -R /testbed", workdir="/")
        log_container_output(exec_result)
        env_vars = polyglot_runtime_env(entry)
        exec_result = container.exec_run(
            ["/bin/sh", "-c", polyglot_preflight_cmd(entry)],
            environment=env_vars,
            workdir="/",
        )
        log_container_output(exec_result)
        if exec_result.exit_code != 0:
            output = exec_result.output.decode("utf-8", errors="replace")
            raise RuntimeError(f"Polyglot preflight failed: {output[:1000]}")

        diff_base_commit = base_commit
        if should_sanitize_agent_workspace():
            safe_log("Sanitizing agent workspace")
            exec_result = container.exec_run(
                ["/bin/sh", "-c", get_sanitize_agent_workspace_cmd()],
                environment=env_vars,
                workdir="/",
            )
            log_container_output(exec_result)
            if exec_result.exit_code != 0:
                output = exec_result.output.decode("utf-8", errors="replace")
                raise RuntimeError(f"Agent workspace sanitization failed: {output[:1000]}")
            diff_base_commit = extract_commit_hash(exec_result.output)
            if not diff_base_commit:
                raise RuntimeError("Agent workspace sanitization did not emit a base commit")
            safe_log(f"Sanitized base commit: {diff_base_commit}")

        if is_test_suite_task(entry):
            safe_log("Applying visible test-suite scaffold")
            copy_scaffold_patch_to_container(container, entry)
            exec_result = container.exec_run(
                ["/bin/sh", "-c", get_apply_patch_cmd("/hgm/scaffold_patch.diff")],
                environment=env_vars,
                workdir="/testbed",
            )
            log_container_output(exec_result)
            if exec_result.exit_code != 0:
                output = exec_result.output.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Failed to apply test-suite scaffold: {output[:1000]}"
                )
            exec_result = container.exec_run(
                [
                    "/bin/sh",
                    "-c",
                    "git -C /testbed add -A && "
                    "git -C /testbed commit -q --allow-empty -m eval-scaffold && "
                    "git -C /testbed rev-parse HEAD",
                ],
                environment=env_vars,
                workdir="/",
            )
            log_container_output(exec_result)
            if exec_result.exit_code != 0:
                output = exec_result.output.decode("utf-8", errors="replace")
                raise RuntimeError(
                    f"Failed to commit test-suite scaffold: {output[:1000]}"
                )
            diff_base_commit = extract_commit_hash(exec_result.output)
            if not diff_base_commit:
                raise RuntimeError("Test-suite scaffold did not emit a base commit")
            safe_log(f"Scaffold base commit: {diff_base_commit}")

        reusable = load_reusable_model_patch(reuse_patch_dir, instance_id)
        if reusable:
            safe_log(f"Reusing model patch from {reusable['source']}")
            model_patch = reusable["model_patch"]
            proposed_model_patches = reusable["proposed_model_patches"]
            write_patch_text_to_container(
                container,
                model_patch,
                "/hgm/model_patch.diff",
                f"{instance_id}.reuse-model.",
            )
        else:
            # Get test description
            eval_cmd = MAP_REPO_VERSION_TO_SPECS[entry["language"]]["test_cmd"]
            test_description = get_test_description(eval_cmd, polyglot=True)

            # Apply model patch
            if model_patch_paths:
                safe_log("Applying model patches")
                for model_patch_path in model_patch_paths:
                    copy_to_container(container, model_patch_path, "/hgm/parent_patch.txt")
                    apply_parent_patch_cmd = get_apply_patch_cmd("/hgm/parent_patch.txt")
                    exec_result = container.exec_run(
                        ["/bin/sh", "-c", apply_parent_patch_cmd],
                        environment=env_vars,
                        workdir="/hgm",
                    )
                    log_container_output(exec_result)
                    if exec_result.exit_code != 0:
                        output = exec_result.output.decode("utf-8", errors="replace")
                        raise RuntimeError(
                            f"Failed to apply agent patch {model_patch_path}: {output[:1000]}"
                        )
                    exec_result = container.exec_run(
                        "rm /hgm/parent_patch.txt",
                        environment=env_vars,
                        workdir="/hgm",
                    )
                    log_container_output(exec_result)

                # (import json injection moved to after agent_python is resolved,
                #  see below — we need the host agent python to run the injection
                #  reliably across container OSes.)

            # Agent Python: non-Python task images (C++/Go/Rust/Java/JS) often have no python.
            host_agent_python = resolve_host_agent_python()
            if host_agent_python:
                safe_log(f"Using host agent python: {host_agent_python}")
                probe_cmd = [
                    host_agent_python,
                    "-c",
                    (
                        "import anthropic, openai, sys; "
                        "print('agent python ok', sys.version.split()[0])"
                    ),
                ]
                exec_result = container.exec_run(
                    probe_cmd, environment=env_vars, workdir="/"
                )
                log_container_output(exec_result)
                if exec_result.exit_code != 0:
                    output = exec_result.output.decode("utf-8", errors="replace")
                    raise RuntimeError(
                        f"Host agent python unusable in container ({host_agent_python}): "
                        f"{output[:500]}"
                    )
                agent_python = host_agent_python
            else:
                agent_python = "python"
                safe_log("Installing more requirements")
                exec_result = container.exec_run(
                    f"{agent_python} -m pip install -r /hgm/requirements.txt",
                    environment=env_vars,
                    workdir="/",
                )
                log_container_output(exec_result)
                if exec_result.exit_code != 0:
                    raise RuntimeError(
                        "Agent requirements install failed; set POLYGLOT_HOST_AGENT_PYTHON "
                        "to a host venv for non-Python containers."
                    )

            # Inject `import json` into /hgm/coding_agent.py if missing.
            # The HGM patch chain adds an extract_json_from_response() method
            # that uses json.JSONDecodeError, but the patch's `import json`
            # hunk can be silently dropped by `patch --fuzz=5` fallback,
            # causing NameError at runtime. Using Python (not sed) for
            # cross-container reliability.
            safe_log("Injecting import json into coding_agent.py")
            exec_result = container.exec_run(
                [agent_python, "-c",
                 "p='/hgm/coding_agent.py'; t=open(p).read(); "
                 "open(p,'w').write(t) if 'import json' in t else "
                 "open(p,'w').write(t.replace('import argparse','import argparse\\nimport json',1))"],
                environment=env_vars, workdir="/hgm",
            )
            log_container_output(exec_result, raise_error=False)
            # Verify injection succeeded
            exec_result = container.exec_run(
                ["grep", "-c", "import json", "/hgm/coding_agent.py"],
                environment=env_vars, workdir="/hgm",
            )
            log_container_output(exec_result, raise_error=False)

            # Run the agent
            if uses_vllm_model(llm):
                vllm_url = f"http://{env_vars['VLLM_HOST']}:{env_vars['VLLM_PORT']}/v1/models"
                safe_log(f"Checking vLLM connectivity from task container: {vllm_url}")
                check_cmd = [
                    agent_python,
                    "-c",
                    (
                        "import os, urllib.request; "
                        "url=f\"http://{os.environ['VLLM_HOST']}:{os.environ['VLLM_PORT']}/v1/models\"; "
                        "print(f'Checking {url}', flush=True); "
                        "urllib.request.urlopen(url, timeout=10).read(); "
                        "print('vLLM connectivity OK', flush=True)"
                    ),
                ]
                exec_result = container.exec_run(
                    check_cmd, environment=env_vars, workdir="/testbed/"
                )
                log_container_output(exec_result, raise_error=False)
                if exec_result.exit_code != 0:
                    output = exec_result.output.decode("utf-8", errors="replace")
                    raise RuntimeError(
                        "vLLM is not reachable from the Polyglot task container at "
                        f"{vllm_url}. Check the SSH reverse tunnel and Docker network mode. "
                        f"Output: {output}"
                    )

            safe_log("Running the agent")
            cmd = [
                "timeout",
                str(timeout),
                agent_python,
                "-u",
                "/hgm/coding_agent.py",
                "--problem_statement",
                problem_statement,
                "--git_dir",
                "/testbed/",
                "--chat_history_file",
                chat_history_file_container,
                "--base_commit",
                diff_base_commit,
                "--outdir",
                "/hgm/",
                "--test_description",
                test_description,
                "--language",
                entry["language"],
                "--model",
                llm,
                "--timeout",
                str(timeout),
            ]
            exec_result, attempts_used, container, diff_base_commit = run_agent_with_startup_retry(
                container=container,
                env_vars=env_vars,
                cmd=cmd,
                logger=logger,
                entry=entry,
                diff_base_commit=diff_base_commit,
                model_patch_paths=model_patch_paths,
                init_agent_path=init_agent_path,
                test_description=test_description,
                instance_id=instance_id,
                chat_history_file_container=chat_history_file_container,
                timeout=timeout,
                client=client,
                run_id=run_id,
            )
            if attempts_used > 1:
                safe_log(f"Agent completed after {attempts_used} attempts")
            log_container_output(exec_result)

            refresh_cmd = refresh_model_patch_cmd(diff_base_commit, entry)
            exec_result = container.exec_run(
                ["/bin/sh", "-c", refresh_cmd],
                environment=env_vars,
                workdir="/testbed/",
            )
            log_container_output(exec_result)

            # Copy output files back to host
            logger.info("Copying output files back to host")
            copy_from_container(container, chat_history_file_container, chat_history_file)
            # Additional chat history files
            exec_result = container.exec_run(
                f"find /hgm/ -name '{instance_id}_*.md'", workdir="/"
            )
            chat_history_files_container = exec_result.output.decode().split()
            for chat_history_file_container in chat_history_files_container:
                chat_history_file = out_dname / Path(chat_history_file_container).name
                copy_from_container(
                    container, chat_history_file_container, chat_history_file
                )

            # Get model_patch
            logger.info("Getting model_patch")
            exec_result = container.exec_run("cat /hgm/model_patch.diff")
            log_container_output(exec_result)
            model_patch = ""
            model_patch = exec_result.output.decode()

            # Additional proposed model patches
            proposed_model_patches = []

        # Directly do eval
        eval_result = ""
        if not model_patch:
            eval_result = "empty_patch"
            result = result_record(
                instance_id,
                model_name_or_path,
                model_patch,
                proposed_model_patches,
                eval_result,
                True,
            )
            out_fname.write_text(json.dumps(result, indent=4))
            return {
                "success": True,
                "instance_id": instance_id,
                "eval_result": eval_result,
            }

        # Reset to the sanitized base, re-apply the model patch, then inject hidden tests
        # from host metadata. Do not use test_commit here: it exposes hidden tests via git
        # history during the agent phase and lets the agent modify tests instead of code.
        exec_result = container.exec_run(
            f"git -C /testbed reset --hard {shlex.quote(diff_base_commit)}",
            environment=env_vars,
            workdir="/",
        )
        log_container_output(exec_result)
        if exec_result.exit_code != 0:
            output = exec_result.output.decode("utf-8", errors="replace")
            raise RuntimeError(f"Failed to reset to sanitized base: {output[:1000]}")
        exec_result = container.exec_run(
            "git -C /testbed clean -fdx && "
            + polyglot_runtime_setup_cmd(entry, clean_testbed=True),
            environment=env_vars,
            workdir="/",
        )
        log_container_output(exec_result)
        if exec_result.exit_code != 0:
            output = exec_result.output.decode("utf-8", errors="replace")
            raise RuntimeError(f"Failed to clean testbed before eval: {output[:1000]}")

        safe_log("Applying model patch for eval")
        exec_result = container.exec_run(
            ["/bin/sh", "-c", get_apply_patch_cmd("/hgm/model_patch.diff")],
            environment=env_vars,
            workdir="/testbed",
        )
        log_container_output(exec_result)
        if exec_result.exit_code != 0:
            output = exec_result.output.decode("utf-8", errors="replace")
            raise RuntimeError(f"Failed to apply model patch for eval: {output[:1000]}")

        safe_log("Applying hidden test patch for eval")
        copy_test_patch_to_container(container, entry)
        exec_result = container.exec_run(
            ["/bin/sh", "-c", get_apply_patch_cmd("/hgm/test_patch.diff")],
            environment=env_vars,
            workdir="/testbed",
        )
        log_container_output(exec_result)
        if exec_result.exit_code != 0:
            output = exec_result.output.decode("utf-8", errors="replace")
            raise RuntimeError(f"Failed to apply hidden test patch: {output[:1000]}")

        safe_log("Running the eval")
        test_command = test_commands_for_entry(entry)

        eval_file.write_text(get_eval_script(test_command, entry=entry))

        copy_to_container(container, eval_file, "/testbed/eval.sh")
        exec_result = container.exec_run("ls -R /testbed", workdir="/")
        log_container_output(exec_result)
        exec_result = container.exec_run(
            "chmod +x /testbed/eval.sh", environment=env_vars, workdir="/"
        )
        log_container_output(exec_result)

        exec_result = container.exec_run(
            "timeout 120 ./eval.sh", environment=env_vars, workdir="/testbed"
        )
        log_container_output(exec_result, raise_error=False)
        eval_result_file.write_text(exec_result.output.decode())
        if exec_result.exit_code == 0:
            eval_result = "resolved"
        else:
            eval_result = "unresolved"

        # Write result to file
        result = result_record(
            instance_id,
            model_name_or_path,
            model_patch,
            proposed_model_patches,
            eval_result,
            True,
        )
        out_fname.write_text(json.dumps(result, indent=4))

        return {"success": True, "instance_id": instance_id, "eval_result": eval_result}

    except Exception as e:
        # Check if eval_result exists in local scope
        if "eval_result" not in locals():
            eval_result = "incomplete"
        else:
            eval_result = "error"
        if "model_patch" not in locals():
            model_patch = ""
        if "proposed_model_patches" not in locals():
            proposed_model_patches = []

        # Write result to file
        result = result_record(
            instance_id,
            model_name_or_path,
            model_patch,
            proposed_model_patches,
            eval_result,
            False,
            error=e,
        )
        out_fname.write_text(json.dumps(result, indent=4))

        # print(f"Error processing entry {instance_id}: {str(e)}")
        if logger is not None:
            logger.error(f"Error processing entry {instance_id}: {str(e)}")
        else:
            print(f"Error processing entry {instance_id}: {str(e)}")
        return {
            "success": False,
            "instance_id": instance_id,
            "eval_result": eval_result,
            "error": str(e),
        }

    finally:
        # Clean up docker container
        try:
            if container is not None:
                cleanup_container(client, container, logger)
        except Exception as e:
            print(f"Error cleaning up Docker container for {instance_id}: {e}")


def harness(
    dataset_path="polyglot/polyglot_benchmark_metadata.json",
    test_task_list=None,
    num_samples=-1,
    max_workers=4,
    model_name_or_path=None,
    model_patch_paths=None,
    num_evals=1,
    num_evals_parallel=1,
    pred_dname="./polyglot/predictions",
    output_dir="./polyglot/predictions",
    skip_existing=True,
    init_agent_path=".",
    reuse_patch_dir=None,
):
    """
    Parallel processing harness using ThreadPoolExecutor.

    Args:
        test_task_list: List of task IDs to process (None for all)
        num_samples: Number of samples to process (-1 for all)
        max_workers: Maximum number of concurrent threads
        model_name_or_path: Model name or path
        model_patch_paths: Paths to the model patches for hgm
        num_evals: Repeated number of swe evaluations
        reuse_patch_dir: Optional prediction dir containing reusable model_patch fields
    """
    # Load dataset
    if not os.path.exists(dataset_path):
        raise FileNotFoundError(f"Dataset file not found: {dataset_path}")
    with open(dataset_path) as f:
        dataset = json.load(f)

    # Ensure that necessary directories exist
    if model_name_or_path is None:
        timestamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
        model_name_or_path = f"{timestamp}--claude-3-5-sonnet-20241022"
    pred_dname = Path(pred_dname)
    pred_dname.mkdir(parents=True, exist_ok=True)
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    out_dnames = []

    filtered_model_patch_paths = None
    temp_patch_files = []
    if model_patch_paths:
        filtered_model_patch_paths = []
        for model_patch_path in model_patch_paths:
            with open(model_patch_path, "r") as f:
                patch_content = f.read()
            patch_content = remove_patch_by_files(patch_content)
            temp_patch = tempfile.NamedTemporaryFile(
                mode="w",
                suffix=".diff",
                prefix=f"{Path(model_patch_path).stem}.filtered.",
                dir=pred_dname,
                delete=False,
            )
            try:
                temp_patch.write(patch_content)
            finally:
                temp_patch.close()
            filtered_model_patch_paths.append(temp_patch.name)
            temp_patch_files.append(temp_patch.name)

    # Prepare the dataset entries
    entries = list(dataset)
    if test_task_list is not None:
        test_task_set = set(test_task_list)
        entries = [entry for entry in entries if entry["instance_id"] in test_task_set]
    if num_samples is not None and num_samples > -1:
        entries = entries[:num_samples]
    if not entries:
        raise ValueError("No Polyglot entries selected for evaluation")

    # Build the environment images
    client = docker_from_env()
    build_env_images(
        client, dataset=entries, max_workers=max_workers, force_rebuild=False
    )

    # Define a function to handle a single evaluation for all specified issues
    model_name_or_path_inst = f"{model_name_or_path}_{0}"
    out_dname = pred_dname / model_name_or_path_inst
    out_dname.mkdir(exist_ok=True)
    out_dnames.append(out_dname)

    print(f"Starting evaluation {0} for model {model_name_or_path}")

    # Process entries in parallel
    results = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        # Submit all tasks
        future_to_entry = {
            executor.submit(
                process_entry,
                entry,
                out_dname,
                model_name_or_path_inst,
                filtered_model_patch_paths,
                skip_existing=skip_existing,
                init_agent_path=init_agent_path,
                reuse_patch_dir=reuse_patch_dir,
            ): entry
            for entry in entries
        }

        # Process completed tasks as they finish
        for future in as_completed(future_to_entry):
            result = future.result()
            results.append(result)
            if result["success"]:
                print(
                    f"Successfully processed entry {result['instance_id']} for eval {0}"
                )
            else:
                print(
                    f"Failed to process entry {result['instance_id']} for eval {0}: {result.get('error', 'Unknown error')}"
                )
        # Get final results from completed futures

    print(f"All evaluations completed for model {model_name_or_path}")

    # Directly generate report
    # write report to file
    incomplete_ids = [
        result["instance_id"] for result in results if not result["success"]
    ]
    completed_ids = [result["instance_id"] for result in results if result["success"]]
    # Get resolved/unresolved/error/empty patch IDs from results
    resolved_ids = []
    unresolved_ids = []
    error_ids = []
    empty_patch_ids = []
    unstopped_containers = []
    unremoved_images = []

    for result in results:
        if result["success"]:
            if result.get("eval_result") == "resolved":
                resolved_ids.append(result["instance_id"])
            elif result.get("eval_result") == "unresolved":
                unresolved_ids.append(result["instance_id"])
            elif result.get("eval_result") == "empty_patch":
                empty_patch_ids.append(result["instance_id"])
            else:
                error_ids.append(result["instance_id"])
        else:
            error_ids.append(result["instance_id"])

    report = {
        "total_instances": len(dataset),
        "submitted_instances": len(results),
        "completed_instances": len(completed_ids),
        "resolved_instances": len(resolved_ids),
        "unresolved_instances": len(unresolved_ids),
        "empty_patch_instances": len(empty_patch_ids),
        "error_instances": len(error_ids),
        "total_submitted_instances": len(results),
        "total_completed_instances": len(completed_ids),
        "total_resolved_instances": len(resolved_ids),
        "total_unresolved_instances": len(unresolved_ids),
        "total_emptypatch_instances": len(empty_patch_ids),
        "total_error_instances": len(error_ids),
        "unstopped_instances": len(unstopped_containers),
        "completed_ids": list(sorted(completed_ids)),
        "incomplete_ids": list(sorted(incomplete_ids)),
        "empty_patch_ids": list(sorted(empty_patch_ids)),
        "submitted_ids": list(sorted(result["instance_id"] for result in results)),
        "resolved_ids": list(sorted(resolved_ids)),
        "unresolved_ids": list(sorted(unresolved_ids)),
        "error_ids": list(sorted(error_ids)),
        "total_completed_ids": list(sorted(completed_ids)),
        "total_incomplete_ids": list(sorted(incomplete_ids)),
        "total_emptypatch_ids": list(sorted(empty_patch_ids)),
        "total_submitted_ids": list(sorted(result["instance_id"] for result in results)),
        "total_resolved_ids": list(sorted(resolved_ids)),
        "total_unresolved_ids": list(sorted(unresolved_ids)),
        "total_error_ids": list(sorted(error_ids)),
        "unstopped_containers": list(sorted(unstopped_containers)),
        "unremoved_images": list(sorted(unremoved_images)),
        "schema_version": 3,
    }

    print(report)
    report_file = output_dir / Path(
        model_name_or_path.replace("/", "__")
        + " "
        + datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")
        + ".json"
    )
    with open(report_file, "w") as f:
        print(json.dumps(report, indent=4), file=f)
    print(f"Report written to {report_file}")

    for temp_patch_file in temp_patch_files:
        try:
            os.remove(temp_patch_file)
        except FileNotFoundError:
            pass

    return out_dnames


def main():
    # Parse command line arguments
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--num_samples", type=int, default=-1, help="Number of samples to process"
    )
    parser.add_argument(
        "--max_workers",
        type=int,
        default=5,
        help="Maximum number of concurrent threads",
    )
    parser.add_argument(
        "--model_name_or_path", type=str, default=None, help="Model name or path"
    )
    parser.add_argument(
        "--model_patch_paths", type=str, default=None, help="Paths to the model patches"
    )
    parser.add_argument(
        "--num_evals", type=int, default=1, help="Repeated number of swe evaluations"
    )
    parser.add_argument(
        "--num_evals_parallel",
        type=int,
        default=1,
        help="Number of parallel repeated evaluations",
    )
    args = parser.parse_args()

    with open("polyglot/polyglot_benchmark_metadata.json") as f:
        metadata = json.loads(f.read())
        language_task_list = [
            entry["instance_id"]
            for entry in metadata
            if entry["instance_id"].startswith("python")
        ]
        # Create a list of all tasks from metadata
        all_task_list = [entry["instance_id"] for entry in metadata]

    from utils.common_utils import load_json_file

    swe_issues_med = load_json_file("./polyglot/subsets/medium.json")
    model_patch_paths = (
        args.model_patch_paths.split(",")
        if args.model_patch_paths is not None
        else None
    )
    # Run the parallel harness

    harness(
        dataset_path="polyglot/polyglot_benchmark_metadata.json",
        test_task_list=all_task_list,
        num_samples=args.num_samples,
        max_workers=args.max_workers,
        model_name_or_path=args.model_name_or_path,
        model_patch_paths=model_patch_paths,
        num_evals=args.num_evals,
        num_evals_parallel=args.num_evals_parallel,
    )


if __name__ == "__main__":
    main()
