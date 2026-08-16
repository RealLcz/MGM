#!/usr/bin/env python3
"""Pull SWE-bench Multilingual instance images into local Apptainer .sif files."""

from __future__ import annotations

import json
import os
import sys
import time
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from utils.container_runtime import container_from_env  # noqa: E402
from SWEbench_Multilingual.swebench_ml_utils import (  # noqa: E402
    DEFAULT_SUBSET,
    selected_rows_for_subset,
)


def main() -> int:
    subset = sys.argv[1] if len(sys.argv) > 1 else str(DEFAULT_SUBSET)
    _, rows = selected_rows_for_subset(subset)
    if not rows:
        print(f"No rows in subset {subset}", file=sys.stderr)
        return 2

    client = container_from_env(timeout=7200)
    image_dir = Path(
        os.environ.get(
            "APPTAINER_IMAGE_DIR",
            "/mnt/vast/home/ym56kacy/jinhe/MendelGM/apptainer_images",
        )
    )
    image_dir.mkdir(parents=True, exist_ok=True)

    existing_tags: set[str] = set()
    for meta in image_dir.glob("*.json"):
        try:
            existing_tags.update(json.loads(meta.read_text()).get("tags", []))
        except Exception:
            pass

    total = len(rows)
    success = skip = fail = 0
    failed: list[str] = []

    print(f"Pulling up to {total} Multilingual images into {image_dir}")
    for i, row in enumerate(rows, 1):
        instance_id = row["instance_id"]
        from utils.swebench_compat import make_test_spec

        tag = make_test_spec(row, namespace=os.environ.get("SWE_ML_IMAGE_NAMESPACE", "swebench")).instance_image_key
        if tag in existing_tags:
            print(f"[{i}/{total}] SKIP  {instance_id}")
            skip += 1
            continue

        print(f"[{i}/{total}] PULL  {instance_id} ({tag}) ... ", end="", flush=True)
        t0 = time.time()
        try:
            client.images.pull(tag)
            existing_tags.add(tag)
            print(f"OK ({time.time() - t0:.0f}s)")
            success += 1
        except Exception as exc:
            print(f"FAILED ({time.time() - t0:.0f}s)")
            print(f"  Error: {str(exc)[:200]}")
            fail += 1
            failed.append(instance_id)

    print(f"\nDone: pulled={success} skipped={skip} failed={fail} total={total}")
    if failed:
        for fid in failed:
            print(f"  - {fid}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
