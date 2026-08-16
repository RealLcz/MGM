#!/usr/bin/env python3
"""Run an HGM/MGM agent on SWE-bench Pro tasks and evaluate its patch.

This is the SWE-bench Pro counterpart of the existing SWE/Polyglot harnesses:
it starts the official SWE-bench Pro Docker image, copies the selected agent into
the container, asks it to edit /app, captures model_patch.diff, then runs the
official SWE-bench Pro run_script/parser pair.
"""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import io
import json
import os
import re
import shlex
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import docker
from tqdm import tqdm

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from swebench_pro_utils import (  # noqa: E402
    DEFAULT_DATASET,
    DEFAULT_DOCKERHUB_USERNAME,
    DEFAULT_REMOTE_SOCKET,
    DEFAULT_REMOTE_USER,
    DEFAULT_SPLIT,
    DEFAULT_SUBSET,
    ROOT,
    docker_host_context,
    dockerhub_image_from_row,
    selected_rows_for_subset,
)
from utils.evo_utils import get_model_patch_paths  # noqa: E402


EXCLUDE_PATTERNS = [
    "node_modules/",
    "__pycache__/",
    "*.pyc",
    ".pytest_cache/",
    "target/",
    "build/",
    ".gradle/",
    ".npm/",
    ".cache/",
    "dist/",
]

AGENT_VENV = "/hgm/agent_venv"
AGENT_PYTHON = f"{AGENT_VENV}/bin/python"
AGENT_SITE = "/hgm/agent_site"

AGENT_LANGUAGE_ALIASES = {
    "c++": "cpp",
    "cc": "cpp",
    "cxx": "cpp",
    "golang": "go",
    "js": "javascript",
    "jsx": "javascript",
    "ts": "javascript",
    "tsx": "javascript",
    "typescript": "javascript",
    "py": "python",
    "rs": "rust",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["initial", "best", "node"], default="best")
    parser.add_argument("--node-id", default="", help="Required when --agent node")
    parser.add_argument("--hgm-output-dir", default="output_mgm")
    parser.add_argument("--init-agent-src", default="initial_swe/default_agent/src")
    parser.add_argument(
        "--agent-entrypoint",
        choices=["auto", "coding_agent.py", "coding_agent_polyglot.py"],
        default=os.environ.get("SWE_PRO_AGENT_ENTRYPOINT", "auto"),
        help=(
            "Agent file to execute as /hgm/coding_agent.py. auto selects the "
            "polyglot entrypoint when the init or HGM output path contains polyglot."
        ),
    )
    parser.add_argument("--subset", default=str(DEFAULT_SUBSET))
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--upstream-dir", default=str(ROOT / "upstream"))
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--dockerhub-username", default=DEFAULT_DOCKERHUB_USERNAME)
    parser.add_argument("--remote-host", default="")
    parser.add_argument("--remote-user", default=DEFAULT_REMOTE_USER)
    parser.add_argument("--remote-socket", default=DEFAULT_REMOTE_SOCKET)
    parser.add_argument("--llm", default=os.environ.get("HGM_LLM_MODEL_ID", "Qwen/Qwen3-Coder-Next"))
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--agent-timeout", type=int, default=3600)
    parser.add_argument("--eval-timeout", type=int, default=7200)
    parser.add_argument("--docker-timeout", type=int, default=7200)
    parser.add_argument(
        "--agent-pip-index-url",
        default=os.environ.get("SWE_PRO_AGENT_PIP_INDEX_URL", "https://pypi.org/simple"),
        help="Modern PyPI index used only for installing the copied agent requirements.",
    )
    parser.add_argument(
        "--agent-pip-extra-index-url",
        default=os.environ.get("SWE_PRO_AGENT_PIP_EXTRA_INDEX_URL", ""),
        help="Optional extra PyPI index used only for installing agent requirements.",
    )
    parser.add_argument(
        "--agent-pip-trusted-host",
        default=os.environ.get("SWE_PRO_AGENT_PIP_TRUSTED_HOST", ""),
        help="Comma-separated trusted hosts for the agent requirements pip install.",
    )
    parser.add_argument(
        "--agent-pip-timeout",
        type=int,
        default=int(os.environ.get("SWE_PRO_AGENT_PIP_TIMEOUT", "120")),
    )
    parser.add_argument(
        "--agent-pip-constraints",
        default=os.environ.get("SWE_PRO_AGENT_PIP_CONSTRAINTS", str(ROOT / "agent_constraints.txt")),
        help="Optional constraints file copied into the task container for agent dependency install.",
    )
    parser.add_argument("--docker-platform", default=None)
    parser.add_argument("--pull", choices=["missing", "never", "always"], default="missing")
    parser.add_argument("--redo", action="store_true")
    parser.add_argument("--block-network", action="store_true")
    return parser.parse_args()


