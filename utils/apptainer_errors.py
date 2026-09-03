"""Exception types for the Apptainer container client (docker-py compatible names)."""

from __future__ import annotations


class APIError(Exception):
    pass


class NotFound(Exception):
    pass


class ImageNotFound(NotFound):
    pass


class BuildError(Exception):
    def __init__(self, message: str, build_log: str = "") -> None:
        super().__init__(message)
        self.build_log = build_log
