#!/usr/bin/env python3
"""Shared helpers for SWE-bench Multilingual held-out evaluation."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Iterable

ROOT = Path(__file__).resolve().parent
DEFAULT_DATASET = "SWE-bench/SWE-bench_Multilingual"
DEFAULT_SPLIT = "test"
DEFAULT_SUBSET = ROOT / "subsets" / "heldout_60.json"
REPO_LANGUAGES = ROOT / "repo_languages.json"

LANGUAGE_TARGETS_60 = {
    "c": 6,
    "cpp": 2,
    "go": 8,
    "java": 9,
    "javascript": 9,
    "php": 9,
    "ruby": 8,
    "rust": 9,
}


def load_subset(path: str | Path) -> list[str]:
    path = Path(path)
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, list) or not all(isinstance(x, str) for x in data):
        raise ValueError(f"{path} must be a JSON array of instance_id strings")
    return data


def load_repo_languages() -> dict[str, str]:
    return json.loads(REPO_LANGUAGES.read_text(encoding="utf-8"))


def language_for_repo(repo: str, repo_languages: dict[str, str] | None = None) -> str:
    repo_languages = repo_languages or load_repo_languages()
    return repo_languages[repo]


def load_multilingual_rows(
    dataset_name: str = DEFAULT_DATASET,
    split: str = DEFAULT_SPLIT,
) -> list[dict]:
    from datasets import load_dataset

    dataset = load_dataset(dataset_name, split=split)
    return [dict(row) for row in dataset]


def rows_by_instance_id(rows: Iterable[dict]) -> dict[str, dict]:
    return {row["instance_id"]: row for row in rows}


def selected_rows_for_subset(
    subset_path: str | Path,
    dataset_name: str = DEFAULT_DATASET,
    split: str = DEFAULT_SPLIT,
) -> tuple[list[str], list[dict]]:
    wanted = load_subset(subset_path)
    by_id = rows_by_instance_id(load_multilingual_rows(dataset_name, split))
    missing = [instance_id for instance_id in wanted if instance_id not in by_id]
    if missing:
        raise KeyError(f"Missing {len(missing)} instance ids from {dataset_name}: {missing[:5]}")
    rows = [by_id[instance_id] for instance_id in wanted]
    return wanted, rows
