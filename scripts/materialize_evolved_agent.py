#!/usr/bin/env python3
"""Materialize an evolved HGM/MGM agent into best_agent/<name>/src."""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), ".."))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

from utils.evo_utils import get_model_patch_paths
from utils.git_utils import apply_patch


AGENT_FILES = (
    "coding_agent.py",
    "coding_agent_polyglot.py",
    "llm.py",
    "llm_withtools.py",
    "config.py",
    "config.yaml",
    "tree.py",
    "requirements.txt",
    "pytest.ini",
    "LICENSE",
    "README.md",
)

AGENT_DIRS = (
    ("tools", ("__pycache__", "*.pyc"), ("docker_utils.py", "evo_utils.py")),
    ("utils", ("__pycache__", "*.pyc"), ("docker_utils.py", "evo_utils.py")),
    (
        "prompts",
        ("__pycache__", "*.pyc"),
        ("self_improvement_prompt.py", "diagnose_improvement_prompt.py"),
    ),
    ("tests", ("__pycache__", "*.pyc"), ()),
)


def _copy_agent_src(source_root: str, dest_src: str) -> None:
    """Copy only coding-agent source files, not repo artifacts."""
    os.makedirs(dest_src, exist_ok=True)
    for name in AGENT_FILES:
        src = os.path.join(source_root, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(dest_src, name))

    for dir_name, excludes, file_excludes in AGENT_DIRS:
        src_dir = os.path.join(source_root, dir_name)
        dst_dir = os.path.join(dest_src, dir_name)
        if not os.path.isdir(src_dir):
            continue
        if os.path.exists(dst_dir):
            shutil.rmtree(dst_dir)
        shutil.copytree(
            src_dir,
            dst_dir,
            ignore=shutil.ignore_patterns(*excludes, *file_excludes),
        )


def _git_init_repo(path: str) -> None:
    subprocess.run(["git", "init"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=agent@local", "-c", "user.name=agent", "commit", "-qm", "base"],
        cwd=path,
        check=True,
        capture_output=True,
    )


def materialize_agent(
    *,
    external_root: str,
    hgm_output_dir: str,
    node_id: str,
    dest_dir: str,
    initial_src: str,
) -> str:
    dest_src = os.path.join(dest_dir, "src")
    if os.path.exists(dest_dir):
        shutil.rmtree(dest_dir)
    os.makedirs(dest_dir, exist_ok=True)

    _copy_agent_src(initial_src, dest_src)
    _git_init_repo(dest_src)

    patch_paths = get_model_patch_paths(external_root, hgm_output_dir, node_id)
    for patch_path in patch_paths:
        patch_str = open(patch_path, encoding="utf-8").read()
        if not patch_str.strip():
            continue
        apply_patch(dest_src, patch_str)

    src_node = os.path.join(external_root, hgm_output_dir, node_id)
    for name in ("metadata.json", "model_patch.diff"):
        src_file = os.path.join(src_node, name)
        if os.path.isfile(src_file):
            shutil.copy2(src_file, os.path.join(dest_dir, name))

    manifest = {
        "node_id": node_id,
        "external_root": external_root,
        "hgm_output_dir": hgm_output_dir,
        "patch_paths": patch_paths,
        "src": dest_src,
    }
    with open(os.path.join(dest_dir, "manifest.json"), "w", encoding="utf-8") as f:
        json.dump(manifest, f, indent=2)

    return dest_src


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--external-root", required=True)
    parser.add_argument("--hgm-output-dir", required=True)
    parser.add_argument("--node-id", required=True)
    parser.add_argument("--dest-name", required=True, help="Folder name under best_agent/")
    parser.add_argument(
        "--initial-src",
        default=os.path.join(REPO_ROOT, "initial_swe/default_agent/src"),
    )
    parser.add_argument(
        "--best-agent-root",
        default=os.path.join(REPO_ROOT, "best_agent"),
    )
    args = parser.parse_args()

    dest_dir = os.path.join(args.best_agent_root, args.dest_name)
    dest_src = materialize_agent(
        external_root=os.path.abspath(args.external_root),
        hgm_output_dir=args.hgm_output_dir,
        node_id=args.node_id,
        dest_dir=dest_dir,
        initial_src=os.path.abspath(args.initial_src),
    )
    print(f"Materialized agent -> {dest_src}")


if __name__ == "__main__":
    main()
