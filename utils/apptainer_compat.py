"""
docker-py compatible Apptainer client.

Keeps the docker-py call shape (images.pull / containers.create / exec_run /
put_archive / …) while backing operations with Apptainer SIF images and
writable sandboxes instead of a Docker daemon.
"""

from __future__ import annotations

import io
import json
import os
import re
import shlex
import shutil
import subprocess
import tarfile
import threading
import time
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Union

from utils import apptainer_errors as errors

APPTAINER_BIN = os.environ.get("APPTAINER_BIN", "apptainer")
GHCR_EPOCH_PREFIX = os.environ.get(
    "GHCR_EPOCH_PREFIX", "ghcr.io/epoch-research"
)
USE_HOST_NETWORK = os.environ.get("APPTAINER_USE_HOST_NETWORK", "1") not in (
    "0",
    "false",
    "False",
)

_path_locks: Dict[str, threading.Lock] = {}
_path_locks_guard = threading.Lock()


def get_container_api_timeout() -> int:
    """Timeout (seconds) for Apptainer subprocesses."""
    return max(
        60,
        int(
            os.environ.get(
                "APPTAINER_API_TIMEOUT",
                os.environ.get("DOCKER_API_TIMEOUT", "600"),
            )
        ),
    )


def get_image_dir(*, ensure: bool = False) -> Path:
    path = Path(
        os.environ.get(
            "APPTAINER_IMAGE_DIR",
            os.path.expanduser("~/.mendelgm/images"),
        )
    )
    if ensure:
        path.mkdir(parents=True, exist_ok=True)
    return path


def get_workspace_root(*, ensure: bool = False) -> Path:
    path = Path(
        os.environ.get(
            "APPTAINER_WORKSPACE_ROOT",
            "/tmp/mendelgm-workspaces",
        )
    )
    if ensure:
        path.mkdir(parents=True, exist_ok=True)
    return path


def split_image_ref(image_ref: str) -> tuple[str, str]:
    ref = image_ref.strip()
    if ref.startswith("docker://"):
        ref = ref[len("docker://") :]
    # tag is after the last ':' that follows the last '/'
    if ":" in ref:
        slash = ref.rfind("/")
        colon = ref.rfind(":")
        if colon > slash:
            return ref[:colon], ref[colon + 1 :]
    return ref, "latest"


def sanitize_tag(value: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", value)


def _sif_path_for_tag(
    image_ref: str, image_dir: Optional[Path] = None
) -> Path:
    repo, tag = split_image_ref(image_ref)
    safe = sanitize_tag(f"{repo}_{tag}")
    return (image_dir or get_image_dir()) / f"{safe}.sif"


def _meta_path_for_sif(sif: Path) -> Path:
    return sif.with_suffix(".sif.meta.json")


def _docker_uri_for_local_tag(image_ref: str) -> str:
    repo, tag = split_image_ref(image_ref)
    if (
        repo.startswith("sweb.eval.")
        or repo.startswith("sweb.base.")
        or repo.startswith("sweb.env.")
    ):
        suffix = repo[len("sweb.") :]
        remote = f"{GHCR_EPOCH_PREFIX}/swe-bench.{suffix}:{tag}"
        return f"docker://{remote}"
    return f"docker://{repo}:{tag}"


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path.resolve()) if path.exists() else str(path)
    with _path_locks_guard:
        lock = _path_locks.setdefault(key, threading.Lock())
    return lock


def _run_apptainer(
    args: List[str],
    *,
    timeout: Optional[int] = None,
    check: bool = True,
    cwd: Optional[str] = None,
    env: Optional[Dict[str, str]] = None,
) -> subprocess.CompletedProcess:
    cmd = [APPTAINER_BIN] + args
    proc = subprocess.run(
        cmd,
        capture_output=True,
        timeout=timeout,
        cwd=cwd,
        env=env,
        check=False,
    )
    if check and proc.returncode != 0:
        detail = (proc.stderr or proc.stdout or b"").decode(
            "utf-8", errors="replace"
        )
        raise errors.APIError(
            f"apptainer {' '.join(args)} failed (rc={proc.returncode}): {detail}"
        )
    return proc


