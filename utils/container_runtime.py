"""Container runtime entry point (Apptainer)."""

from __future__ import annotations

from typing import Optional

from utils.apptainer_compat import ApptainerClient


def container_from_env(timeout: Optional[int] = None) -> ApptainerClient:
    """Return the Apptainer client used by all harness and self-improve paths."""
    return ApptainerClient(timeout=timeout)
