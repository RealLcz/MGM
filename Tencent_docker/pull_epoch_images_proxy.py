#!/usr/bin/env python3
"""
Pull SWE-bench images from ghcr.io via the local (European) network,
then load them into the remote Docker daemon on the Tencent Cloud VM.

This bypasses the China network issue by:
  1. Downloading image layers from ghcr.io using the SLURM node's fast European network
  2. Constructing a Docker-loadable tar in a temp file
  3. Streaming it into the remote Docker daemon via the SSH-tunneled socket

Prerequisites:
    export DOCKER_HOST="unix:///tmp/docker-remote.sock"
    python3 -m pip install docker requests

Usage:
    python3 -u New/pull_epoch_images_proxy.py small
    python3 -u New/pull_epoch_images_proxy.py medium
    python3 -u New/pull_epoch_images_proxy.py all
    python3 -u New/pull_epoch_images_proxy.py verified
"""

import io
import json
import os
import signal
import sys
import tarfile
import tempfile
import time
from pathlib import Path

import docker
import requests

GHCR_TOKEN_URL = "https://ghcr.io/token"
GHCR_API = "https://ghcr.io/v2"
EPOCH_ORG = "epoch-research"
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
    "django__django-12774", "sphinx-doc__sphinx-7748", "sphinx-doc__sphinx-8036",
    "sphinx-doc__sphinx-8548", "sphinx-doc__sphinx-9229", "django__django-11880",
    "django__django-12143", "django__django-12155", "sphinx-doc__sphinx-9367",
    "django__django-12209", "django__django-11951", "django__django-12193",
    "django__django-12276", "sphinx-doc__sphinx-12304", "django__django-11999",
    "django__django-12039", "django__django-12273", "django__django-12325",
    "django__django-12406", "sphinx-doc__sphinx-10673", "sphinx-doc__sphinx-11510",
    "sphinx-doc__sphinx-7757", "sphinx-doc__sphinx-8269", "sphinx-doc__sphinx-8551",
    "sphinx-doc__sphinx-8638", "django__django-11815", "django__django-11885",
    "django__django-12708", "sphinx-doc__sphinx-7590", "sphinx-doc__sphinx-7985",
    "sphinx-doc__sphinx-8056", "sphinx-doc__sphinx-9461", "django__django-11964",
    "django__django-12308", "sphinx-doc__sphinx-10449", "sphinx-doc__sphinx-7454",
    "sphinx-doc__sphinx-8269", "sphinx-doc__sphinx-9230", "sphinx-doc__sphinx-9281",
]


def get_ids(subset):
    if subset == "small":
        return SMALL_IDS
    elif subset == "medium":
        return MEDIUM_IDS
    elif subset == "all":
        return list(dict.fromkeys(SMALL_IDS + MEDIUM_IDS))
    elif subset == "verified":
        id_file = SCRIPT_DIR / "verified_instance_ids.txt"
        return [l.strip() for l in id_file.read_text().splitlines() if l.strip()]
    else:
        print(f"Unknown subset: {subset}")
        sys.exit(1)


def get_token(repo):
    resp = requests.get(GHCR_TOKEN_URL, params={"scope": f"repository:{repo}:pull"})
    resp.raise_for_status()
    return resp.json()["token"]


def get_manifest(repo, reference, token):
    headers = {
        "Authorization": f"Bearer {token}",
        "Accept": ", ".join([
            "application/vnd.docker.distribution.manifest.v2+json",
            "application/vnd.oci.image.manifest.v1+json",
            "application/vnd.docker.distribution.manifest.list.v2+json",
            "application/vnd.oci.image.index.v1+json",
        ]),
    }
    resp = requests.get(f"{GHCR_API}/{repo}/manifests/{reference}", headers=headers)
    resp.raise_for_status()
    return resp.json()


def download_blob(repo, digest, token, dest_path):
    headers = {"Authorization": f"Bearer {token}"}
    with requests.get(
        f"{GHCR_API}/{repo}/blobs/{digest}", headers=headers, stream=True
    ) as resp:
        resp.raise_for_status()
        with open(dest_path, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 1024):
                if chunk:
                    f.write(chunk)