def _normalize_cmd(cmd: Union[str, List[str]]) -> List[str]:
    if isinstance(cmd, str):
        return ["/bin/sh", "-c", cmd]
    return [str(c) for c in cmd]


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Image model
# ---------------------------------------------------------------------------


class ApptainerImage:
    def __init__(
        self,
        client: "ApptainerClient",
        sif: Path,
        tags: Optional[List[str]] = None,
        image_id: Optional[str] = None,
        attrs: Optional[Dict[str, Any]] = None,
        parent_id: Optional[str] = None,
    ) -> None:
        self.client = client
        self.sif = Path(sif)
        self.tags = list(tags or [])
        self.id = image_id or f"sha256:{self.sif.stem}"
        self.attrs = attrs or {
            "Created": _utc_now_iso(),
            "Id": self.id,
        }
        self._parent_id = parent_id

    def tag(self, repository: str, tag: str = "latest", force: bool = True) -> bool:
        target_ref = f"{repository}:{tag}"
        target = _sif_path_for_tag(target_ref, self.client.image_dir)
        with _lock_for_path(target):
            if target.exists() or target.is_symlink():
                if not force:
                    return False
                target.unlink()
            try:
                os.link(self.sif, target)
            except OSError:
                shutil.copy2(self.sif, target)
            tags = list({*self.tags, target_ref})
            self.client._write_meta(
                target,
                tags=tags,
                image_id=self.id,
                created=self.attrs.get("Created"),
                parent_id=self._parent_id,
            )
            self.tags = tags
        return True

    def history(self) -> List[Dict[str, Any]]:
        layers = [{"Id": self.id, "Tags": self.tags}]
        parent = self._parent_id or self.attrs.get("Parent")
        if parent:
            layers.append({"Id": parent})
        return layers

    def reload(self) -> None:
        meta = self.client._read_meta(self.sif)
        if meta:
            self.tags = list(meta.get("tags") or self.tags)
            self.id = meta.get("id") or self.id
            self.attrs = {
                "Created": meta.get("created", self.attrs.get("Created")),
                "Id": self.id,
                "Parent": meta.get("parent_id"),
            }
            self._parent_id = meta.get("parent_id")


# ---------------------------------------------------------------------------
# Exec result + low-level API shim
# ---------------------------------------------------------------------------


@dataclass
class ExecResult:
    exit_code: Optional[int]
    output: bytes


@dataclass
class _PendingExec:
    container_id: str
    cmd: Union[str, List[str]]
    workdir: Optional[str] = None
    environment: Optional[Dict[str, str]] = None
    user: Optional[str] = None
    process: Optional[subprocess.Popen] = None
    output: bytes = b""
    exit_code: Optional[int] = None
    timed_out: bool = False


