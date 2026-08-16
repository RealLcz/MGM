# This file is adapted from https://github.com/jennyzzt/dgm.

import io
import json
import logging
import os
import shutil
import subprocess
import tarfile
import threading
from pathlib import Path
from typing import List, Optional, Set, Union

import pathspec

from utils import apptainer_errors as container_errors


def get_container_api_timeout() -> int:
    """Subprocess timeout (seconds) for Apptainer pull/build/exec operations."""
    return max(
        60,
        int(os.environ.get("APPTAINER_API_TIMEOUT", os.environ.get("CONTAINER_API_TIMEOUT", "600"))),
    )


def get_docker_api_timeout() -> int:
    """Backward-compatible alias for get_container_api_timeout()."""
    return get_container_api_timeout()


def docker_from_env(timeout: Optional[int] = None):
    """Return the local Apptainer client (docker-py compatible API)."""
    from utils.container_runtime import container_from_env

    if timeout is None:
        timeout = get_container_api_timeout()
    return container_from_env(timeout=timeout)


def read_dockerignore(dockerignore_path: str) -> List[str]:
    """Read and parse .dockerignore file, removing comments and empty lines."""
    patterns = []

    if not os.path.exists(dockerignore_path):
        print(f"Warning: {dockerignore_path} not found")
        return patterns

    with open(dockerignore_path, "r") as f:
        for line in f:
            line = line.strip()
            # Skip empty lines and comments
            if line and not line.startswith("#") and not line.startswith("<!--"):
                patterns.append(line)

    return patterns


def get_files_respecting_dockerignore(
    root_dir: str, spec: pathspec.PathSpec
) -> Set[str]:
    """Get files in the directory tree, skipping directories that match dockerignore patterns."""
    files = set()
    root_path = Path(root_dir)

    for root, dirs, filenames in os.walk(root_dir):
        # Convert to relative path
        rel_root = Path(root).relative_to(root_path)

        # Filter out directories that should be ignored
        # We need to modify dirs in-place to prevent os.walk from entering them
        dirs_to_remove = []
        for dirname in dirs:
            if rel_root == Path("."):
                rel_dir_path = dirname + "/"
            else:
                rel_dir_path = str(rel_root / dirname) + "/"

            if spec.match_file(rel_dir_path):
                dirs_to_remove.append(dirname)

        # Remove ignored directories from dirs list to skip them
        for dirname in dirs_to_remove:
            dirs.remove(dirname)

        # Add files that are not ignored
        for filename in filenames:
            if rel_root == Path("."):
                rel_path = filename
            else:
                rel_path = str(rel_root / filename)

            if not spec.match_file(rel_path):
                files.add(rel_path)

    return files


