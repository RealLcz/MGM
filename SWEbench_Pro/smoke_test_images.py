#!/usr/bin/env python3
"""Run a lightweight sanity check inside pulled SWE-bench Pro images."""

from __future__ import annotations

import argparse
import sys

import docker

from swebench_pro_utils import (
    DEFAULT_DATASET,
    DEFAULT_DOCKERHUB_USERNAME,
    DEFAULT_REMOTE_SOCKET,
    DEFAULT_REMOTE_USER,
    DEFAULT_SPLIT,
    DEFAULT_SUBSET,
    docker_host_context,
    dockerhub_image_from_row,
    local_alias_for_instance,
    selected_rows_for_subset,
)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--subset", default=str(DEFAULT_SUBSET))
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--dockerhub-username", default=DEFAULT_DOCKERHUB_USERNAME)
    parser.add_argument("--remote-host", default="")
    parser.add_argument("--remote-user", default=DEFAULT_REMOTE_USER)
    parser.add_argument("--remote-socket", default=DEFAULT_REMOTE_SOCKET)
    parser.add_argument("--use-alias", action="store_true")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=900)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _, rows = selected_rows_for_subset(args.subset, args.dataset_name, args.split)
    if args.limit > 0:
        rows = rows[: args.limit]

    script = (
        "set -euo pipefail; "
        "test -d /app; "
        "git -C /app rev-parse --is-inside-work-tree; "
        "git -C /app status --short | head -20; "
        "echo SWEbench-Pro image OK"
    )

    failures: list[str] = []
    with docker_host_context(
        remote_host=args.remote_host or None,
        remote_user=args.remote_user,
        remote_socket=args.remote_socket,
    ):
        client = docker.from_env(timeout=args.timeout)
        print(f"Connected to Docker daemon: {client.info().get('Name', 'unknown')}")
        for idx, row in enumerate(rows, 1):
            instance_id = row["instance_id"]
            image = (
                local_alias_for_instance(instance_id)
                if args.use_alias
                else dockerhub_image_from_row(row, args.dockerhub_username)
            )
            print(f"[{idx}/{len(rows)}] {instance_id}")
            try:
                client.images.get(image)
                output = client.containers.run(
                    image,
                    entrypoint="/bin/bash",
                    command=["-lc", script],
                    remove=True,
                    stdout=True,
                    stderr=True,
                    detach=False,
                )
                print(output.decode("utf-8", errors="replace").strip())
            except Exception as exc:
                print(f"FAILED: {exc}")
                failures.append(instance_id)

    if failures:
        print("\nFailed smoke tests:")
        for instance_id in failures:
            print(f"  - {instance_id}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