class ApptainerAPI:
    """Minimal docker.APIClient-compatible surface used by MendelGM."""

    def __init__(self, client: "ApptainerClient") -> None:
        self._client = client
        self._execs: Dict[str, _PendingExec] = {}
        self._execs_lock = threading.Lock()

    def build(
        self,
        path: Optional[str] = None,
        tag: Optional[str] = None,
        dockerfile: str = "Dockerfile",
        rm: bool = True,
        forcerm: bool = True,
        decode: bool = True,
        platform: Optional[str] = None,
        nocache: bool = False,
        **kwargs: Any,
    ) -> Iterator[Dict[str, Any]]:
        build_path = Path(path or ".")
        df_name = dockerfile
        df_path = build_path / df_name if not Path(df_name).is_absolute() else Path(df_name)
        if not df_path.exists():
            yield {
                "errorDetail": {"message": f"Dockerfile not found: {df_path}"},
                "error": f"Dockerfile not found: {df_path}",
            }
            return

        image_ref = tag or build_path.name
        sif = _sif_path_for_tag(image_ref, self._client.image_dir)
        tmp = sif.with_suffix(".sif.tmp")
        parent_id = self._infer_parent_from_dockerfile(df_path)

        build_args = ["build"]
        if nocache:
            build_args.append("--force")
        # Prefer fakeroot when available; fall back without it.
        build_args.extend([str(tmp), str(df_path)])

        yield {"stream": f"Building {image_ref} via apptainer build...\n"}
        try:
            with _lock_for_path(sif):
                if tmp.exists():
                    tmp.unlink()
                proc = subprocess.Popen(
                    [APPTAINER_BIN] + build_args,
                    cwd=str(build_path),
                    stdout=subprocess.PIPE,
                    stderr=subprocess.STDOUT,
                    text=True,
                )
                assert proc.stdout is not None
                buildlog = ""
                for line in proc.stdout:
                    buildlog += line
                    yield {"stream": line}
                rc = proc.wait(timeout=self._client.timeout)
                if rc != 0:
                    yield {
                        "errorDetail": {
                            "message": f"apptainer build failed (rc={rc})"
                        },
                        "error": f"apptainer build failed (rc={rc})",
                    }
                    raise errors.BuildError(
                        f"apptainer build failed (rc={rc})", buildlog
                    )
                if sif.exists() or sif.is_symlink():
                    sif.unlink()
                tmp.rename(sif)
                self._client._write_meta(
                    sif,
                    tags=[image_ref if ":" in image_ref else f"{image_ref}:latest"],
                    parent_id=parent_id,
                )
            yield {"stream": "Image built successfully\n"}
            yield {"aux": {"ID": f"sha256:{sif.stem}"}}
        except errors.BuildError:
            raise
        except Exception as e:
            yield {"errorDetail": {"message": str(e)}, "error": str(e)}
            raise errors.BuildError(str(e), "") from e

    def _infer_parent_from_dockerfile(self, df_path: Path) -> Optional[str]:
        try:
            for line in df_path.read_text(errors="replace").splitlines():
                stripped = line.strip()
                if stripped.upper().startswith("FROM "):
                    base = stripped.split(None, 1)[1].split()[0]
                    if base.upper() == "scratch":
                        return None
                    try:
                        img = self._client.images.get(base)
                        return img.id
                    except errors.ImageNotFound:
                        return None
        except OSError:
            return None
        return None

    def exec_create(
        self,
        container: str,
        cmd: Union[str, List[str]],
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        user: Optional[str] = None,
        **kwargs: Any,
    ) -> Dict[str, str]:
        exec_id = uuid.uuid4().hex
        pending = _PendingExec(
            container_id=container,
            cmd=cmd,
            workdir=workdir,
            environment=environment,
            user=user,
        )
        with self._execs_lock:
            self._execs[exec_id] = pending
        return {"Id": exec_id}

    def exec_start(
        self, exec_id: str, stream: bool = False, **kwargs: Any
    ) -> Union[bytes, Iterator[bytes]]:
        with self._execs_lock:
            pending = self._execs.get(exec_id)
        if pending is None:
            raise errors.NotFound(f"Exec {exec_id} not found")

        container = self._client.containers.get(pending.container_id)
        run_cmd = _normalize_cmd(pending.cmd)
        if pending.user:
            run_cmd = container._wrap_user_cmd(run_cmd, user=pending.user)

        sandbox = container._writable_rootfs()
        base = container._exec_base_args(
            workdir=pending.workdir,
            environment=pending.environment,
            sandbox=sandbox,
        )
        full_cmd = [APPTAINER_BIN] + base + run_cmd

        if not stream:
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                timeout=self._client.timeout,
            )
            pending.output = (proc.stdout or b"") + (proc.stderr or b"")
            pending.exit_code = proc.returncode
            return pending.output

        def _gen() -> Iterator[bytes]:
            proc = subprocess.Popen(
                full_cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
            )
            pending.process = proc
            assert proc.stdout is not None
            chunks: List[bytes] = []
            for chunk in iter(lambda: proc.stdout.read(4096), b""):
                chunks.append(chunk)
                yield chunk
            pending.exit_code = proc.wait()
            pending.output = b"".join(chunks)

        return _gen()

    def exec_inspect(self, exec_id: str) -> Dict[str, Any]:
        with self._execs_lock:
            pending = self._execs.get(exec_id)
        if pending is None:
            raise errors.NotFound(f"Exec {exec_id} not found")
        pid = 0
        if pending.process is not None and pending.process.poll() is None:
            pid = pending.process.pid or 0
        return {
            "Id": exec_id,
            "Pid": pid,
            "ExitCode": pending.exit_code,
            "Running": pending.process is not None
            and pending.process.poll() is None,
        }

    def inspect_container(self, container_id: str) -> Dict[str, Any]:
        container = self._client.containers.get(container_id)
        return {
            "Id": container.id,
            "Name": container.name,
            "State": {
                "Running": container._started and not container._removed,
                "Pid": 0,
            },
        }


