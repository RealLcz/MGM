"""Local Apptainer container client (sole supported runtime)."""

from __future__ import annotations

from utils.apptainer_compat import ApptainerClient


def container_from_env(timeout: int | None = None) -> ApptainerClient:
    """Return the Apptainer client used by all harness and self-improve paths."""
    return ApptainerClient(timeout=timeout)