def copy_src_files(dest_dir: str, source_dir: str = ".", build_image: bool = False):
    """Copy files from source_dir to dest_dir, excluding files matching .dockerignore patterns."""
    source_dir = os.path.abspath(source_dir)
    dest_dir = os.path.abspath(dest_dir)

    # Read ignore patterns
    dockerignore_path = os.path.join(source_dir, ".dockerignore")
    ignore_patterns = read_dockerignore(dockerignore_path)
    print(f"Found {len(ignore_patterns)} ignore patterns")

    # Create pathspec object for pattern matching
    spec = pathspec.PathSpec.from_lines("gitwildmatch", ignore_patterns)

    # Get files that are not ignored (skipping ignored directories entirely)
    files_to_copy = get_files_respecting_dockerignore(source_dir, spec)
    print(
        f"Found {len(files_to_copy)} files to copy (ignored directories were skipped)"
    )

    # Create destination directory
    os.makedirs(dest_dir, exist_ok=True)

    # Copy files
    copied_count = 0
    for rel_path in files_to_copy:
        source_path = os.path.join(source_dir, rel_path)
        dest_path = os.path.join(dest_dir, rel_path)

        # Create destination directory if needed
        dest_parent = os.path.dirname(dest_path)
        if dest_parent:
            os.makedirs(dest_parent, exist_ok=True)

        # Copy file
        try:
            if os.path.exists(source_path) and os.path.isfile(source_path):
                shutil.copy2(source_path, dest_path)
                copied_count += 1
                print(f"Copied: {rel_path}")
        except Exception as e:
            print(f"Error copying {rel_path}: {e}")

    print(f"\nCopied {copied_count} files to {dest_dir}")

    metadata_path = os.path.join(dest_dir, "..", "metadata.json")
    if not os.path.exists(metadata_path):
        with open(metadata_path, "w") as f:
            json.dump(
                {
                    "run_id": "initial",
                    "overall_performance": {
                        "accuracy_score": 0,
                        "total_resolved_instances": 0,
                        "total_submitted_instances": 0,
                        "files": [],
                        "total_unresolved_ids": [],
                        "total_emptypatch_ids": [],
                        "total_resolved_ids": [],
                        "total_submitted_ids": [],
                    },
                },
                f,
            )
    if build_image:
        shutil.copy2(os.path.join(source_dir, dockerignore_path), dest_dir)
        shutil.copy2(os.path.join(source_dir, "Dockerfile"), dest_dir)

        image_name = os.path.basename(os.path.abspath(dest_dir + "/.."))
        print(f"Building Apptainer image '{image_name}'...")
        client = docker_from_env()
        client.images.build(path=dest_dir, tag=image_name, nocache=True)


# Thread-local storage for loggers
_thread_local = threading.local()


def get_thread_logger():
    """Get the logger instance specific to the current thread."""
    return getattr(_thread_local, "logger", None)


def setup_logger(log_file):
    """
    Set up a thread-safe logger with file handler.

    Args:
        log_file (str): Path to the log file

    Returns:
        logging.Logger: Thread-specific logger instance
    """
    # Create logger with thread-specific name
    thread_id = threading.get_ident()
    logger_name = f"selfimprove_logger_{thread_id}"
    logger = logging.getLogger(logger_name)
    logger.setLevel(logging.INFO)

    # Clear existing handlers
    for handler in logger.handlers:
        logger.removeHandler(handler)

    # Create file handler with lock
    handler = logging.FileHandler(log_file)
    handler.setLevel(logging.INFO)
    handler.stream.lock = threading.Lock()

    # Create formatter
    formatter = logging.Formatter(
        "%(asctime)s - %(threadName)s - %(levelname)s - %(message)s"
    )
    handler.setFormatter(formatter)

    # Add handler to logger
    logger.addHandler(handler)

    # Store logger in thread local storage
    _thread_local.logger = logger

    return logger


def safe_log(message: str, level: int = logging.INFO):
    """Thread-safe logging function."""
    logger = get_thread_logger()
    if logger:
        logger.log(level, message)
    else:
        print(f"Warning: No logger found for thread {threading.get_ident()}")


def remove_existing_container(client, container_name):
    """
    Check if a container with the specified name exists, and remove it if so.
    """
    try:
        existing_container = client.containers.get(container_name)
        safe_log(f"Removing existing container with name {container_name}")
        existing_container.stop()
        existing_container.remove()
    except container_errors.NotFound:
        # Container does not exist, no action needed
        safe_log(f"No existing container with name {container_name} found.")
    except container_errors.APIError as e:
        safe_log(
            f"Error removing existing container {container_name}: {e}", logging.ERROR
        )
        raise


def create_archive(path: Union[str, Path], data: Optional[bytes] = None) -> bytes:
    """
    Create a tar archive containing either file data or a directory structure.

    Args:
        path (Union[str, Path]): Path where the file/directory should be placed in the container
        data (Optional[bytes]): File content in bytes if creating archive for a single file

    Returns:
        bytes: Tar archive containing the file or directory structure
    """
    tar_stream = io.BytesIO()
    with tarfile.open(fileobj=tar_stream, mode="w") as tar:
        if data is not None:
            # Handle single file
            tarinfo = tarfile.TarInfo(name=str(path))
            tarinfo.size = len(data)
            tar.addfile(tarinfo, io.BytesIO(data))
        else:
            # Handle directory
            path = Path(path)
            arcname = path.name
            tar.add(path, arcname=arcname)

    tar_stream.seek(0)
    return tar_stream.read()


