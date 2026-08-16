#!/usr/bin/env python3
"""Evaluate SWE-bench Pro patches using Docker API copies, safe for remote Docker."""

from __future__ import annotations

import argparse
import ast
import concurrent.futures
import io
import json
import os
import re
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

import docker
import pandas as pd
from tqdm import tqdm

from swebench_pro_utils import (
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


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", default=str(DEFAULT_SUBSET))
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--raw-sample-path", default="")
    parser.add_argument("--patch-path", default=str(ROOT / "data" / "gold_patches.json"))
    parser.add_argument("--output-dir", default=str(ROOT / "outputs" / "gold_eval"))
    parser.add_argument("--upstream-dir", default=str(ROOT / "upstream"))
    parser.add_argument("--dockerhub-username", default=DEFAULT_DOCKERHUB_USERNAME)
    parser.add_argument("--remote-host", default="")
    parser.add_argument("--remote-user", default=DEFAULT_REMOTE_USER)
    parser.add_argument("--remote-socket", default=DEFAULT_REMOTE_SOCKET)
    parser.add_argument("--num-workers", type=int, default=1)
    parser.add_argument("--timeout", type=int, default=7200)
    parser.add_argument("--docker-timeout", type=int, default=7200)
    parser.add_argument("--docker-platform", default=None)
    parser.add_argument("--redo", action="store_true")
    parser.add_argument("--block-network", action="store_true")
    parser.add_argument("--limit", type=int, default=0)
    return parser.parse_args()


def strip_binary_hunks(patch: str) -> str:
    if not patch:
        return patch
    sections = re.split(r"(?=^diff --git )", patch, flags=re.MULTILINE)
    kept: list[str] = []
    for section in sections:
        if not section.strip():
            continue
        if re.search(r"^Binary files .* differ$", section, re.MULTILINE):
            continue
        if re.search(r"^GIT binary patch$", section, re.MULTILINE):
            continue
        kept.append(section)
    return "".join(kept)


def load_rows(args: argparse.Namespace) -> list[dict]:
    if args.raw_sample_path:
        df = pd.read_csv(args.raw_sample_path).fillna("")
        rows = [dict(row) for _, row in df.iterrows()]
        subset_ids = set(json.loads(Path(args.subset).read_text(encoding="utf-8")))
        return [row for row in rows if row["instance_id"] in subset_ids]
    _, rows = selected_rows_for_subset(args.subset, args.dataset_name, args.split)
    return rows


def load_patches(path: str | os.PathLike[str]) -> dict[str, dict]:
    with open(path, encoding="utf-8") as f:
        data = json.load(f)
    patches = {}
    for item in data:
        patches[item["instance_id"]] = item
    return patches


def load_text(path: Path) -> str:
    with open(path, encoding="utf-8") as f:
        return f.read()


def literal_list(value: Any) -> list[str]:
    if isinstance(value, list):
        return value
    if value is None or value == "":
        return []
    return list(ast.literal_eval(str(value)))


def create_entryscript(sample: dict, upstream_dir: Path) -> str:
    instance_id = sample["instance_id"]
    base_dockerfile = load_text(upstream_dir / "dockerfiles" / "base_dockerfile" / instance_id / "Dockerfile")
    instance_dockerfile = load_text(upstream_dir / "dockerfiles" / "instance_dockerfile" / instance_id / "Dockerfile")

    env_cmds: list[str] = []
    for dockerfile_content in (base_dockerfile, instance_dockerfile):
        for line in dockerfile_content.splitlines():
            line = line.strip()
            if line.startswith("ENV "):
                env_cmds.append(line.replace("ENV", "export", 1))

    before_repo_set_cmd = str(sample["before_repo_set_cmd"]).strip().split("\n")[-1]
    selected_files = ",".join(literal_list(sample["selected_test_files_to_run"]))
    base_commit = sample["base_commit"]
    env_exports = "\n".join(env_cmds)
    return f"""#!/bin/bash
set -euo pipefail
{env_exports}

cd /app
git reset --hard {base_commit}
git clean -fd
git checkout {base_commit}
git apply -v /workspace/patch.diff
{before_repo_set_cmd}
bash /workspace/run_script.sh {selected_files} > /workspace/stdout.log 2> /workspace/stderr.log
python /workspace/parser.py /workspace/stdout.log /workspace/stderr.log /workspace/output.json
"""


def make_tar(files: dict[str, str | bytes]) -> bytes:
    bio = io.BytesIO()
    with tarfile.open(fileobj=bio, mode="w") as tar:
        for name, content in files.items():
            data = content if isinstance(content, bytes) else content.encode("utf-8")
            info = tarfile.TarInfo(name=name)
            info.size = len(data)
            info.mode = 0o755 if name.endswith(".sh") else 0o644
            tar.addfile(info, io.BytesIO(data))
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


def prepare_files(sample: dict, patch: str, upstream_dir: Path) -> tuple[dict[str, str], str]:
    instance_id = sample["instance_id"]
    run_script = load_text(upstream_dir / "run_scripts" / instance_id / "run_script.sh")
    parser_script = load_text(upstream_dir / "run_scripts" / instance_id / "parser.py")
    entryscript = create_entryscript(sample, upstream_dir)
    cleaned_patch = strip_binary_hunks(patch)
    files = {
        "patch.diff": cleaned_patch,
        "run_script.sh": run_script,
        "parser.py": parser_script,
        "entryscript.sh": entryscript,
    }
    return files, entryscript


def evaluate_one(
    sample: dict,
    patch_item: dict,
    args: argparse.Namespace,
    output_dir: Path,
    upstream_dir: Path,
) -> tuple[str, bool, str]:
    instance_id = sample["instance_id"]
    prefix = patch_item.get("prefix", "run") or "run"
    item_dir = output_dir / instance_id
    item_dir.mkdir(parents=True, exist_ok=True)
    result_path = item_dir / f"{prefix}_output.json"
    summary_path = item_dir / f"{prefix}_summary.json"
    if not args.redo and summary_path.exists():
        try:
            summary = json.loads(summary_path.read_text(encoding="utf-8"))
            return instance_id, bool(summary.get("resolved")), "cached"
        except Exception:
            pass

    patch = patch_item.get("model_patch", patch_item.get("patch", ""))
    files, entryscript = prepare_files(sample, patch, upstream_dir)
    (item_dir / f"{prefix}_patch.diff").write_text(files["patch.diff"], encoding="utf-8")
    (item_dir / f"{prefix}_entryscript.sh").write_text(entryscript, encoding="utf-8")

    client = docker.from_env(timeout=args.docker_timeout)
    image = dockerhub_image_from_row(sample, args.dockerhub_username)
    try:
        client.images.get(image)
    except Exception:
        if args.docker_platform:
            client.images.pull(image, platform=args.docker_platform)
        else:
            client.images.pull(image)

    container = None
    name = f"swe-pro-{int(time.time())}-{abs(hash(instance_id)) % 10_000_000}"
    try:
        run_kwargs = {
            "image": image,
            "name": name,
            "entrypoint": "/bin/bash",
            "command": ["-lc", "tail -f /dev/null"],
            "detach": True,
        }
        if args.block_network:
            run_kwargs["network_mode"] = "none"
        if args.docker_platform:
            run_kwargs["platform"] = args.docker_platform
        container = client.containers.create(**run_kwargs)
        container.start()
        container.exec_run(["/bin/bash", "-lc", "mkdir -p /workspace"], workdir="/")
        container.put_archive("/workspace", make_tar(files))
        exec_result = container.exec_run(
            ["timeout", str(args.timeout), "/bin/bash", "/workspace/entryscript.sh"],
            workdir="/",
        )
        stdout = copy_text_from_container(container, "/workspace/stdout.log")
        stderr = copy_text_from_container(container, "/workspace/stderr.log")
        output_json = copy_text_from_container(container, "/workspace/output.json")
        (item_dir / f"{prefix}_stdout.log").write_text(stdout, encoding="utf-8")
        (item_dir / f"{prefix}_stderr.log").write_text(stderr, encoding="utf-8")
        if output_json:
            result_path.write_text(output_json, encoding="utf-8")

        if exec_result.exit_code != 0:
            message = f"entryscript exit {exec_result.exit_code}: {exec_result.output.decode('utf-8', errors='replace')[:500]}"
            summary = {"instance_id": instance_id, "resolved": False, "error": message}
            summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
            return instance_id, False, message

        output = json.loads(output_json)
        passed = {test["name"] for test in output.get("tests", []) if test.get("status") == "PASSED"}
        expected = set(literal_list(sample["fail_to_pass"])) | set(literal_list(sample["pass_to_pass"]))
        resolved = expected <= passed
        summary = {
            "instance_id": instance_id,
            "resolved": resolved,
            "num_expected": len(expected),
            "num_passed": len(passed),
            "missing": sorted(expected - passed),
        }
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return instance_id, resolved, "ok"
    except Exception as exc:
        summary = {"instance_id": instance_id, "resolved": False, "error": repr(exc)}
        summary_path.write_text(json.dumps(summary, indent=2), encoding="utf-8")
        return instance_id, False, repr(exc)
    finally:
        if container is not None:
            try:
                container.remove(force=True)
            except Exception:
                pass


def main() -> int:
    args = parse_args()
    upstream_dir = Path(args.upstream_dir)
    required_dirs = [
        upstream_dir / "run_scripts",
        upstream_dir / "dockerfiles" / "base_dockerfile",
        upstream_dir / "dockerfiles" / "instance_dockerfile",
    ]
    missing = [str(path) for path in required_dirs if not path.exists()]
    if missing:
        print("Missing official evaluation assets:", file=sys.stderr)
        for path in missing:
            print(f"  - {path}", file=sys.stderr)
        print("Run SWEbench_Pro/sync_official_eval_assets.sh first.", file=sys.stderr)
        return 2

    rows = load_rows(args)
    if args.limit > 0:
        rows = rows[: args.limit]
    patches = load_patches(args.patch_path)
    rows = [row for row in rows if row["instance_id"] in patches]
    if not rows:
        print("No matching rows to evaluate.", file=sys.stderr)
        return 2

    output_dir = Path(args.output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    with docker_host_context(
        remote_host=args.remote_host or None,
        remote_user=args.remote_user,
        remote_socket=args.remote_socket,
    ):
        client = docker.from_env(timeout=args.docker_timeout)
        print(f"Connected to Docker daemon: {client.info().get('Name', 'unknown')}")
        results: dict[str, bool] = {}
        with concurrent.futures.ThreadPoolExecutor(max_workers=args.num_workers) as executor:
            futures = {
                executor.submit(
                    evaluate_one,
                    row,
                    patches[row["instance_id"]],
                    args,
                    output_dir,
                    upstream_dir,
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

    result_path = output_dir / "eval_results.json"
    result_path.write_text(json.dumps(results, indent=2), encoding="utf-8")
    accuracy = sum(results.values()) / len(results)
    print(f"Overall accuracy: {accuracy:.2%} ({sum(results.values())}/{len(results)})")
    print(f"Results: {result_path}")
    return 0 if all(results.values()) else 1


if __name__ == "__main__":
    raise SystemExit(main())