# ---------------------------------------------------------------------------
# Container
# ---------------------------------------------------------------------------


class ApptainerContainer:
    def __init__(
        self,
        client: "ApptainerClient",
        image_ref: str,
        name: Optional[str] = None,
        user: Optional[str] = None,
        network_mode: Optional[str] = None,
        command: Optional[Union[str, List[str]]] = None,
        volumes: Optional[Dict[str, Any]] = None,
        environment: Optional[Dict[str, str]] = None,
        working_dir: Optional[str] = None,
        **kwargs: Any,
    ) -> None:
        self.client = client
        self.image_ref = image_ref
        self.name = name or f"apptainer-{uuid.uuid4().hex[:12]}"
        self.id = f"apptainer-{uuid.uuid4().hex[:12]}"
        self._user = user
        self._network_mode = network_mode
        self._command = command
        self._environment = dict(environment or {})
        self._working_dir = working_dir
        self._workspace = get_workspace_root(ensure=True) / sanitize_tag(self.name)
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._binds: Dict[str, Path] = {}
        self._started = False
        self._removed = False
        self._sandbox: Optional[Path] = None
        self._lock = threading.RLock()
        self._parse_volumes(volumes)
        # Ignore docker-only kwargs (nano_cpus, platform, detach, …)
        _ = kwargs

    def _parse_volumes(self, volumes: Optional[Dict[str, Any]]) -> None:
        if not volumes:
            return
        # docker-py style: {"/host": {"bind": "/container", "mode": "rw"}}
        for host, spec in volumes.items():
            if isinstance(spec, dict):
                container_path = spec.get("bind") or spec.get("Bind")
                if container_path:
                    self._binds[str(container_path)] = Path(host)
            elif isinstance(spec, str):
                self._binds[spec] = Path(host)

    def _sif(self) -> Path:
        sif = _sif_path_for_tag(self.image_ref, self.client.image_dir)
        if not sif.exists():
            # Also accept image_ref that already points at a .sif path
            as_path = Path(self.image_ref)
            if as_path.suffix == ".sif" and as_path.exists():
                return as_path
            raise errors.ImageNotFound(
                f"Image not found: {self.image_ref} (expected {sif})"
            )
        return sif

    def start(self) -> None:
        self._started = True

    def stop(self, timeout: int = 10) -> None:
        self._started = False

    def remove(self, force: bool = False) -> None:
        with self._lock:
            self._started = False
            self._removed = True
            # Drop from client registry
            self.client.containers._forget(self)
            sandbox = self._workspace / "rootfs"
            if sandbox.exists():
                shutil.rmtree(sandbox, ignore_errors=True)
            # Keep workspace metadata lightly; remove whole workspace
            shutil.rmtree(self._workspace, ignore_errors=True)
            self._sandbox = None

    def _writable_rootfs(self) -> Path:
        """Extract image to a per-container writable sandbox (SIF rootfs is read-only)."""
        with self._lock:
            sandbox = self._workspace / "rootfs"
            if self._sandbox and self._sandbox.exists():
                return self._sandbox
            if sandbox.exists() and any(sandbox.iterdir()):
                self._sandbox = sandbox
                return sandbox

            sif = self._sif()
            if sandbox.exists():
                shutil.rmtree(sandbox, ignore_errors=True)
            proc = _run_apptainer(
                ["build", "--sandbox", str(sandbox), str(sif)],
                timeout=self.client.timeout,
                check=False,
            )
            if proc.returncode != 0 or not sandbox.exists():
                detail = (proc.stderr or proc.stdout or b"").decode(
                    "utf-8", errors="replace"
                )
                raise errors.APIError(
                    f"Failed to create writable sandbox from {sif}: {detail}"
                )
            self._sandbox = sandbox
            return sandbox

    def _bind_args(self) -> List[str]:
        args: List[str] = []
        for container_path, host_path in self._binds.items():
            host_path.mkdir(parents=True, exist_ok=True)
            args.extend(["--bind", f"{host_path}:{container_path}"])
        return args

    def _exec_base_args(
        self,
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        sandbox: Optional[Path] = None,
    ) -> List[str]:
        args = ["exec", "--writable"]
        if self._network_mode == "host" and USE_HOST_NETWORK:
            args.extend(["--net", "--network", "host"])
        args.extend(self._bind_args())
        pwd = workdir or self._working_dir
        if pwd:
            args.extend(["--pwd", pwd])
        env = dict(self._environment)
        if environment:
            env.update(environment)
        for key, val in env.items():
            args.extend(["--env", f"{key}={val}"])
        args.append(str(sandbox or self._writable_rootfs()))
        return args

    def _wrap_user_cmd(
        self, run_cmd: List[str], user: Optional[str] = None
    ) -> List[str]:
        effective = user if user is not None else self._user
        if not effective or effective in ("root", "0"):
            return run_cmd
        inner = " ".join(shlex.quote(str(c)) for c in run_cmd)
        return [
            "/bin/sh",
            "-c",
            f"exec su -s /bin/sh {shlex.quote(str(effective))} -c {shlex.quote(inner)}",
        ]

    def exec_run(
        self,
        cmd: Union[str, List[str]],
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        detach: bool = False,
        user: Optional[str] = None,
        **kwargs: Any,
    ) -> ExecResult:
        if self._removed:
            raise errors.APIError(f"Container {self.name} has been removed")
        if not self._started:
            # Match docker-py: allow exec only after start; auto-start for convenience
            self.start()

        run_cmd = _normalize_cmd(cmd)
        run_cmd = self._wrap_user_cmd(run_cmd, user=user)

        with self._lock:
            sandbox = self._writable_rootfs()
            base = self._exec_base_args(
                workdir=workdir, environment=environment, sandbox=sandbox
            )
            full_cmd = [APPTAINER_BIN] + base + run_cmd

            if detach:
                subprocess.Popen(
                    full_cmd,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    start_new_session=True,
                )
                return ExecResult(exit_code=None, output=b"")

            try:
                proc = subprocess.run(
                    full_cmd,
                    capture_output=True,
                    timeout=self.client.timeout,
                )
            except subprocess.TimeoutExpired as e:
                out = (e.stdout or b"") + (e.stderr or b"")
                raise errors.APIError(
                    f"exec_run timed out after {self.client.timeout}s"
                ) from e

            return ExecResult(
                exit_code=proc.returncode,
                output=(proc.stdout or b"") + (proc.stderr or b""),
            )

    def put_archive(self, path: str, data: bytes) -> bool:
        """
        Extract a tar archive into the container filesystem.

        Never bind-mount an entire sandbox path (e.g. /testbed, /tmp):
        that replaces the writable rootfs dir with an empty host folder.
        """
        container_path = Path(path)
        dest_dir = str(container_path)

        with self._lock:
            sandbox = self._writable_rootfs()
            use_sandbox = dest_dir not in self._binds and not any(
                dest_dir.startswith(b.rstrip("/") + "/") or dest_dir == b
                for b in self._binds
            )

            if use_sandbox:
                target_dir = sandbox / dest_dir.lstrip("/")
            else:
                host_bind = self._binds.get(dest_dir)
                if host_bind is None:
                    # Find longest matching bind prefix
                    host_bind = None
                    prefix = None
                    for cpath, hpath in self._binds.items():
                        if dest_dir == cpath or dest_dir.startswith(
                            cpath.rstrip("/") + "/"
                        ):
                            if prefix is None or len(cpath) > len(prefix):
                                prefix = cpath
                                rel = dest_dir[len(cpath) :].lstrip("/")
                                host_bind = hpath / rel if rel else hpath
                    if host_bind is None:
                        target_dir = sandbox / dest_dir.lstrip("/")
                    else:
                        target_dir = host_bind
                else:
                    target_dir = host_bind

            target_dir.mkdir(parents=True, exist_ok=True)

            stream = io.BytesIO(data if isinstance(data, bytes) else b"".join(data))
            with tarfile.open(fileobj=stream, mode="r:*") as tar:
                tar.extractall(path=str(target_dir))
            return True

    def get_archive(self, path: str):
        """Return (bits_iterable, stat_dict) like docker-py get_archive."""
        container_path = Path(path)
        with self._lock:
            sandbox = self._writable_rootfs()
            src = sandbox / str(container_path).lstrip("/")
            # Prefer bind mount if path is under a bind
            for cpath, hpath in self._binds.items():
                if str(container_path) == cpath or str(container_path).startswith(
                    cpath.rstrip("/") + "/"
                ):
                    rel = str(container_path)[len(cpath) :].lstrip("/")
                    candidate = hpath / rel if rel else hpath
                    if candidate.exists():
                        src = candidate
                        break

            if not src.exists():
                raise errors.NotFound(f"Path not found in container: {path}")

            tar_stream = io.BytesIO()
            with tarfile.open(fileobj=tar_stream, mode="w") as tar:
                tar.add(str(src), arcname=src.name)
            tar_stream.seek(0)
            data = tar_stream.read()
            stat = {
                "name": src.name,
                "size": len(data),
                "mode": src.stat().st_mode if src.exists() else 0,
                "mtime": src.stat().st_mtime if src.exists() else time.time(),
            }

            def _chunks() -> Iterator[bytes]:
                yield data

            return _chunks(), stat


