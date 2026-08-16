#!/usr/bin/env python3
"""Generate SWE-bench Pro subset CSV, patch files, and image map."""

from __future__ import annotations

import argparse
import json
from pathlib import Path

import pandas as pd

from swebench_pro_utils import (
    DEFAULT_DATASET,
    DEFAULT_DOCKERHUB_USERNAME,
    DEFAULT_SPLIT,
    DEFAULT_SUBSET,
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
    parser.add_argument("--output-dir", default=str(Path(__file__).resolve().parent / "data"))
    parser.add_argument("--prefix", default="gold")
    return parser.parse_args()


def main() -> int:
    args = parse_args()
    _, rows = selected_rows_for_subset(args.subset, args.dataset_name, args.split)
    out_dir = Path(args.output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    csv_path = out_dir / "test_subset.csv"
    pd.DataFrame(rows).to_csv(csv_path, index=False)

    gold_patches = [
        {
            "instance_id": row["instance_id"],
            "patch": row.get("patch", ""),
            "prefix": args.prefix,
        }
        for row in rows
    ]
    gold_path = out_dir / "gold_patches.json"
    gold_path.write_text(json.dumps(gold_patches, indent=2), encoding="utf-8")

    empty_patches = [
        {
            "instance_id": row["instance_id"],
            "patch": "",
            "prefix": "empty",
        }
        for row in rows
    ]
    empty_path = out_dir / "empty_patches.json"
    empty_path.write_text(json.dumps(empty_patches, indent=2), encoding="utf-8")

    image_map = [
        {
            "instance_id": row["instance_id"],
            "repo": row.get("repo", ""),
            "dockerhub_tag": row.get("dockerhub_tag", ""),
            "source_image": dockerhub_image_from_row(row, args.dockerhub_username),
            "local_alias": local_alias_for_instance(row["instance_id"]),
        }
        for row in rows
    ]
    image_map_path = out_dir / "image_map.json"
    image_map_path.write_text(json.dumps(image_map, indent=2), encoding="utf-8")

    print(f"Wrote {len(rows)} rows")
    print(f"  raw samples:   {csv_path}")
    print(f"  gold patches:  {gold_path}")
    print(f"  empty patches: {empty_path}")
    print(f"  image map:     {image_map_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

