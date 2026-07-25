"""docker-py compatible exception types for the Apptainer runtime."""


class APIError(Exception):
    """Raised when an Apptainer / container API operation fails."""


class NotFound(Exception):
    """Raised when a container or other named resource is not found."""


class ImageNotFound(NotFound):
    """Raised when a requested image/SIF is not found."""


class BuildError(Exception):
    def __init__(self, message: str, build_log: str = "") -> None:
        super().__init__(message)
        self.build_log = build_log