# ---------------------------------------------------------------------------
# Collections: images / containers
# ---------------------------------------------------------------------------


class ImageCollection:
    def __init__(self, client: "ApptainerClient") -> None:
        self._client = client

    def _image_from_sif(self, sif: Path) -> ApptainerImage:
        meta = self._client._read_meta(sif)
        tags = list(meta.get("tags") or [])
        if not tags:
            # Recover a tag guess from filename stem
            tags = [sif.stem.replace("_latest", ":latest")]
        return ApptainerImage(
            self._client,
            sif,
            tags=tags,
            image_id=meta.get("id"),
            attrs={
                "Created": meta.get("created", _utc_now_iso()),
                "Id": meta.get("id", f"sha256:{sif.stem}"),
                "Parent": meta.get("parent_id"),
            },
            parent_id=meta.get("parent_id"),
        )

    def get(self, name: str) -> ApptainerImage:
        # Exact SIF path
        as_path = Path(name)
        if as_path.suffix == ".sif" and as_path.exists():
            return self._image_from_sif(as_path)

        sif = _sif_path_for_tag(name, self._client.image_dir)
        if sif.exists():
            return self._image_from_sif(sif)

        # Search by tag in metadata / filename
        repo, tag = split_image_ref(name)
        wanted = f"{repo}:{tag}"
        for img in self.list(all=True):
            if wanted in img.tags or name in img.tags or img.id == name:
                return img
            if any(t.split(":")[0] == name for t in img.tags):
                return img
        raise errors.ImageNotFound(f"Image not found: {name}")

    def list(self, all: bool = False, **kwargs: Any) -> List[ApptainerImage]:
        images: List[ApptainerImage] = []
        seen: set[str] = set()
        for sif in sorted(self._client.image_dir.glob("*.sif")):
            if sif.suffixes[-2:] == [".sif", ".tmp"] or str(sif).endswith(".sif.tmp"):
                continue
            if not sif.is_file() and not sif.is_symlink():
                continue
            key = str(sif.resolve()) if sif.exists() else str(sif)
            if key in seen:
                continue
            seen.add(key)
            images.append(self._image_from_sif(sif))
        return images

    def pull(
        self,
        repository: str,
        tag: Optional[str] = None,
        platform: Optional[str] = None,
        **kwargs: Any,
    ) -> ApptainerImage:
        if tag:
            image_ref = f"{repository}:{tag}"
        else:
            image_ref = repository
        sif = self._client._pull_to_sif(image_ref, platform=platform)
        return self._image_from_sif(sif)

    def build(
        self,
        path: Optional[str] = None,
        tag: Optional[str] = None,
        rm: bool = True,
        nocache: bool = False,
        dockerfile: str = "Dockerfile",
        platform: Optional[str] = None,
        **kwargs: Any,
    ):
        logs: List[Dict[str, Any]] = []
        image_id = None
        for chunk in self._client.api.build(
            path=path,
            tag=tag,
            dockerfile=dockerfile,
            rm=rm,
            nocache=nocache,
            platform=platform,
            decode=True,
            **kwargs,
        ):
            logs.append(chunk)
            if "aux" in chunk and isinstance(chunk["aux"], dict):
                image_id = chunk["aux"].get("ID")
            if "error" in chunk:
                raise errors.BuildError(
                    chunk.get("error") or "build failed",
                    "".join(
                        c.get("stream", "") for c in logs if isinstance(c, dict)
                    ),
                )
        image_ref = tag or (Path(path or ".").name)
        if ":" not in image_ref:
            image_ref = f"{image_ref}:latest"
        img = self.get(image_ref)
        if image_id:
            img.id = image_id
        return img, iter(logs)

    def remove(self, image: str, force: bool = False, **kwargs: Any) -> None:
        try:
            img = self.get(image)
        except errors.ImageNotFound:
            if force:
                return
            raise
        sif = img.sif
        with _lock_for_path(sif):
            meta = _meta_path_for_sif(sif)
            if sif.exists() or sif.is_symlink():
                sif.unlink()
            if meta.exists():
                meta.unlink()