def pull_single_image(docker_client, instance_id):
    id_lower = instance_id.lower()
    repo = f"{EPOCH_ORG}/swe-bench.eval.x86_64.{id_lower}"
    local_tag = f"sweb.eval.x86_64.{id_lower}:latest"

    token = get_token(repo)
    manifest = get_manifest(repo, "latest", token)

    media_type = manifest.get("mediaType", "")
    if "manifest.list" in media_type or "image.index" in media_type:
        for m in manifest.get("manifests", []):
            plat = m.get("platform", {})
            if plat.get("architecture") == "amd64" and plat.get("os") == "linux":
                manifest = get_manifest(repo, m["digest"], token)
                break
        else:
            raise RuntimeError(f"No amd64/linux manifest found for {instance_id}")

    config_digest = manifest["config"]["digest"]
    layers = manifest["layers"]

    with tempfile.TemporaryDirectory() as tmpdir:
        config_path = os.path.join(tmpdir, "config.json")
        download_blob(repo, config_digest, token, config_path)

        layer_files = []
        total_size = 0
        for i, layer in enumerate(layers):
            fname = f"layer_{i}.tar.gz"
            fpath = os.path.join(tmpdir, fname)
            download_blob(repo, layer["digest"], token, fpath)
            layer_files.append((fname, fpath))
            total_size += os.path.getsize(fpath)

        tar_path = os.path.join(tmpdir, "image.tar")
        with tarfile.open(tar_path, "w") as tar:
            tar.add(config_path, arcname="config.json")
            layer_names = []
            for fname, fpath in layer_files:
                tar.add(fpath, arcname=fname)
                layer_names.append(fname)

            docker_manifest = json.dumps([{
                "Config": "config.json",
                "RepoTags": [local_tag],
                "Layers": layer_names,
            }]).encode()
            ti = tarfile.TarInfo(name="manifest.json")
            ti.size = len(docker_manifest)
            tar.addfile(ti, io.BytesIO(docker_manifest))

        tar_size_mb = os.path.getsize(tar_path) / (1024 * 1024)
        load_timeout = max(300, int(tar_size_mb * 2))
        print(f"loading {tar_size_mb:.1f} MB into Docker (timeout {load_timeout}s) ... ", end="", flush=True)

        def _alarm_handler(signum, frame):
            raise TimeoutError(f"images.load timed out after {load_timeout}s")

        old_handler = signal.signal(signal.SIGALRM, _alarm_handler)
        signal.alarm(load_timeout)
        try:
            with open(tar_path, "rb") as f:
                docker_client.images.load(f)
        finally:
            signal.alarm(0)
            signal.signal(signal.SIGALRM, old_handler)

    return local_tag, total_size


def main():
    subset = sys.argv[1] if len(sys.argv) > 1 else "all"
    ids = get_ids(subset)

    slice_arg = sys.argv[2] if len(sys.argv) > 2 else None
    if slice_arg:
        n, m = map(int, slice_arg.split("/"))
        chunk = (len(ids) + m - 1) // m
        start = (n - 1) * chunk
        end = min(start + chunk, len(ids))
        ids = ids[start:end]
        print(f"Slice {n}/{m}: processing IDs [{start}:{end}] ({len(ids)} total)")
    total = len(ids)

    print("Testing ghcr.io connectivity from this machine ...")
    try:
        resp = requests.get("https://ghcr.io/v2/", timeout=10)
        print(f"  ghcr.io reachable (status {resp.status_code})")
    except Exception as e:
        print(f"  ERROR: Cannot reach ghcr.io from this machine: {e}")
        sys.exit(1)

    print("Connecting to remote Docker daemon ...")
    docker_client = docker.from_env(timeout=3600)
    print(f"  Connected to: {docker_client.info().get('Name', 'unknown')}")

    print("Loading existing images on remote Docker ...")
    existing_tags = set()
    for img in docker_client.images.list():
        for tag in img.tags:
            existing_tags.add(tag)
    print(f"  Found {len(existing_tags)} existing tags")

    print(f"\n=== Downloading {total} images via proxy (subset: {subset}) ===")
    print(f"    Download: SLURM node (Europe) -> ghcr.io (fast)")
    print(f"    Load:     SLURM node -> SSH tunnel -> Tencent VM Docker\n")

    success = 0
    skip = 0
    fail = 0
    failed_ids = []
    total_bytes = 0

    for i, instance_id in enumerate(ids):
        idx = i + 1
        local_tag = f"sweb.eval.x86_64.{instance_id.lower()}:latest"

        if local_tag in existing_tags:
            print(f"[{idx}/{total}] SKIP  {instance_id}")
            skip += 1
            continue

        print(f"[{idx}/{total}] PULL  {instance_id} ... ", end="", flush=True)
        max_retries = 3
        pulled = False
        for attempt in range(1, max_retries + 1):
            t0 = time.time()
            try:
                _, size = pull_single_image(docker_client, instance_id)
                elapsed = time.time() - t0
                size_mb = size / (1024 * 1024)
                print(f"OK ({elapsed:.0f}s, {size_mb:.1f} MB)")
                success += 1
                total_bytes += size
                existing_tags.add(local_tag)
                pulled = True
                break
            except Exception as e:
                elapsed = time.time() - t0
                if attempt < max_retries:
                    backoff = 15 * attempt
                    print(f"retry {attempt}/{max_retries} ({elapsed:.0f}s, {str(e)[:120]}) ... ", end="", flush=True)
                    time.sleep(backoff)
                else:
                    print(f"FAILED ({elapsed:.0f}s)")
                    print(f"  Error: {str(e)[:200]}")
        if not pulled:
            fail += 1
            failed_ids.append(instance_id)

    total_gb = total_bytes / (1024**3)
    print(f"\n=== Done ===")
    print(f"  Pulled:  {success} ({total_gb:.2f} GB)")
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