def literal_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return list(ast.literal_eval(str(value)))


def normalize_agent_language(value: Any) -> str:
    language = str(value or "python").strip().lower()
    return AGENT_LANGUAGE_ALIASES.get(language, language)


def load_text(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def find_best_node(hgm_output_dir: Path) -> str:
    best: tuple[float, int, int, str] | None = None
    for meta_path in hgm_output_dir.glob("*/metadata.json"):
        node_id = meta_path.parent.name
        if node_id == "initial":
            continue
        try:
            metadata = json.loads(meta_path.read_text(encoding="utf-8"))
        except Exception:
            continue
        perf = metadata.get("overall_performance") or {}
        submitted = int(perf.get("total_submitted_instances") or 0)
        resolved = int(perf.get("total_resolved_instances") or 0)
        if submitted <= 0:
            continue
        score = perf.get("accuracy_score")
        score = float(score) if score is not None else resolved / submitted
        candidate = (score, resolved, submitted, node_id)
        if best is None or candidate > best:
            best = candidate
    if best is None:
        raise RuntimeError(f"No evaluated nodes found under {hgm_output_dir}")
    return best[3]


def resolve_agent(args: argparse.Namespace) -> tuple[str, list[str], str]:
    hgm_output_dir = Path(args.hgm_output_dir)
    if not hgm_output_dir.is_absolute():
        hgm_output_dir = REPO_ROOT / hgm_output_dir

    if args.agent == "initial":
        return "initial", [], "initial"

    if args.agent == "best":
        node_id = find_best_node(hgm_output_dir)
    else:
        if not args.node_id:
            raise ValueError("--node-id is required when --agent node")
        node_id = args.node_id

    patch_paths = get_model_patch_paths(str(REPO_ROOT), str(hgm_output_dir), node_id)
    return node_id, patch_paths, args.agent


def resolve_agent_entrypoint(args: argparse.Namespace, init_agent_src: Path) -> str:
    if args.agent_entrypoint != "auto":
        return args.agent_entrypoint

    if (
        "polyglot" in str(init_agent_src).lower()
        or "polyglot" in str(args.hgm_output_dir).lower()
    ):
        return "coding_agent_polyglot.py"
    return "coding_agent.py"


def make_tar(files: dict[str, str | bytes], executable_suffixes: tuple[str, ...] = (".sh",)) -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tar:
        for name, content in files.items():
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(executable_suffixes) else 0o644
            tar.addfile(info, io.BytesIO(data))
    bio.seek(0)
    return bio.getvalue()


def make_dir_tar(src_dir: Path) -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tar:
        for path in src_dir.rglob("*"):
            if path.is_dir():
                continue
            if ".git" in path.parts:
                continue
            rel = path.relative_to(src_dir)
            tar.add(path, arcname=str(rel))
    bio.seek(0)
    return bio.getvalue()


def copy_text_from_container(container, path: str) -> str:
    try:
        chunks, _ = container.get_archive(path)
    except Exception:
        return ""
    data = b"".join(chunks)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r") as tar:
        members = tar.getmembers()
        if not members:
            return ""
        extracted = tar.extractfile(members[0])
        if extracted is None:
            return ""
        return extracted.read().decode("utf-8", errors="replace")


def pro_test_description(sample: dict) -> str:
    selected = literal_list(sample["selected_test_files_to_run"])
    selected_text = " ".join(shlex.quote(x) for x in selected) if selected else "<selected files>"
    return (
        "The repository is mounted at /app. The official SWE-bench Pro tests for "
        "this instance can be run with "
        f"`cd /app && bash /workspace/run_script.sh {selected_text}` after "
        "the benchmark run_script has been copied to /workspace. Use exactly this "
        "command shape when running validation tests."
    )


def apply_patch_cmd(patch_path: str, workdir: str) -> str:
    quoted = shlex.quote(patch_path)
    return (
        f"if [ -s {quoted} ]; then "
        f"git -C {shlex.quote(workdir)} apply --whitespace=nowarn --recount {quoted} "
        f"|| patch --batch --fuzz=5 -d {shlex.quote(workdir)} -p1 < {quoted}; "
        "fi"
    )


def agent_venv_setup_cmd() -> str:
    return (
        "set -eo pipefail; "
        "PYTHON_BIN=$(command -v python || command -v python3 || true); "
        'if [ -z "$PYTHON_BIN" ]; then echo "ERROR: python not found" >&2; exit 1; fi; '
        f"rm -rf {shlex.quote(AGENT_VENV)} {shlex.quote(AGENT_SITE)}; "
        f"if ! \"$PYTHON_BIN\" -m venv {shlex.quote(AGENT_VENV)}; then "
        f"rm -rf {shlex.quote(AGENT_VENV)}; "
        f"\"$PYTHON_BIN\" -m venv --without-pip {shlex.quote(AGENT_VENV)}; "
        "fi; "
        f"mkdir -p {shlex.quote(AGENT_SITE)}; "
        f"{shlex.quote(AGENT_PYTHON)} -c "
        "'import sys; print(\"agent python\", sys.executable, sys.version.split()[0])'; "
        f"if {shlex.quote(AGENT_PYTHON)} -m pip --version >/dev/null 2>&1; then "
        f"{shlex.quote(AGENT_PYTHON)} -m pip --version; "
        'elif "$PYTHON_BIN" -m pip --version >/dev/null 2>&1; then '
        '"$PYTHON_BIN" -m pip --version; '
        f"echo 'agent deps will be installed with --target {AGENT_SITE}'; "
        "else "
        'echo "ERROR: neither venv pip nor system pip is available" >&2; exit 1; '
        "fi"
    )


def agent_requirements_install_cmd(
    args: argparse.Namespace,
    constraints_path: str = "",
    python_bin: str = AGENT_PYTHON,
) -> str:
    env_prefix = [
        "env",
        "-u",
        "PIP_INDEX_URL",
        "-u",
        "PIP_EXTRA_INDEX_URL",
        "PIP_CONFIG_FILE=/dev/null",
    ]
    pip_args = [
        "install",
        "--index-url",
        args.agent_pip_index_url,
        "--default-timeout",
        str(args.agent_pip_timeout),
        "--prefer-binary",
        "--disable-pip-version-check",
        "--no-input",
    ]
    if args.agent_pip_extra_index_url:
        pip_args.extend(["--extra-index-url", args.agent_pip_extra_index_url])
    for host in [h.strip() for h in args.agent_pip_trusted_host.split(",") if h.strip()]:
        pip_args.extend(["--trusted-host", host])
    if constraints_path:
        pip_args.extend(["--constraint", constraints_path])
    pip_args.extend(["-r", "/hgm/requirements.txt"])

    venv_cmd = " ".join(
        shlex.quote(part)
        for part in [*env_prefix, python_bin, "-m", "pip", *pip_args]
    )
    target_args_cmd = " ".join(
        shlex.quote(part)
        for part in [*pip_args, "--target", AGENT_SITE, "--upgrade"]
    )
    env_prefix_cmd = " ".join(shlex.quote(part) for part in env_prefix)
    return (
        "set -eo pipefail; "
        "PYTHON_BIN=$(command -v python || command -v python3 || true); "
        f"mkdir -p {shlex.quote(AGENT_SITE)}; "
        f"if {shlex.quote(python_bin)} -m pip --version >/dev/null 2>&1; then "
        f"{venv_cmd}; "
        "else "
        'if [ -z "$PYTHON_BIN" ]; then echo "ERROR: python not found" >&2; exit 1; fi; '
        f"{env_prefix_cmd} \"$PYTHON_BIN\" -m pip {target_args_cmd}; "
        "fi"
    )


def eval_script(sample: dict, patch_path: str) -> str:
    before_repo_set_cmd = str(sample["before_repo_set_cmd"]).strip().split("\n")[-1]
    selected_files = ",".join(literal_list(sample["selected_test_files_to_run"]))
    base_commit = sample["base_commit"]
    excludes = "\\n".join(EXCLUDE_PATTERNS)
    return f"""#!/bin/bash
set -euo pipefail

printf '{excludes}\\n' >> /app/.git/info/exclude
cd /app
git reset --hard {base_commit}
git clean -fd
git checkout {base_commit}
{apply_patch_cmd(patch_path, "/app")}
{before_repo_set_cmd}
set +e
timeout {int(os.environ.get("SWE_PRO_EVAL_TIMEOUT", "7200"))} bash /workspace/run_script.sh {selected_files} > /workspace/stdout.log 2> /workspace/stderr.log
RUN_EXIT=$?
set -e
echo "${{RUN_EXIT}}" > /workspace/run_exit_code.txt
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""


def refresh_patch_cmd(base_commit: str) -> str:
    excludes = "\\n".join(EXCLUDE_PATTERNS)
    return (
        f"printf '{excludes}\\n' >> /app/.git/info/exclude && "
        "git -C /app ls-files --others --exclude-standard -z | "
        "xargs -0 -r git -C /app add --intent-to-add -- && "
        f"git -C /app diff --binary {base_commit} -- . > /hgm/model_patch.diff"
    )


def _pull_image_with_retry(
    client,
    image: str,
    args: argparse.Namespace,
    attempts: int = 3,
    base_delay: float = 5.0,
) -> None:
    # docker-py's images.pull() does a `pull` followed by an immediate
    # `inspect_image`, and we occasionally see the inspect return 404 even
    # though the registry has the tag (transient daemon/registry hiccup, or
    # SSH-tunnelled remote daemon mid-pull). Retry a few times before giving
    # up so a single flaky pull doesn't sink the whole batch.
    last_exc: Exception | None = None
    for attempt in range(1, attempts + 1):
        try:
            if args.docker_platform:
                client.images.pull(image, platform=args.docker_platform)
            else:
                client.images.pull(image)
            return
        except Exception as exc:
            last_exc = exc
            if attempt >= attempts:
                break
            time.sleep(base_delay * attempt)
            try:
                client.images.get(image)
                return
            except Exception:
                pass
    assert last_exc is not None
    raise last_exc


def ensure_image(client, image: str, args: argparse.Namespace) -> None:
    if args.pull == "always":
        _pull_image_with_retry(client, image, args)
        return
    try:
        client.images.get(image)
    except Exception:
        if args.pull == "never":
            raise
        _pull_image_with_retry(client, image, args)


def prepare_workspace_files(sample: dict, upstream_dir: Path) -> dict[str, str]:
    instance_id = sample["instance_id"]
    return {
        "run_script.sh": load_text(upstream_dir / "run_scripts" / instance_id / "run_script.sh"),
        "parser.py": load_text(upstream_dir / "run_scripts" / instance_id / "parser.py"),
    }


def run_one(
    sample: dict,
    args: argparse.Namespace,
    agent_label: str,
    agent_patch_paths: list[str],
    output_dir: Path,
    upstream_dir: Path,
    init_agent_src: Path,
    agent_entrypoint: str,
) -> tuple[str, bool, str]:
    instance_id = sample["instance_id"]
    item_dir = output_dir / instance_id
    item_dir.mkdir(parents=True, exist_ok=True)
    summary_path = item_dir / "summary.json"
    if summary_path.exists() and not args.redo:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            return instance_id, bool(summary.get("resolved")), "cached"
        except Exception:
            pass

    client = docker.from_env(timeout=args.docker_timeout)
    image = dockerhub_image_from_row(sample, args.dockerhub_username)

    container = None
    name = f"swe-pro-agent-{int(time.time())}-{abs(hash((agent_label, instance_id))) % 10_000_000}"
    try:
        # ensure_image() is intentionally inside the try block: a flaky Docker
        # Hub pull for one instance must not crash the whole evaluation.
        ensure_image(client, image, args)
        create_kwargs = {
            "image": image,
            "name": name,
            "entrypoint": "/bin/bash",
            "command": ["-lc", "tail -f /dev/null"],
            "detach": True,
        }
        # Container needs to reach the vLLM server, which lives on the Docker
        # host's loopback (the SSH reverse tunnel binds VM:127.0.0.1:VLLM_PORT
        # only). With the default bridge network the container's own loopback
        # is isolated, so the agent gets "Connection error" and exits with an
        # empty patch. Sharing the host's network namespace makes 127.0.0.1
        # inside the container resolve to the VM's loopback. --block-network
        # still wins when explicitly requested.
        if args.block_network:
            create_kwargs["network_mode"] = "none"
        else:
            create_kwargs["network_mode"] = os.getenv(
                "SWE_PRO_CONTAINER_NETWORK", "host"
            )
        if args.docker_platform:
            create_kwargs["platform"] = args.docker_platform
        container = client.containers.create(**create_kwargs)
        container.start()

        container.exec_run(["/bin/bash", "-lc", "mkdir -p /hgm /workspace"], workdir="/")
        container.put_archive("/hgm", make_dir_tar(init_agent_src))
        if agent_entrypoint != "coding_agent.py":
            result = container.exec_run(
                [
                    "/bin/bash",
                    "-lc",
                    f"cp /hgm/{shlex.quote(agent_entrypoint)} /hgm/coding_agent.py",
                ],
                workdir="/",
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Failed to select agent entrypoint {agent_entrypoint}: "
                    f"{result.output.decode('utf-8', errors='replace')[:1000]}"
                )
        container.put_archive("/workspace", make_tar(prepare_workspace_files(sample, upstream_dir)))
        constraints_container_path = ""
        if args.agent_pip_constraints:
            constraints_path = Path(args.agent_pip_constraints)
            if constraints_path.exists():
                constraints_container_path = "/workspace/agent_constraints.txt"
                container.put_archive(
                    "/workspace",
                    make_tar({"agent_constraints.txt": constraints_path.read_text(encoding="utf-8")}),
                )

        for idx, patch_path in enumerate(agent_patch_paths):
            patch_name = f"agent_patch_{idx}.diff"
            container.put_archive(
                "/hgm",
                make_tar({patch_name: Path(patch_path).read_text(encoding="utf-8")}),
            )
            result = container.exec_run(
                ["/bin/bash", "-lc", apply_patch_cmd(f"/hgm/{patch_name}", "/hgm")],
                workdir="/hgm",
            )
            if result.exit_code != 0:
                raise RuntimeError(
                    f"Failed to apply agent patch {patch_path}: "
                    f"{result.output.decode('utf-8', errors='replace')[:1000]}"
                )

        setup = container.exec_run(
            ["/bin/bash", "-lc", agent_venv_setup_cmd()],
            workdir="/",
        )
        if setup.exit_code != 0:
            (item_dir / "pip_install.log").write_bytes(setup.output or b"")
            raise RuntimeError(f"Agent virtualenv setup failed with exit {setup.exit_code}")

        install = container.exec_run(
            ["/bin/bash", "-lc", agent_requirements_install_cmd(args, constraints_container_path)],
            workdir="/",
        )
        install_log = (
            (setup.output or b"")
            + b"\n--- agent requirements install ---\n"
            + (install.output or b"")
        )
        (item_dir / "pip_install.log").write_bytes(install_log)
        if install.exit_code != 0:
            raise RuntimeError(f"Agent requirement install failed with exit {install.exit_code}")

        chat_file = f"/hgm/{instance_id}.md"
        env_vars = {
            "ANTHROPIC_API_KEY": os.getenv("ANTHROPIC_API_KEY"),
            "AWS_REGION": os.getenv("AWS_REGION"),
            "AWS_REGION_NAME": os.getenv("AWS_REGION_NAME"),
            "AWS_ACCESS_KEY_ID": os.getenv("AWS_ACCESS_KEY_ID"),
            "AWS_SECRET_ACCESS_KEY": os.getenv("AWS_SECRET_ACCESS_KEY"),
            "OPENAI_API_KEY": os.getenv("OPENAI_API_KEY"),
            "OpenRouter_API_KEY": os.getenv("OpenRouter_API_KEY"),
            "VLLM_HOST": os.getenv("VLLM_CONTAINER_HOST", "127.0.0.1"),
            "VLLM_PORT": os.getenv("REMOTE_VLLM_PORT", os.getenv("VLLM_PORT", "8000")),
            "PYTHONPATH": AGENT_SITE,
        }
        cmd = [
            "timeout",
            str(args.agent_timeout),
            AGENT_PYTHON,
            "/hgm/coding_agent.py",
            "--problem_statement",
            sample["problem_statement"],
            "--git_dir",
            "/app",
            "--chat_history_file",
            chat_file,
            "--base_commit",
            sample["base_commit"],
            "--outdir",
            "/hgm",
            "--test_description",
            pro_test_description(sample),
            "--model",
            args.llm,
            "--timeout",
            str(args.agent_timeout),
        ]
        if agent_entrypoint == "coding_agent_polyglot.py":
            cmd.extend(["--language", normalize_agent_language(sample.get("repo_language"))])
        else:
            cmd.extend(["--instance_id", instance_id])
        agent_result = container.exec_run(cmd, environment=env_vars, workdir="/")
        (item_dir / "agent_stdout_stderr.log").write_bytes(agent_result.output or b"")

        refresh = container.exec_run(
            ["/bin/bash", "-lc", refresh_patch_cmd(sample["base_commit"])],
            workdir="/app",
        )
        if refresh.exit_code != 0:
            raise RuntimeError(
                f"Failed to refresh model_patch.diff: "
                f"{refresh.output.decode('utf-8', errors='replace')[:1000]}"
            )

        model_patch = copy_text_from_container(container, "/hgm/model_patch.diff")
        chat_history = copy_text_from_container(container, chat_file)
        (item_dir / "model_patch.diff").write_text(model_patch, encoding="utf-8")
        (item_dir / "chat_history.md").write_text(chat_history, encoding="utf-8")

        eval_path = "/workspace/eval_generated_patch.sh"
        eval_content = eval_script(sample, "/hgm/model_patch.diff")
        container.put_archive("/workspace", make_tar({"eval_generated_patch.sh": eval_content}))
        eval_result = container.exec_run(
            ["timeout", str(args.eval_timeout), "/bin/bash", eval_path],
            workdir="/",
        )
        stdout = copy_text_from_container(container, "/workspace/stdout.log")
        stderr = copy_text_from_container(container, "/workspace/stderr.log")
        output_json = copy_text_from_container(container, "/workspace/output.json")
        (item_dir / "eval_stdout.log").write_text(stdout, encoding="utf-8")
        (item_dir / "eval_stderr.log").write_text(stderr, encoding="utf-8")
        (item_dir / "eval_entryscript.sh").write_text(eval_content, encoding="utf-8")
        if output_json:
            (item_dir / "output.json").write_text(output_json, encoding="utf-8")

        if eval_result.exit_code != 0:
            message = (
                f"eval exit {eval_result.exit_code}: "
                f"{(eval_result.output or b'').decode('utf-8', errors='replace')[:500]}"
            )
            summary = {"instance_id": instance_id, "resolved": False, "error": message}
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return instance_id, False, message

        output = json.loads(output_json)
        passed = {test["name"] for test in output.get("tests", []) if test.get("status") == "PASSED"}
        expected = set(literal_list(sample["fail_to_pass"])) | set(literal_list(sample["pass_to_pass"]))
        resolved = expected <= passed
        summary = {
            "instance_id": instance_id,
            "agent": agent_label,
            "resolved": resolved,
            "agent_exit_code": agent_result.exit_code,
            "num_expected": len(expected),
            "num_passed": len(passed),
            "missing": sorted(expected - passed),
            "empty_patch": not bool(model_patch.strip()),
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return instance_id, resolved, "ok"
    except Exception as exc:
        summary = {"instance_id": instance_id, "agent": agent_label, "resolved": False, "error": repr(exc)}
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return instance_id, False, repr(exc)
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def filter_rows(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.task_id:
        wanted = set()
        for value in args.task_id:
            wanted.update(part for part in re.split(r"[\s,:]+", value) if part)
        rows = [row for row in rows if row["instance_id"] in wanted]
    if args.limit > 0:
        rows = rows[: args.limit]
    return rows


def main() -> int:
    args = parse_args()
    upstream_dir = Path(args.upstream_dir)
    if not (upstream_dir / "run_scripts").exists():
        print("Missing SWEbench_Pro/upstream/run_scripts. Run sync_official_eval_assets.sh first.", file=sys.stderr)
        return 2

    init_agent_src = Path(args.init_agent_src)
    if not init_agent_src.is_absolute():
        init_agent_src = REPO_ROOT / init_agent_src
    agent_entrypoint = resolve_agent_entrypoint(args, init_agent_src)
    if not (init_agent_src / agent_entrypoint).exists():
        print(f"Missing {agent_entrypoint} under {init_agent_src}", file=sys.stderr)
        return 2

    agent_label, agent_patch_paths, agent_kind = resolve_agent(args)
    rows = filter_rows(selected_rows_for_subset(args.subset, args.dataset_name, args.split)[1], args)
    if not rows:
        print("No SWE-bench Pro rows selected.", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "outputs" / f"{agent_kind}_{agent_label}"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "agent": agent_kind,
        "agent_label": agent_label,
        "agent_patch_paths": agent_patch_paths,
        "init_agent_src": str(init_agent_src),
        "agent_entrypoint": agent_entrypoint,
        "llm": args.llm,
        "num_tasks": len(rows),
        "remote_host": args.remote_host,
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")

    print(f"Agent: {agent_kind} ({agent_label})")
    print(f"Tasks: {len(rows)}")
    print(f"Output: {output_dir}")
    print(f"LLM: {args.llm}")
    print(f"Agent entrypoint: {agent_entrypoint}")

    with docker_host_context(
        remote_host=args.remote_host or None,
        remote_user=args.remote_user,
        remote_socket=args.remote_socket,
    ):
        client = docker.from_env(timeout=args.docker_timeout)
        print(f"Connected to Docker daemon: {client.info().get('Name', 'unknown')}")

        results: dict[str, bool] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.max_workers) as executor:
            futures = {
                executor.submit(
                    run_one,
                    row,
                    args,
                    agent_label,
                    agent_patch_paths,
                    output_dir,
                    upstream_dir,
                    init_agent_src,
                    agent_entrypoint,
                ): row["instance_id"]
                for row in rows
            }
            pbar = tqdm(concurrent.futures.as_completed(futures), total=len(futures))
            for future in pbar:
                instance_id, resolved, message = future.result()
                results[instance_id] = resolved
                pbar.set_description(f"Accuracy: {sum(results.values()) / len(results):.2%}")
                if message not in ("ok", "cached"):
                    print(f"{instance_id}: {message}")

    (output_dir / "eval_results.json").write_text(json.dumps(results, indent=2), encoding="utf-8")
    resolved_count = sum(results.values())
    print(f"Overall accuracy: {resolved_count / len(results):.2%} ({resolved_count}/{len(results)})")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