class ContainerCollection:
    def __init__(self, client: "ApptainerClient") -> None:
        self._client = client
        self._by_name: Dict[str, ApptainerContainer] = {}
        self._by_id: Dict[str, ApptainerContainer] = {}
        self._lock = threading.Lock()

    def _register(self, container: ApptainerContainer) -> ApptainerContainer:
        with self._lock:
            self._by_name[container.name] = container
            self._by_id[container.id] = container
        return container

    def _forget(self, container: ApptainerContainer) -> None:
        with self._lock:
            self._by_name.pop(container.name, None)
            self._by_id.pop(container.id, None)

    def get(self, name_or_id: str) -> ApptainerContainer:
        with self._lock:
            if name_or_id in self._by_id:
                return self._by_id[name_or_id]
            if name_or_id in self._by_name:
                return self._by_name[name_or_id]
        raise errors.NotFound(f"Container '{name_or_id}' not found")

    def create(
        self,
        image: str,
        name: Optional[str] = None,
        user: Optional[str] = None,
        network_mode: Optional[str] = None,
        command: Optional[Union[str, List[str]]] = None,
        detach: bool = True,
        volumes: Optional[Dict[str, Any]] = None,
        environment: Optional[Dict[str, str]] = None,
        working_dir: Optional[str] = None,
        **kwargs: Any,
    ) -> ApptainerContainer:
        # Ensure image exists locally
        try:
            self._client.images.get(image)
        except errors.ImageNotFound:
            # Leave create failing clearly; callers may pull first
            raise errors.ImageNotFound(f"Image not found for create: {image}")

        if name:
            try:
                existing = self.get(name)
                raise errors.APIError(
                    f"Conflict: container name {name} already in use "
                    f"({existing.id})"
                )
            except errors.NotFound:
                pass

        container = ApptainerContainer(
            self._client,
            image_ref=image,
            name=name,
            user=user,
            network_mode=network_mode,
            command=command,
            volumes=volumes,
            environment=environment,
            working_dir=working_dir,
            detach=detach,
            **kwargs,
        )
        return self._register(container)

    def run(
        self,
        image: str,
        command: Optional[Union[str, List[str]]] = None,
        name: Optional[str] = None,
        detach: bool = False,
        network_mode: Optional[str] = None,
        user: Optional[str] = None,
        volumes: Optional[Dict[str, Any]] = None,
        environment: Optional[Dict[str, str]] = None,
        working_dir: Optional[str] = None,
        **kwargs: Any,
    ):
        container = self.create(
            image=image,
            name=name,
            user=user,
            network_mode=network_mode,
            command=command,
            detach=True,
            volumes=volumes,
            environment=environment,
            working_dir=working_dir,
            **kwargs,
        )
        container.start()
        if detach:
            return container
        if command is not None:
            result = container.exec_run(
                command,
                workdir=working_dir,
                environment=environment,
                user=user,
            )
            return result
        return container


