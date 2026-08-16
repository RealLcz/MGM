#!/usr/bin/env python3
"""Pull SWE-bench Pro Docker images into a local or remote Docker daemon."""

from __future__ import annotations

import argparse
import sys
import time

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
    parser.add_argument("--platform", default=None, help="Optional platform, e.g. linux/amd64")
    parser.add_argument("--max-retries", type=int, default=3)
    parser.add_argument("--timeout", type=int, default=3600, help="Docker SDK timeout seconds")
    parser.add_argument("--print-only", action="store_true", help="Print image mapping and exit")
    parser.add_argument("--skip-alias", action="store_true", help="Do not tag local alias images")
    parser.add_argument("--remote-host", default="", help="SSH host for remote Docker, e.g. 43.131.5.182")
    parser.add_argument("--remote-user", default=DEFAULT_REMOTE_USER)
    parser.add_argument("--remote-socket", default=DEFAULT_REMOTE_SOCKET)
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    ids, rows = selected_rows_for_subset(args.subset, args.dataset_name, args.split)
    image_plan = [
        (row["instance_id"], dockerhub_image_from_row(row, args.dockerhub_username), local_alias_for_instance(row["instance_id"]))
        for row in rows
    ]

    if args.print_only:
        for instance_id, source, alias in image_plan:
            print(f"{instance_id}\n  source: {source}\n  alias:  {alias}")
        return 0

    import docker

    print(f"Preparing to pull {len(image_plan)} SWE-bench Pro images")
    if args.remote_host:
        print(f"Remote Docker: {args.remote_user}@{args.remote_host} via {args.remote_socket}")
    else:
        print("Docker: current DOCKER_HOST/local daemon")

    with docker_host_context(
        remote_host=args.remote_host or None,
        remote_user=args.remote_user,
        remote_socket=args.remote_socket,
    ):
        client = docker.from_env(timeout=args.timeout)
        print(f"Connected to Docker daemon: {client.info().get('Name', 'unknown')}")

        existing_tags = {tag for image in client.images.list() for tag in image.tags}
        print(f"Found {len(existing_tags)} existing image tags")

        success = 0
        skipped = 0
        failed: list[tuple[str, str]] = []

        for idx, (instance_id, source, alias) in enumerate(image_plan, 1):
            if source in existing_tags:
                print(f"[{idx}/{len(image_plan)}] SKIP  {instance_id}")
                skipped += 1
                try:
                    image = client.images.get(source)
                    if not args.skip_alias and alias not in existing_tags:
                        repo, tag = alias.rsplit(":", 1)
                        image.tag(repo, tag=tag)
                        existing_tags.add(alias)
                except Exception as exc:
                    print(f"  warning: could not refresh alias {alias}: {exc}")
                continue

            print(f"[{idx}/{len(image_plan)}] PULL  {instance_id} ... ", end="", flush=True)
            pulled = False
            last_error = ""
            for attempt in range(1, args.max_retries + 1):
                t0 = time.time()
                try:
                    if args.platform:
                        image = client.images.pull(source, platform=args.platform)
                    else:
                        image = client.images.pull(source)
                    if not args.skip_alias:
                        repo, tag = alias.rsplit(":", 1)
                        image.tag(repo, tag=tag)
                        existing_tags.add(alias)
                    existing_tags.add(source)
                    elapsed = time.time() - t0
                    print(f"OK ({elapsed:.0f}s)")
                    success += 1
                    pulled = True
                    break
                except Exception as exc:
                    elapsed = time.time() - t0
                    last_error = str(exc)
                    if attempt < args.max_retries:
                        backoff = 10 * attempt
                        print(
                            f"retry {attempt}/{args.max_retries} after {elapsed:.0f}s: "
                            f"{last_error[:160]} ... ",
                            end="",
                            flush=True,
                        )
                        time.sleep(backoff)
                    else:
                        print(f"FAILED ({elapsed:.0f}s)")
                        print(f"  source: {source}")
                        print(f"  error: {last_error[:300]}")
            if not pulled:
                failed.append((instance_id, last_error))

    print("\n=== Done ===")
    print(f"  Pulled:  {success}")
    print(f"  Skipped: {skipped}")
    print(f"  Failed:  {len(failed)}")
    print(f"  Total:   {len(image_plan)}")
    if failed:
        print("\nFailed images:")
        for instance_id, error in failed:
            print(f"  - {instance_id}: {error[:160]}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
