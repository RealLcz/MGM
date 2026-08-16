#!/usr/bin/env python3
"""Stratified random sample of SWE-bench Multilingual tasks for held-out eval."""

from __future__ import annotations

import argparse
import json
import random
import sys
from collections import Counter, defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from SWEbench_Multilingual.swebench_ml_utils import (  # noqa: E402
    DEFAULT_DATASET,
    DEFAULT_SPLIT,
    LANGUAGE_TARGETS_60,
    load_multilingual_rows,
    load_repo_languages,
    language_for_repo,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUT = ROOT / "subsets" / "heldout_60.json"
DEFAULT_META = ROOT / "subsets" / "heldout_60_meta.json"


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--num-tasks", type=int, default=60)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--dataset-name", default=DEFAULT_DATASET)
    parser.add_argument("--split", default=DEFAULT_SPLIT)
    parser.add_argument("--exclude", nargs="*", default=[], help="instance_ids to exclude")
    parser.add_argument("--output", type=Path, default=DEFAULT_OUT)
    parser.add_argument("--meta-output", type=Path, default=DEFAULT_META)
    return parser.parse_args()


def proportional_targets(num_tasks: int, rows: list[dict], repo_languages: dict[str, str]) -> dict[str, int]:
    if num_tasks == 60:
        return dict(LANGUAGE_TARGETS_60)

    by_lang: dict[str, list[str]] = defaultdict(list)
    for row in rows:
        lang = language_for_repo(row["repo"], repo_languages)
        by_lang[lang].append(row["instance_id"])

    total = sum(len(v) for v in by_lang.values())
    raw = {lang: num_tasks * len(ids) / total for lang, ids in by_lang.items()}
    targets = {lang: int(raw[lang]) for lang in raw}
    remainder = num_tasks - sum(targets.values())
    for lang, _ in sorted(raw.items(), key=lambda item: item[1] - targets[item[0]], reverse=True):
        if remainder <= 0:
            break
        targets[lang] += 1
        remainder -= 1
    return targets


def sample_subset(args: argparse.Namespace) -> tuple[list[str], dict]:
    repo_languages = load_repo_languages()
    rows = load_multilingual_rows(args.dataset_name, args.split)
    exclude = set(args.exclude)
    by_lang: dict[str, list[dict]] = defaultdict(list)
    for row in rows:
        if row["instance_id"] in exclude:
            continue
        lang = language_for_repo(row["repo"], repo_languages)
        by_lang[lang].append(row)

    targets = proportional_targets(args.num_tasks, rows, repo_languages)
    rng = random.Random(args.seed)
    selected: list[str] = []
    per_lang: dict[str, list[str]] = {}

    for lang in sorted(targets):
        pool = by_lang.get(lang, [])
        need = targets[lang]
        if len(pool) < need:
            raise RuntimeError(
                f"Not enough tasks for language {lang}: need {need}, have {len(pool)}"
            )
        picked = rng.sample(pool, need)
        ids = [row["instance_id"] for row in picked]
        per_lang[lang] = ids
        selected.extend(ids)

    rng.shuffle(selected)
    meta = {
        "dataset": args.dataset_name,
        "split": args.split,
        "seed": args.seed,
        "num_tasks": len(selected),
        "language_targets": targets,
        "language_selected": {lang: len(ids) for lang, ids in per_lang.items()},
        "repo_counts": dict(Counter(
            row["repo"] for row in rows if row["instance_id"] in set(selected)
        )),
        "languages": per_lang,
        "instance_ids": selected,
    }
    return selected, meta


def main() -> int:
    args = parse_args()
    selected, meta = sample_subset(args)
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(selected, indent=2) + "\n", encoding="utf-8")
    args.meta_output.write_text(json.dumps(meta, indent=2) + "\n", encoding="utf-8")

    print(f"Wrote {len(selected)} tasks to {args.output}")
    print(f"Meta: {args.meta_output}")
    print("Language counts:", meta["language_selected"])
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