# ---------------------------------------------------------------------------
# Client
# ---------------------------------------------------------------------------


class ApptainerClient:
    def __init__(self, timeout: Optional[int] = None) -> None:
        self.timeout = (
            int(timeout) if timeout is not None else get_container_api_timeout()
        )
        self.image_dir = get_image_dir(ensure=True)
        self.images = ImageCollection(self)
        self.containers = ContainerCollection(self)
        self.api = ApptainerAPI(self)

    def ping(self) -> bool:
        try:
            proc = _run_apptainer(
                ["version"], timeout=min(30, self.timeout), check=False
            )
            if proc.returncode != 0:
                raise errors.APIError(
                    (proc.stderr or proc.stdout or b"").decode(
                        "utf-8", errors="replace"
                    )
                )
            return True
        except FileNotFoundError as e:
            raise errors.APIError(
                f"Apptainer binary not found: {APPTAINER_BIN}"
            ) from e

    def info(self) -> Dict[str, Any]:
        return {
            "Name": "apptainer",
            "ServerVersion": "apptainer-compat",
            "Driver": "apptainer",
            "Images": len(self.images.list(all=True)),
            "Containers": len(self.containers._by_id),
            "ApptainerImageDir": str(self.image_dir),
            "ApptainerWorkspaceRoot": str(get_workspace_root()),
        }

    def _write_meta(
        self,
        sif: Path,
        tags: Optional[List[str]] = None,
        image_id: Optional[str] = None,
        created: Optional[str] = None,
        parent_id: Optional[str] = None,
    ) -> None:
        meta = {
            "tags": tags or [],
            "id": image_id or f"sha256:{sif.stem}",
            "created": created or _utc_now_iso(),
            "parent_id": parent_id,
            "sif": str(sif),
        }
        _meta_path_for_sif(sif).write_text(json.dumps(meta, indent=2))

    def _read_meta(self, sif: Path) -> Dict[str, Any]:
        meta_path = _meta_path_for_sif(sif)
        if meta_path.exists():
            try:
                return json.loads(meta_path.read_text())
            except json.JSONDecodeError:
                return {}
        return {}

    def _pull_to_sif(
        self, image_ref: str, platform: Optional[str] = None
    ) -> Path:
        image_dir = self.image_dir
        sif = _sif_path_for_tag(image_ref, image_dir)
        with _lock_for_path(sif):
            if sif.exists() and sif.stat().st_size > 0:
                return sif
            tmp = sif.with_suffix(".sif.tmp")
            if tmp.exists():
                tmp.unlink()
            uri = _docker_uri_for_local_tag(image_ref)
            pull_args = ["pull", str(tmp), uri]
            # platform is advisory; apptainer pull may ignore it
            _ = platform
            proc = _run_apptainer(
                pull_args, timeout=self.timeout, check=False
            )
            if proc.returncode != 0 or not tmp.exists():
                detail = (proc.stderr or proc.stdout or b"").decode(
                    "utf-8", errors="replace"
                )
                raise errors.APIError(
                    f"Failed to pull {image_ref} from {uri}: {detail}"
                )
            tmp.rename(sif)
            repo, tag = split_image_ref(image_ref)
            tags = [f"{repo}:{tag}"]
            self._write_meta(sif, tags=tags)
            return sif
