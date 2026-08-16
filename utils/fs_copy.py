"""Filesystem helpers for container image build contexts."""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path


def copytree_for_build(src: Path, dst: Path) -> None:
    """Copy a build-context tree, preserving exercise .git dirs for setup_repo.sh.

    Uses ``cp -a`` instead of shutil.copytree so read-only git objects copy
    reliably on shared cluster filesystems.
    """
    src = src.resolve()
    dst.parent.mkdir(parents=True, exist_ok=True)
    if dst.exists():
        shutil.rmtree(dst)
    subprocess.run(["cp", "-a", str(src), str(dst)], check=True)
