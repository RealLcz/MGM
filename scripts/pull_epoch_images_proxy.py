#!/usr/bin/env python3
"""Deprecated: use scripts/pull_epoch_images.py (local Apptainer pull).

This script previously proxied images into a remote Docker daemon.
MendelGM now uses local Apptainer only.
"""

import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = REPO_ROOT / "scripts" / "pull_epoch_images.py"

if __name__ == "__main__":
    print(
        "pull_epoch_images_proxy.py is deprecated. "
        f"Run: python -u {SCRIPT} {' '.join(sys.argv[1:])}",
        file=sys.stderr,
    )
    import runpy

    sys.argv = [str(SCRIPT)] + sys.argv[1:]
    runpy.run_path(str(SCRIPT), run_name="__main__")