def _ensure_hgm_container_packages(container) -> None:
    """Verify git is available (installed at image build time via apptainer/hgm.def)."""
    check = container.exec_run("command -v git", workdir="/")
    if check.exit_code != 0:
        raise RuntimeError(
            "git not found in HGM container image. "
            "Rebuild with apptainer/hgm.def (see build_hgm_container)."
        )


def _build_hgm_image_sif(client, image_name: str) -> None:
    """Build HGM base SIF from apptainer/hgm.def (includes git, build-essential)."""
    from utils.apptainer_compat import _sif_path_for_tag

    repo_root = Path(__file__).resolve().parents[1]
    def_file = repo_root / "apptainer" / "hgm.def"
    if not def_file.exists():
        raise FileNotFoundError(f"HGM def file not found: {def_file}")

    sif = _sif_path_for_tag(image_name)
    if sif.exists():
        client.images._write_meta(sif, [image_name])
        return

    safe_log(f"Building HGM Apptainer image from {def_file} (may take several minutes)...")
    cmd = [os.environ.get("APPTAINER_BIN", "apptainer"), "build", "--force"]
    if os.environ.get("APPTAINER_BUILD_FAKEROOT", "1") == "1":
        cmd.append("--fakeroot")
    cmd.extend([str(sif), str(def_file)])
    proc = subprocess.run(
        cmd,
        capture_output=True,
        text=True,
        timeout=client.timeout,
    )
    if proc.returncode != 0:
        err = (proc.stderr or proc.stdout or "").strip()
        raise RuntimeError(f"apptainer build hgm image failed: {err[:2000]}")
    client.images._write_meta(sif, [image_name])
    safe_log(f"HGM Apptainer image ready: {sif}")


def build_hgm_container(
    client,
    repo_path="./",
    image_name="app",
    container_name="app-container",
    force_rebuild=False,
):
    """
    Build the Apptainer image for the hgm app and start a workspace with /hgm bind-mounted.
    """
    from utils.apptainer_compat import ApptainerClient, _sif_path_for_tag

    repo_path = os.path.abspath(repo_path)

    try:
        if force_rebuild:
            try:
                client.images.remove(image_name, force=True)
            except Exception:
                pass
        try:
            client.images.get(image_name)
        except container_errors.ImageNotFound:
            _build_hgm_image_sif(client, image_name)
    except Exception as e:
        safe_log(f"Error while building the container image: {e}")
        return None

    try:
        container = client.containers.create(
            image=image_name,
            name=container_name,
            detach=True,
            network_mode="host",
            command="tail -f /dev/null",
        )
        from pathlib import Path as _Path

        container._binds["/hgm"] = _Path(repo_path)
        container.start()
        _ensure_hgm_container_packages(container)
        safe_log(f"Container '{container_name}' started successfully.")
        return container
    except Exception as e:
        safe_log(f"Error while starting the container: {e}")
        return None


def cleanup_container(container):
    """
    Stops and removes the specified Docker container.
    """
    safe_log(f"Stopping container '{container.name}'...")
    container.stop()
    container.remove()
    safe_log(f"Container '{container.name}' removed.")


