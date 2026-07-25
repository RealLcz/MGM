#!/usr/bin/env python3
"""Run a Polyglot HGM/MGM agent on SWE-bench Multilingual and grade patches."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import datetime
import json
import os
import re
import shlex
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils import apptainer_errors as container_errors

from prompts.testrepo_prompt import get_test_description  # noqa: E402
from polyglot.docker_utils import exec_run_with_timeout  # noqa: E402
from swebench.harness.constants import DOCKER_USER  # noqa: E402
from swebench.harness.docker_build import BuildImageError, build_env_images, cleanup_container  # noqa: E402
from SWEbench_Multilingual.swebench_ml_utils import (  # noqa: E402
    DEFAULT_DATASET,
    DEFAULT_SPLIT,
    DEFAULT_SUBSET,
    ROOT,
    language_for_repo,
    selected_rows_for_subset,
)
SWE_PRO_DIR = REPO_ROOT / "SWEbench_Pro"
if str(SWE_PRO_DIR) not in sys.path:
    sys.path.insert(0, str(SWE_PRO_DIR))

from run_agent_eval import (  # noqa: E402  # SWEbench_Pro/run_agent_eval.py
    AGENT_PYTHON,
    AGENT_SITE,
    AGENT_VENV,
    agent_requirements_install_cmd,
    agent_venv_setup_cmd,
    apply_patch_cmd,
    find_best_node,
    make_dir_tar,
    make_tar,
)
from swe_bench.harness import build_container_with_network, container_ulimits, raise_host_open_file_limit  # noqa: E402
from swe_bench.utils import copy_from_container, copy_to_container, remove_existing_container, setup_logger  # noqa: E402
from utils.container_runtime import container_from_env  # noqa: E402
from utils.evo_utils import get_model_patch_paths  # noqa: E402
from utils.swebench_compat import get_eval_report, make_test_spec  # noqa: E402

ML_IMAGE_NAMESPACE = os.environ.get("SWE_ML_IMAGE_NAMESPACE", "swebench")


def make_ml_test_spec(entry: dict):
    return make_test_spec(entry, namespace=ML_IMAGE_NAMESPACE)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--agent", choices=["initial", "best", "node"], default="node")
    parser.add_argument("--node-id", default="")
    parser.add_argument("--node-path", default="", help="Absolute path to node folder")
    parser.add_argument("--hgm-output-dir", default="")
    parser.add_argument("--init-agent-src", default="initial_polyglot/default_agent/src")
    parser.add_argument("--subset", default=str(DEFAULT_SUBSET))
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--output-dir", default="")
    parser.add_argument("--llm", default=os.environ.get("HGM_LLM_MODEL_ID", "Qwen/Qwen3.6-35B-A3B"))
    parser.add_argument("--max-workers", type=int, default=1)
    parser.add_argument("--limit", type=int, default=0)
    parser.add_argument("--task-id", action="append", default=[])
    parser.add_argument("--agent-timeout", type=int, default=3600)
    parser.add_argument("--eval-timeout", type=int, default=7200)
    parser.add_argument("--container-timeout", type=int, default=7200, help="Apptainer pull/exec timeout (seconds)")
    parser.add_argument("--agent-pip-index-url", default=os.environ.get("SWE_ML_AGENT_PIP_INDEX_URL", "https://pypi.org/simple"))
    parser.add_argument("--agent-pip-constraints", default=os.environ.get("SWE_ML_AGENT_PIP_CONSTRAINTS", "SWEbench_Pro/agent_constraints.txt"))
    parser.add_argument("--pull", choices=["missing", "never", "always"], default="missing")
    parser.add_argument("--redo", action="store_true")
    return parser.parse_args()


def literal_list(value) -> list[str]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return list(ast.literal_eval(str(value)))


def resolve_node(args: argparse.Namespace) -> tuple[str, list[str], str, Path]:
    if args.node_path:
        node_dir = Path(args.node_path).resolve()
        if not (node_dir / "metadata.json").exists():
            raise FileNotFoundError(f"Missing metadata.json under {node_dir}")
        hgm_output_dir = node_dir.parent
        node_id = node_dir.name
        patch_paths = get_model_patch_paths(str(REPO_ROOT), str(hgm_output_dir), node_id)
        return node_id, patch_paths, "node", hgm_output_dir

    hgm_output_dir = Path(args.hgm_output_dir or "output_polyglot")
    if not hgm_output_dir.is_absolute():
        hgm_output_dir = REPO_ROOT / hgm_output_dir

    if args.agent == "initial":
        return "initial", [], "initial", hgm_output_dir

    if args.agent == "best":
        node_id = find_best_node(hgm_output_dir)
    else:
        if not args.node_id:
            raise ValueError("--node-id or --node-path is required when --agent node")
        node_id = args.node_id

    patch_paths = get_model_patch_paths(str(REPO_ROOT), str(hgm_output_dir), node_id)
    return node_id, patch_paths, args.agent, hgm_output_dir


def filter_rows(rows: list[dict], args: argparse.Namespace) -> list[dict]:
    if args.task_id:
        wanted: set[str] = set()
        for value in args.task_id:
            wanted.update(part for part in re.split(r"[\s,:]+", value) if part)
        rows = [row for row in rows if row["instance_id"] in wanted]
    if args.limit > 0:
        rows = rows[: args.limit]
    return rows


def ensure_image(client, image: str, pull: str) -> None:
    if pull == "always":
        client.images.pull(image)
        return
    try:
        client.images.get(image)
    except Exception:
        if pull == "never":
            raise
        client.images.pull(image)


def copy_polyglot_agent(container, init_agent_src: Path) -> None:
    entrypoint = init_agent_src / "coding_agent_polyglot.py"
    if not entrypoint.exists():
        raise FileNotFoundError(f"Missing {entrypoint}")
    for rel in [
        "coding_agent_polyglot.py",
        "requirements.txt",
        "pytest.ini",
        "tools/",
        "utils/",
        "tests/",
        "prompts/",
        "llm.py",
        "llm_withtools.py",
        "LICENSE",
        "README.md",
    ]:
        src = init_agent_src / rel
        if not src.exists():
            continue
        dst = f"/hgm/{rel.rstrip('/')}"
        copy_to_container(container, str(src), dst)
    container.exec_run(
        ["/bin/bash", "-lc", "cp /hgm/coding_agent_polyglot.py /hgm/coding_agent.py"],
        workdir="/",
    )


def refresh_patch_cmd(base_commit: str) -> str:
    excludes = "\\n".join([
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
    ])
    return (
        f"printf '{excludes}\\n' >> /testbed/.git/info/exclude && "
        "git -C /testbed ls-files --others --exclude-standard -z | "
        "xargs -0 -r git -C /testbed add --intent-to-add -- && "
        f"git -C /testbed diff --binary {base_commit} -- . > /hgm/model_patch.diff"
    )


def run_one(
    entry: dict,
    args: argparse.Namespace,
    agent_label: str,
    agent_patch_paths: list[str],
    output_dir: Path,
    init_agent_src: Path,
) -> tuple[str, bool, str]:
    instance_id = entry["instance_id"]
    item_dir = output_dir / instance_id
    item_dir.mkdir(parents=True, exist_ok=True)
    summary_path = item_dir / "summary.json"
    if summary_path.exists() and not args.redo:
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            return instance_id, bool(summary.get("resolved")), "cached"
        except Exception:
            pass

    client = container_from_env(timeout=args.container_timeout)
    container = None
    logger = setup_logger(str(item_dir / f"{instance_id}_docker.log"))
    test_spec = make_ml_test_spec(entry)
    run_id = datetime.datetime.now().strftime("%Y%m%d_%H%M%S_%f")

    try:
        ensure_image(client, test_spec.instance_image_key, args.pull)
        remove_existing_container(client, test_spec.get_instance_container_name(run_id))
        container = build_container_with_network(
            test_spec, client, run_id, logger, nocache=True, force_rebuild=False
        )
        container.start()

        copy_polyglot_agent(container, init_agent_src)
        for idx, patch_path in enumerate(agent_patch_paths):
            patch_name = f"agent_patch_{idx}.diff"
            copy_to_container(container, patch_path, f"/hgm/{patch_name}")
            result = container.exec_run(
                ["/bin/bash", "-lc", apply_patch_cmd(f"/hgm/{patch_name}", "/hgm")],
                workdir="/hgm",
            )
            if result.exit_code != 0:
                raise RuntimeError(result.output.decode("utf-8", errors="replace")[:1000])

        eval_file = item_dir / f"{instance_id}_eval.sh"
        eval_file.write_text(test_spec.eval_script, encoding="utf-8")
        copy_to_container(container, str(eval_file), "/eval.sh")
        setup = container.exec_run("/bin/bash /eval.sh", workdir="/")
        (item_dir / "setup.log").write_bytes(setup.output or b"")
        if setup.exit_code != 0:
            raise RuntimeError(f"Environment setup failed with exit {setup.exit_code}")

        test_description = get_test_description(eval_script=test_spec.eval_script, swerepo=True)
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
            "VLLM_PORT": os.getenv("VLLM_PORT", "8000"),
            "PYTHONPATH": AGENT_SITE,
        }
        setup_agent = container.exec_run(["/bin/bash", "-lc", agent_venv_setup_cmd()], workdir="/")
        if setup_agent.exit_code != 0:
            raise RuntimeError("Agent virtualenv setup failed")
        install = container.exec_run(
            ["/bin/bash", "-lc", agent_requirements_install_cmd(args, "")],
            workdir="/",
        )
        (item_dir / "pip_install.log").write_bytes((setup_agent.output or b"") + (install.output or b""))
        if install.exit_code != 0:
            raise RuntimeError("Agent requirement install failed")

        cmd = [
            "timeout",
            str(args.agent_timeout),
            AGENT_PYTHON,
            "/hgm/coding_agent.py",
            "--problem_statement",
            entry["problem_statement"],
            "--git_dir",
            "/testbed",
            "--chat_history_file",
            chat_file,
            "--base_commit",
            entry["base_commit"],
            "--outdir",
            "/hgm",
            "--test_description",
            test_description,
            "--language",
            language_for_repo(entry["repo"]),
            "--model",
            args.llm,
            "--timeout",
            str(args.agent_timeout),
        ]
        agent_result = container.exec_run(cmd, environment=env_vars, workdir="/")
        (item_dir / "agent_stdout_stderr.log").write_bytes(agent_result.output or b"")

        refresh = container.exec_run(["/bin/bash", "-lc", refresh_patch_cmd(entry["base_commit"])], workdir="/")
        if refresh.exit_code != 0:
            raise RuntimeError("Failed to refresh model_patch.diff")

        copy_from_container(container, "/hgm/model_patch.diff", item_dir / "model_patch.diff")
        copy_from_container(container, chat_file, item_dir / "chat_history.md")
        model_patch = (item_dir / "model_patch.diff").read_text(encoding="utf-8")

        copy_to_container(container, str(item_dir / "model_patch.diff"), "/tmp/patch.diff")
        apply = container.exec_run(
            "if [ ! -s /tmp/patch.diff ]; then true; else git apply -v /tmp/patch.diff; fi",
            workdir="/testbed",
            user=DOCKER_USER,
        )
        if apply.exit_code != 0:
            apply = container.exec_run(
                "patch --batch --fuzz=5 -p1 -i /tmp/patch.diff",
                workdir="/testbed",
                user=DOCKER_USER,
            )
        if apply.exit_code != 0:
            summary = {
                "instance_id": instance_id,
                "resolved": False,
                "error": apply.output.decode("utf-8", errors="replace")[:1000],
            }
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return instance_id, False, "apply_failed"

        copy_to_container(container, str(eval_file), "/eval.sh")
        test_output, timed_out, runtime = exec_run_with_timeout(
            container, "/bin/bash /eval.sh", args.eval_timeout
        )
        test_output_path = item_dir / "test_output.txt"
        test_output_path.write_text(test_output, encoding="utf-8")
        (item_dir / "eval_runtime.txt").write_text(f"{runtime:.2f}\n", encoding="utf-8")
        if timed_out:
            summary = {"instance_id": instance_id, "resolved": False, "error": "eval_timeout"}
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return instance_id, False, "eval_timeout"

        pred = {
            "instance_id": instance_id,
            "model_name_or_path": agent_label,
            "model_patch": model_patch,
        }
        report = get_eval_report(
            test_spec=test_spec,
            prediction=pred,
            log_path=test_output_path,
            include_tests_status=True,
        )
        (item_dir / "report.json").write_text(json.dumps(report, indent=2), encoding="utf-8")
        resolved = bool(report[instance_id]["resolved"])
        summary = {
            "instance_id": instance_id,
            "agent": agent_label,
            "resolved": resolved,
            "language": language_for_repo(entry["repo"]),
            "repo": entry["repo"],
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
                cleanup_container(client, container, logger)
            except Exception:
                try:
                    container.remove(force=True)
                except Exception:
                    pass


def maybe_build_env_images(client, rows: list[dict], max_workers: int) -> None:
    all_exist = True
    for entry in rows:
        spec = make_ml_test_spec(entry)
        try:
            client.images.get(spec.instance_image_key)
        except container_errors.ImageNotFound:
            all_exist = False
            break
    if all_exist:
        print("All instance images already exist, skipping base/env image builds.")
        return
    env_build_workers = int(os.getenv("SWE_ENV_BUILD_WORKERS", str(min(max_workers, 2))))
    _, env_failed = build_env_images(
        client,
        dataset=rows,
        force_rebuild=False,
        max_workers=max(1, env_build_workers),
        instance_image_tag="latest",
        env_image_tag="latest",
    )
    if env_failed:
        print(f"WARNING: {len(env_failed)} environment image(s) failed to build.")


def main() -> int:
    args = parse_args()
    init_agent_src = Path(args.init_agent_src)
    if not init_agent_src.is_absolute():
        init_agent_src = REPO_ROOT / init_agent_src
    if not (init_agent_src / "coding_agent_polyglot.py").exists():
        print(f"Missing coding_agent_polyglot.py under {init_agent_src}", file=sys.stderr)
        return 2

    agent_label, agent_patch_paths, agent_kind, hgm_output_dir = resolve_node(args)
    _, rows = selected_rows_for_subset(args.subset, args.dataset_name, args.split)
    rows = filter_rows(rows, args)
    if not rows:
        print("No SWE-bench Multilingual rows selected.", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir) if args.output_dir else ROOT / "outputs" / f"{agent_kind}_{agent_label}"
    output_dir.mkdir(parents=True, exist_ok=True)
    run_meta = {
        "agent": agent_kind,
        "agent_label": agent_label,
        "agent_patch_paths": agent_patch_paths,
        "init_agent_src": str(init_agent_src),
        "hgm_output_dir": str(hgm_output_dir),
        "llm": args.llm,
        "num_tasks": len(rows),
        "subset": str(args.subset),
    }
    (output_dir / "run_metadata.json").write_text(json.dumps(run_meta, indent=2), encoding="utf-8")

    print(f"Agent: {agent_kind} ({agent_label})")
    print(f"Tasks: {len(rows)}")
    print(f"Output: {output_dir}")
    print(f"LLM: {args.llm}")

    raise_host_open_file_limit()
    client = container_from_env(timeout=args.container_timeout)
    print(f"Connected to Apptainer: {client.info().get('Name', 'unknown')}")
    maybe_build_env_images(client, rows, args.max_workers)

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
                init_agent_src,
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

    resolved_count = sum(results.values())
    eval_results = {
        "agent": agent_kind,
        "agent_label": agent_label,
        "resolved": resolved_count,
        "submitted": len(results),
        "accuracy": resolved_count / len(results) if results else 0.0,
        "results": results,
    }
    (output_dir / "eval_results.json").write_text(json.dumps(eval_results, indent=2), encoding="utf-8")
    print(
        f"Done: {resolved_count}/{len(results)} resolved "
        f"({eval_results['accuracy']:.2%})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
