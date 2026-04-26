#!/usr/bin/env python3
"""
Pull pre-built SWE-bench instance images from Epoch AI registry
and retag them to match the local naming convention.

Uses the Docker Python SDK (works over remote socket, no docker CLI needed).

Prerequisites: DOCKER_HOST must point to the remote Docker daemon, e.g.:
    export DOCKER_HOST="unix:///tmp/docker-remote.sock"

Usage:
    python -u scripts/pull_epoch_images.py small      # 10 images
    python -u scripts/pull_epoch_images.py medium      # 50 images
    python -u scripts/pull_epoch_images.py all         # small+medium = 60
    python -u scripts/pull_epoch_images.py verified    # all 500 SWE-bench Verified (~30 GB)
"""

import sys
import time
from pathlib import Path

import docker

REGISTRY = "ghcr.io/epoch-research"
SCRIPT_DIR = Path(__file__).resolve().parent

SMALL_IDS = [
    "django__django-10973", "django__django-11066", "django__django-12754",
    "django__django-15930", "django__django-13279", "django__django-16661",
    "django__django-13346", "django__django-10880", "django__django-10999",
    "django__django-11087",
]

MEDIUM_IDS = [
    "django__django-11790", "django__django-12050", "django__django-12262",
    "django__django-12713", "sphinx-doc__sphinx-8475", "sphinx-doc__sphinx-8721",
    "sphinx-doc__sphinx-9320", "sphinx-doc__sphinx-9698", "django__django-11848",
    "django__django-12774", "sphinx-doc__sphinx-7748", "sphinx-doc__sphinx-8035",
    "sphinx-doc__sphinx-8548", "sphinx-doc__sphinx-9229", "django__django-11880",
    "django__django-12143", "django__django-12155", "sphinx-doc__sphinx-9367",
    "django__django-12209", "django__django-11951", "django__django-12193",
    "django__django-12276", "django__django-12304", "django__django-9296",
    "sphinx-doc__sphinx-10466", "django__django-11999", "django__django-12039",
    "django__django-12273", "django__django-12325", "django__django-12406",
    "sphinx-doc__sphinx-10673", "sphinx-doc__sphinx-11510", "sphinx-doc__sphinx-7757",
    "sphinx-doc__sphinx-8265", "sphinx-doc__sphinx-8551", "sphinx-doc__sphinx-8638",
    "django__django-11815", "django__django-11885", "django__django-12708",
    "sphinx-doc__sphinx-7590", "sphinx-doc__sphinx-7985", "sphinx-doc__sphinx-8056",
    "sphinx-doc__sphinx-9461", "django__django-11964", "django__django-12308",
    "sphinx-doc__sphinx-10449", "sphinx-doc__sphinx-7454", "sphinx-doc__sphinx-8269",
    "sphinx-doc__sphinx-9230", "sphinx-doc__sphinx-9281",
]


def get_ids(subset: str) -> list[str]:
    if subset == "small":
        return SMALL_IDS
    elif subset == "medium":
        return MEDIUM_IDS
    elif subset == "all":
        return list(dict.fromkeys(SMALL_IDS + MEDIUM_IDS))
    elif subset == "verified":
        id_file = SCRIPT_DIR / "verified_instance_ids.txt"
        return [line.strip() for line in id_file.read_text().splitlines() if line.strip()]
    else:
        print(f"Unknown subset: {subset}")
        print("Usage: python -u pull_epoch_images.py [small|medium|all|verified]")
        sys.exit(1)


def main():
    subset = sys.argv[1] if len(sys.argv) > 1 else "all"
    ids = get_ids(subset)
    total = len(ids)

    print("Connecting to Docker daemon...")
    client = docker.from_env(timeout=120)
    print(f"Connected to: {client.info().get('Name', 'unknown')}")

    # Pre-fetch all existing image tags in one call to avoid N individual API calls
    print("Loading existing images (this may take a minute over SSH tunnel)...")
    existing_tags = set()
    for img in client.images.list():
        for tag in img.tags:
            existing_tags.add(tag)
    print(f"Found {len(existing_tags)} existing image tags")

    print(f"\n=== Pulling {total} images from Epoch AI registry (subset: {subset}) ===\n")

    success = 0
    skip = 0
    fail = 0
    failed_ids = []

    for i, instance_id in enumerate(ids):
        idx = i + 1
        id_lower = instance_id.lower()
        remote_image = f"{REGISTRY}/swe-bench.eval.x86_64.{id_lower}:latest"
        local_tag = f"sweb.eval.x86_64.{id_lower}:latest"

        if local_tag in existing_tags:
            print(f"[{idx}/{total}] SKIP  {instance_id}")
            skip += 1
            continue

        print(f"[{idx}/{total}] PULL  {instance_id} ... ", end="", flush=True)
        t0 = time.time()
        try:
            image = client.images.pull(remote_image)
            image.tag(local_tag.split(":")[0], tag="latest")
            try:
                client.images.remove(remote_image)
            except Exception:
                pass
            existing_tags.add(local_tag)
            elapsed = time.time() - t0
            print(f"OK ({elapsed:.0f}s)")
            success += 1
        except Exception as e:
            elapsed = time.time() - t0
            err_msg = str(e)[:200]
            print(f"FAILED ({elapsed:.0f}s)")
            print(f"  Error: {err_msg}")
            fail += 1
            failed_ids.append(instance_id)

    print(f"\n=== Done ===")
    print(f"  Pulled:  {success}")
    print(f"  Skipped: {skip}")
    print(f"  Failed:  {fail}")
    print(f"  Total:   {total}")

    if failed_ids:
        print(f"\nFailed images:")
        for fid in failed_ids:
            print(f"  - {fid}")
        print("\nRe-run the script to retry failed images.")
        sys.exit(1)


if __name__ == "__main__":
    main()
