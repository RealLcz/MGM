#!/usr/bin/env python3
"""Shared helpers for local SWE-bench Pro scripts.

The scripts in this directory intentionally live outside the existing SWE-bench
and Polyglot code paths so they can be used without changing the HGM harness.
"""

from __future__ import annotations

import contextlib
import json
import os
import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator


ROOT = Path(__file__).resolve().parent
DEFAULT_SUBSET = ROOT / "subsets" / "test.json"
DEFAULT_DATASET = "ScaleAI/SWE-bench_Pro"
DEFAULT_SPLIT = "test"
DEFAULT_DOCKERHUB_USERNAME = "jefzda"
DEFAULT_REMOTE_HOST = "43.131.5.182"
DEFAULT_REMOTE_USER = "ubuntu"
DEFAULT_REMOTE_SOCKET = "/tmp/swebench-pro-docker.sock"
DEFAULT_OUTPUT_DIR = ROOT / "outputs"
LOCAL_ALIAS_PREFIX = "swebench-pro.eval.x86_64"


def load_subset(path: str | os.PathLike[str]) -> list[str]:
    """Load a subset file.

    The current subset is a JSON array. For convenience, this also accepts a
    legacy JSONL/list-of-strings file.
    """

    path = Path(path)
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []

    if text[0] == "[":
        data = json.loads(text)
        if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
            raise ValueError(f"{path} must be a JSON array of instance_id strings")
        return data

    ids: list[str] = []
    for lineno, line in enumerate(text.splitlines(), 1):
        line = line.strip()
        if not line:
            continue
        try:
            item = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{lineno} is not JSON") from exc
        if not isinstance(item, str):
            raise ValueError(f"{path}:{lineno} must be a JSON string")
        ids.append(item)
    return ids


def load_swebench_pro_rows(
    dataset_name: str = DEFAULT_DATASET,
    split: str = DEFAULT_SPLIT,
) -> list[dict]:
    """Load SWE-bench Pro rows from HuggingFace datasets."""

    from datasets import load_dataset

    dataset = load_dataset(dataset_name, split=split)
    return [dict(row) for row in dataset]


def rows_by_instance_id(rows: Iterable[dict]) -> dict[str, dict]:
    return {row["instance_id"]: row for row in rows}


def dockerhub_image_from_row(row: dict, dockerhub_username: str) -> str:
    """Return the official Docker Hub image reference for a dataset row."""

    tag = row.get("dockerhub_tag")
    if tag:
        return f"{dockerhub_username}/sweap-images:{tag}"

    # Fallback matching the official helper_code/image_uri.py logic.
    repo = row.get("repo") or ""
    instance_id = row["instance_id"]
    if "/" not in repo:
        raise ValueError(
            f"Row {instance_id} does not contain dockerhub_tag and repo is invalid: {repo!r}"
        )
    repo_base, repo_name_only = repo.lower().split("/", 1)
    hash_part = instance_id.replace("instance_", "", 1)
    if instance_id == "instance_element-hq__element-web-ec0f940ef0e8e3b61078f145f34dc40d1938e6c5-vnan":
        repo_name_only = "element-web"
    elif "element-hq" in repo.lower() and "element-web" in repo.lower():
        repo_name_only = "element"
        if hash_part.endswith("-vnan"):
            hash_part = hash_part[:-5]
    elif hash_part.endswith("-vnan"):
        hash_part = hash_part[:-5]
    tag = f"{repo_base}.{repo_name_only}-{hash_part}"
    if len(tag) > 128:
        tag = tag[:128]
    return f"{dockerhub_username}/sweap-images:{tag}"


def local_alias_for_instance(instance_id: str) -> str:
    return f"{LOCAL_ALIAS_PREFIX}.{instance_id.lower()}:latest"


@dataclass
class SshTunnel:
    proc: subprocess.Popen
    socket_path: str
    old_docker_host: str | None

    def close(self) -> None:
        self.proc.terminate()
        try:
            self.proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.proc.kill()
            self.proc.wait(timeout=5)
        if self.old_docker_host is None:
            os.environ.pop("DOCKER_HOST", None)
        else:
            os.environ["DOCKER_HOST"] = self.old_docker_host


def _ssh_command(
    remote_user: str,
    remote_host: str,
    remote_socket: str,
    remote_docker_socket: str,
) -> list[str]:
    ssh = [
        "ssh",
        "-nNT",
        "-F",
        "/dev/null",
        "-o",
        "StrictHostKeyChecking=no",
        "-o",
        "ServerAliveInterval=30",
        "-o",
        "ServerAliveCountMax=20",
        "-o",
        "ExitOnForwardFailure=yes",
        "-o",
        "NumberOfPasswordPrompts=1",
        "-L",
        f"{remote_socket}:{remote_docker_socket}",
        f"{remote_user}@{remote_host}",
    ]
    if os.environ.get("REMOTE_DOCKER_PASSWORD") and shutil.which("sshpass"):
        return ["sshpass", "-e", *ssh]
    return ssh


@contextlib.contextmanager
def docker_host_context(
    *,
    remote_host: str | None = None,
    remote_user: str = DEFAULT_REMOTE_USER,
    remote_socket: str = DEFAULT_REMOTE_SOCKET,
    remote_docker_socket: str = "/var/run/docker.sock",
    wait_seconds: int = 20,
) -> Iterator[None]:
    """Optionally open an SSH tunnel and set DOCKER_HOST for docker-py.

    If remote_host is not provided, the current DOCKER_HOST environment is used.
    """

    if not remote_host:
        yield
        return

    socket_path = Path(remote_socket)
    try:
        socket_path.unlink()
    except FileNotFoundError:
        pass
    old_docker_host = os.environ.get("DOCKER_HOST")
    cmd = _ssh_command(remote_user, remote_host, remote_socket, remote_docker_socket)
    env = os.environ.copy()
    if os.environ.get("REMOTE_DOCKER_PASSWORD"):
        env["SSHPASS"] = os.environ["REMOTE_DOCKER_PASSWORD"]
    proc = subprocess.Popen(cmd, env=env)
    tunnel = SshTunnel(proc=proc, socket_path=remote_socket, old_docker_host=old_docker_host)
    try:
        deadline = time.time() + wait_seconds
        while time.time() < deadline:
            if proc.poll() is not None:
                raise RuntimeError(f"SSH tunnel exited early with status {proc.returncode}")
            if socket_path.exists():
                break
            time.sleep(0.5)
        else:
            raise TimeoutError(f"Timed out waiting for SSH Docker socket {remote_socket}")

        os.environ["DOCKER_HOST"] = f"unix://{remote_socket}"
        yield
    finally:
        tunnel.close()


def selected_rows_for_subset(
    subset_path: str | os.PathLike[str],
    dataset_name: str = DEFAULT_DATASET,
    split: str = DEFAULT_SPLIT,
) -> tuple[list[str], list[dict]]:
    ids = load_subset(subset_path)
    row_map = rows_by_instance_id(load_swebench_pro_rows(dataset_name, split))
    missing = [instance_id for instance_id in ids if instance_id not in row_map]
    if missing:
        preview = "\n".join(f"  - {x}" for x in missing[:10])
        raise KeyError(
            f"{len(missing)} subset ids were not found in {dataset_name}/{split}:\n{preview}"
        )
    return ids, [row_map[instance_id] for instance_id in ids]