def copy_to_container(
    container, source_path: Union[str, Path], dest_path: Union[str, Path]
) -> None:
    """
    Copy a file or directory from the local system to a Docker container.

    Args:
        container: Docker container object
        source_path (Union[str, Path]): Path to the source file/directory on local system
        dest_path (Union[str, Path]): Destination path in the container

    Raises:
        FileNotFoundError: If source path doesn't exist
        Exception: For other errors during copy operation
    """
    source_path = Path(source_path)
    dest_path = Path(dest_path)

    try:
        if not source_path.exists():
            raise FileNotFoundError(f"Source path not found: {source_path}")

        # Determine container destination directory
        if source_path.is_file():
            container_dest_dir = str(dest_path.parent)
            archive_path = dest_path.name
            with open(source_path, "rb") as source_file:
                data = source_file.read()
            archive = create_archive(archive_path, data)
        else:
            # For directories, we want to copy to the parent of dest_path
            container_dest_dir = str(dest_path.parent)
            archive = create_archive(source_path)

        # Create destination directory in container if it doesn't exist
        container.exec_run(f"mkdir -p {container_dest_dir}")

        safe_log(f"Copying {source_path} to container at {dest_path}")
        success = container.put_archive(container_dest_dir, archive)

        if not success:
            raise Exception(f"Failed to copy {source_path} to container")

        safe_log(f"Successfully copied {source_path} to container")

    except Exception as e:
        safe_log(f"Error copying to container: {e}", logging.ERROR)
        raise


def copy_from_container(
    container, source_path: Union[str, Path], dest_path: Union[str, Path]
) -> None:
    """
    Copy a file or directory from a Docker container to the local system.

    Args:
        container: Docker container object
        source_path (Union[str, Path]): Path to the source file/directory in container
        dest_path (Union[str, Path]): Destination path on local system

    Raises:
        FileNotFoundError: If source path doesn't exist in container
        Exception: For other errors during copy operation
    """
    source_path = Path(source_path)
    dest_path = Path(dest_path)

    try:
        # Check if source exists in container
        result = container.exec_run(f"test -e {source_path}")
        if result.exit_code and result.exit_code != 0:
            raise FileNotFoundError(
                f"Source path not found in container: {source_path}"
            )

        # Get file type from container
        result = container.exec_run(f"stat -f '%HT' {source_path}")
        is_file = result.output.decode().strip() == "Regular File"

        # Create destination directory if it doesn't exist
        dest_path.parent.mkdir(parents=True, exist_ok=True)

        safe_log(f"Copying from container {source_path} to local path {dest_path}")

        # Get archive from container
        bits, stat = container.get_archive(str(source_path))

        # Concatenate all chunks into a single bytes object
        archive_data = b"".join(bits)

        # Extract to temporary stream
        stream = io.BytesIO(archive_data)

        with tarfile.open(fileobj=stream, mode="r") as tar:
            # If extracting a single file
            if is_file:
                member = tar.getmembers()[0]
                with tar.extractfile(member) as source_file:
                    data = source_file.read()
                    # Write directly to destination file
                    with open(dest_path, "wb") as dest_file:
                        dest_file.write(data)
            else:
                # For directories, extract to parent directory
                tar.extractall(path=str(dest_path.parent))
                # Rename if necessary
                extracted_path = dest_path.parent / Path(stat["name"]).name
                if extracted_path != dest_path and extracted_path.exists():
                    extracted_path.rename(dest_path)

        safe_log(f"Successfully copied from container to {dest_path}")

    except Exception as e:
        safe_log(f"Error copying from container: {e}", logging.ERROR)
        raise


def log_container_output(exec_result, raise_error=True):
    """
    Log output from a Docker container execution, handling both streaming and non-streaming cases.
    """
    # Handle output logging
    if isinstance(exec_result.output, bytes):
        # Handle non-streaming output
        safe_log(f"Container output: {exec_result.output.decode()}")
    else:
        # Handle streaming output
        for chunk in exec_result.output:
            if chunk:
                safe_log(f"Container output: {chunk.decode().strip()}")
    safe_log("Wrote to the log file")

    # Check exit code
    if raise_error and exec_result.exit_code and exec_result.exit_code != 0:
        error_msg = f"Script failed with exit code {exec_result.exit_code}"
        safe_log(error_msg, logging.ERROR)
        raise Exception(error_msg)
