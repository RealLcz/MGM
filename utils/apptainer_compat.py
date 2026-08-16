"""Docker-py compatible client implemented with Apptainer.

Maps docker-py container workflows to apptainer exec/run with bind mounts.
Persistent containers are simulated via workspace directories and optional
apptainer instances.
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
import tempfile
import threading
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, Tuple, Union

from utils import apptainer_errors as container_errors
from utils.fs_copy import copytree_for_build


APPTAINER_BIN = os.environ.get("APPTAINER_BIN", "apptainer")
WORKSPACE_ROOT = Path(
    os.environ.get(
        "APPTAINER_WORKSPACE_ROOT",
        str(Path(os.environ.get("TMPDIR", "/tmp")) / "apptainer-workspaces"),
    )
)
GHCR_EPOCH_PREFIX = os.environ.get(
    "SWE_GHCR_EPOCH_PREFIX", "ghcr.io/epoch-research"
)
USE_FAKEROOT_BUILD = os.environ.get("APPTAINER_BUILD_FAKEROOT", "1") == "1"
USE_NV = os.environ.get("APPTAINER_NV", "0") == "1"
USE_HOST_NETWORK = os.environ.get("APPTAINER_USE_HOST_NETWORK", "0") == "1"


def get_image_dir() -> Path:
    """Resolve Apptainer SIF storage (matches swe_scripts/apptainer_runtime.inc.sh)."""
    if os.environ.get("APPTAINER_IMAGE_DIR"):
        return Path(os.environ["APPTAINER_IMAGE_DIR"])
    hf_home = Path(os.environ.get("HF_HOME", Path.home()))
    candidates = [
        hf_home / "apptainer_images",
        hf_home / ".cache" / "huggingface" / "apptainer_images",
        Path.home() / ".cache" / "huggingface" / "apptainer_images",
        Path.home() / ".apptainer" / "images",
    ]
    for candidate in candidates:
        if candidate.is_dir() and any(candidate.glob("*.sif")):
            return candidate
    return hf_home / "apptainer_images"


def _candidate_image_dirs() -> List[Path]:
    dirs: List[Path] = []
    seen: set[str] = set()
    for candidate in [
        get_image_dir(),
        Path(os.environ.get("APPTAINER_IMAGE_DIR", "")),
        Path.home() / ".cache" / "huggingface" / "apptainer_images",
        Path.home() / ".apptainer" / "images",
    ]:
        if not str(candidate) or str(candidate) in seen:
            continue
        seen.add(str(candidate))
        if candidate.is_dir():
            dirs.append(candidate)
    return dirs

_PULL_LOCKS: Dict[str, threading.Lock] = {}
_PULL_LOCKS_GUARD = threading.Lock()


def _lock_for_path(path: Path) -> threading.Lock:
    key = str(path)
    with _PULL_LOCKS_GUARD:
        if key not in _PULL_LOCKS:
            _PULL_LOCKS[key] = threading.Lock()
        return _PULL_LOCKS[key]


def _run_apptainer(
    args: List[str],
    timeout: Optional[int] = None,
    check: bool = True,
) -> subprocess.CompletedProcess:
    cmd = [APPTAINER_BIN] + args
    return subprocess.run(
        cmd,
        capture_output=True,
        text=False,
        timeout=timeout,
        check=check,
    )


def _sanitize_tag(image_ref: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]+", "_", image_ref)


def _split_image_ref(image_ref: str) -> Tuple[str, str]:
    if ":" in image_ref:
        repo, tag = image_ref.rsplit(":", 1)
        return repo, tag
    return image_ref, "latest"


def _sif_path_for_tag(image_ref: str, image_dir: Optional[Path] = None) -> Path:
    repo, tag = _split_image_ref(image_ref)
    safe = _sanitize_tag(f"{repo}_{tag}")
    return (image_dir or get_image_dir()) / f"{safe}.sif"


def _qualify_docker_image(image_ref: str) -> str:
    image_ref = image_ref.strip()
    if image_ref.startswith("docker://"):
        return image_ref.split("://", 1)[1]
    if "/" not in image_ref:
        return f"docker.io/library/{image_ref}"
    registry = image_ref.split("/", 1)[0]
    if "." not in registry and ":" not in registry:
        return f"docker.io/{image_ref}"
    return image_ref


def _parse_dockerfile_instructions(
    dockerfile_text: str,
) -> Tuple[Optional[str], List[tuple[str, str]], List[str], List[str]]:
    """Parse Dockerfile into FROM ref, COPY steps, ENV lines, and RUN shell commands."""
    from_ref: Optional[str] = None
    copy_steps: List[tuple[str, str]] = []
    env_lines: List[str] = []
    run_lines: List[str] = []

    physical_lines = dockerfile_text.splitlines()
    idx = 0
    while idx < len(physical_lines):
        raw = physical_lines[idx]
        idx += 1
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue

        parts = stripped.split(None, 1)
        instr = parts[0].upper()
        payload = parts[1] if len(parts) > 1 else ""
        while payload.endswith("\\") and idx < len(physical_lines):
            next_line = physical_lines[idx].strip()
            idx += 1
            if next_line.startswith("#"):
                continue
            payload = payload[:-1].rstrip() + " " + next_line

        if instr == "FROM" and from_ref is None:
            from_ref = payload.strip()
            if "--platform=" in from_ref:
                from_ref = from_ref.split(None, 1)[1]
            continue
        if instr == "COPY":
            copy_parts = shlex.split(payload)
            if len(copy_parts) >= 2:
                copy_steps.append((copy_parts[0], copy_parts[1]))
            continue
        if instr == "ENV":
            env_lines.append(payload.strip())
            continue
        if instr == "ADD":
            add_parts = payload.split(None, 1)
            if len(add_parts) == 2 and add_parts[0].startswith("http"):
                run_lines.append(f'curl -fsSL "{add_parts[0]}" -o {add_parts[1]}')
            continue
        if instr == "RUN":
            run_lines.append(payload.strip())
            continue
        if instr == "WORKDIR":
            workdir = payload.strip()
            run_lines.append(f"mkdir -p {workdir} && cd {workdir}")

    return from_ref, copy_steps, env_lines, run_lines


def _resolve_copy_dest(sandbox: Path, build_context: Path, src: str, dest: str) -> Path:
    src_path = build_context / src
    dest = dest.strip()
    if dest.endswith("/") or dest.endswith("/."):
        return sandbox / dest.lstrip("/").rstrip("/") / src_path.name
    dest_path = sandbox / dest.lstrip("/")
    if dest_path.exists() and dest_path.is_dir():
        return dest_path / src_path.name
    return dest_path


def _dockerfile_post_script(dockerfile_text: str) -> str:
    """Convert a simple Dockerfile (FROM/RUN/ENV/ADD http) into a bash post script."""
    _, _, env_lines, run_lines = _parse_dockerfile_instructions(dockerfile_text)
    lines = ["#!/bin/bash", "set -euxo pipefail"]
    for env in env_lines:
        if "=" in env:
            key, val = env.split("=", 1)
            key = key.strip()
            val = val.strip().strip('"').strip("'")
            lines.append(f'export {key}="{val}"')
    lines.extend(run_lines)
    lines.append("")
    return "\n".join(lines)


def _dockerfile_env_script(env_lines: List[str]) -> str:
    """Persist Dockerfile ENV lines into the Apptainer runtime environment."""
    lines = ["#!/bin/sh"]
    for env in env_lines:
        if "=" not in env:
            continue
        key, val = env.split("=", 1)
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        escaped = val.replace("\\", "\\\\").replace('"', '\\"').replace("`", "\\`")
        lines.append(f'export {key}="{escaped}"')
    lines.append("")
    return "\n".join(lines)


def _build_sif_from_dockerfile(
    path: Path,
    tag: str,
    sif: Path,
    timeout: Optional[int] = None,
) -> List[dict]:
    dockerfile_text = (path / "Dockerfile").read_text(encoding="utf-8")
    from_ref, copy_steps, env_lines, run_lines = _parse_dockerfile_instructions(
        dockerfile_text
    )

    if not from_ref:
        raise container_errors.BuildError(
            f"No FROM line in Dockerfile under {path}", "missing FROM"
        )

    logs: List[dict] = []
    sandbox = Path(
        tempfile.mkdtemp(
            prefix="apptainer-dockerfile-",
            dir=os.environ.get("TMPDIR", "/tmp"),
        )
    )
    try:
        local_parent = _sif_path_for_tag(from_ref)
        if local_parent.exists():
            logs.append({"stream": f"Building sandbox from local image {from_ref}\n"})
            proc = _run_apptainer(
                ["build", "--force", "--sandbox", str(sandbox), str(local_parent)],
                timeout=timeout,
                check=False,
            )
        else:
            base_uri = f"docker://{_qualify_docker_image(from_ref)}"
            logs.append({"stream": f"Building sandbox from {base_uri}\n"})
            proc = _run_apptainer(
                ["build", "--force", "--sandbox", str(sandbox), base_uri],
                timeout=timeout,
                check=False,
            )
        logs.append(
            {
                "stream": (proc.stdout or proc.stderr or b"").decode(
                    "utf-8", errors="replace"
                )
            }
        )
        if proc.returncode != 0:
            raise container_errors.BuildError(
                f"apptainer sandbox build failed for {tag}",
                logs[-1]["stream"],
            )

        for src, dest in copy_steps:
            src_path = path / src
            dest_path = _resolve_copy_dest(sandbox, path, src, dest)
            dest_path.parent.mkdir(parents=True, exist_ok=True)
            if src_path.is_dir():
                copytree_for_build(src_path, dest_path)
            else:
                shutil.copy2(src_path, dest_path)
            logs.append({"stream": f"Copied {src_path} -> {dest_path}\n"})

        post_lines = [
            "#!/bin/bash",
            "set -euxo pipefail",
            "export HOME=/root",
            "export CONDA_ENVS_PATH=/opt/miniconda3/envs",
            "export CONDA_PKGS_DIRS=/opt/miniconda3/pkgs",
        ]
        for env in env_lines:
            if "=" in env:
                key, val = env.split("=", 1)
                post_lines.append(
                    f'export {key.strip()}="{val.strip().strip(chr(34)).strip(chr(39))}"'
                )
        post_lines.extend(run_lines)
        post_lines.append("")
        post_path = sandbox / "dockerfile-post.sh"
        post_path.write_text("\n".join(post_lines), encoding="utf-8")
        if run_lines or env_lines:
            logs.append({"stream": "Running Dockerfile post script in sandbox\n"})
            exec_args = [
                "exec",
                "--writable",
                str(sandbox),
                "bash",
                "/dockerfile-post.sh",
            ]
            proc = _run_apptainer(exec_args, timeout=timeout, check=False)
            logs.append(
                {
                    "stream": (proc.stdout or proc.stderr or b"").decode(
                        "utf-8", errors="replace"
                    )
                }
            )
            if proc.returncode != 0:
                raise container_errors.BuildError(
                    f"apptainer Dockerfile post script failed for {tag}",
                    logs[-1]["stream"],
                )

        if env_lines:
            env_dir = sandbox / ".singularity.d" / "env"
            env_dir.mkdir(parents=True, exist_ok=True)
            env_path = env_dir / "91-dockerfile-env.sh"
            env_path.write_text(_dockerfile_env_script(env_lines), encoding="utf-8")
            logs.append({"stream": f"Persisted Dockerfile ENV to {env_path}\n"})

        build_args = ["build", "--force"]
        if USE_FAKEROOT_BUILD:
            build_args.append("--fakeroot")
        build_args.extend([str(sif), str(sandbox)])
        proc = subprocess.Popen(
            [APPTAINER_BIN] + build_args,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        assert proc.stdout is not None
        for line in proc.stdout:
            text = line.decode("utf-8", errors="replace")
            logs.append({"stream": text})
        if proc.wait() != 0:
            raise container_errors.BuildError(
                f"apptainer build failed for {tag}",
                "".join(x["stream"] for x in logs),
            )
        return logs
    finally:
        shutil.rmtree(sandbox, ignore_errors=True)


def _docker_uri_for_local_tag(image_ref: str) -> str:
    """Map local SWE-bench tags to registry URIs for apptainer pull."""
    repo, tag = _split_image_ref(image_ref)
    if repo.startswith("ghcr.io/") or repo.startswith("docker.io/"):
        return f"docker://{repo}:{tag}"
    if "/" in repo and not repo.startswith("sweb.") and not repo.startswith("swebench"):
        return f"docker://{repo}:{tag}"
    # sweb.eval.x86_64.instance_id -> ghcr.io/epoch-research/swe-bench.eval...
    if repo.startswith("sweb.eval.") or repo.startswith("sweb.base.") or repo.startswith("sweb.env."):
        suffix = repo[len("sweb.") :]
        remote = f"{GHCR_EPOCH_PREFIX}/swe-bench.{suffix}:{tag}"
        return f"docker://{remote}"
    return f"docker://{repo}:{tag}"


@dataclass
class ExecResult:
    exit_code: int = 0
    output: Union[bytes, Iterator[bytes]] = b""

    def decode(self, encoding: str = "utf-8", errors: str = "replace") -> str:
        if isinstance(self.output, bytes):
            return self.output.decode(encoding, errors)
        return b"".join(self.output).decode(encoding, errors)


@dataclass
class ApptainerImage:
    tags: List[str] = field(default_factory=list)
    id: str = ""
    attrs: Dict[str, Any] = field(default_factory=dict)

    @property
    def short_id(self) -> str:
        return self.id[:12] if self.id else ""


class ApptainerImagesAPI:
    def __init__(self, client: "ApptainerClient") -> None:
        self._client = client

    def get(self, name: str) -> ApptainerImage:
        try:
            sif = self._client.images.resolve_sif(name)
            return ApptainerImage(tags=[name], id=sif.stem, attrs={"Created": ""})
        except container_errors.ImageNotFound:
            if name.startswith("sweb.env."):
                return ApptainerImage(
                    tags=[name], id=name.replace(":", "__"), attrs={"Created": ""}
                )
            raise

    def pull(self, repository: str, platform: Optional[str] = None) -> ApptainerImage:
        sif = self._client.images._pull_to_sif(repository, platform=platform)
        return ApptainerImage(tags=[repository], id=sif.stem)

    def remove(self, image_id: str, force: bool = False) -> None:
        if "sweb.eval." in image_id:
            return
        sif = _sif_path_for_tag(image_id)
        if sif.exists():
            sif.unlink()
        meta = sif.with_suffix(".json")
        if meta.exists():
            meta.unlink()

    def list(self, all: bool = True) -> List[ApptainerImage]:
        images: List[ApptainerImage] = []
        for image_dir in _candidate_image_dirs():
            for sif in image_dir.glob("*.sif"):
                meta_path = sif.with_suffix(".json")
                tags: List[str] = []
                if meta_path.exists():
                    try:
                        meta = json.loads(meta_path.read_text())
                        tags = meta.get("tags", [])
                    except Exception:
                        pass
                images.append(ApptainerImage(tags=tags, id=sif.stem))
        return images


class ApptainerImages:
    def __init__(self, client: "ApptainerClient") -> None:
        self._client = client

    def get(self, name: str) -> ApptainerImage:
        return ApptainerImagesAPI(self._client).get(name)

    def pull(self, repository: str, platform: Optional[str] = None) -> ApptainerImage:
        return ApptainerImagesAPI(self._client).pull(repository, platform=platform)

    def remove(self, image_id: str, force: bool = False) -> None:
        ApptainerImagesAPI(self._client).remove(image_id, force=force)

    def list(self, all: bool = True) -> List[ApptainerImage]:
        return ApptainerImagesAPI(self._client).list(all=all)

    def resolve_sif(self, image_ref: str) -> Path:
        for image_dir in _candidate_image_dirs():
            sif = _sif_path_for_tag(image_ref, image_dir)
            if sif.exists():
                return sif
        alias = self._resolve_multilingual_sif(image_ref)
        if alias is not None:
            return alias
        alias = self._resolve_sif_by_instance_suffix(image_ref)
        if alias is not None:
            return alias
        raise container_errors.ImageNotFound(f"Apptainer image not found: {image_ref}")

    def _resolve_multilingual_sif(self, image_ref: str) -> Optional[Path]:
        """Map SWE-bench Multilingual instance keys to pulled SIF filenames.

        make_test_spec() yields tags like ``sweb.eval.x86_64.apache__druid-14092:latest``
        while local Apptainer images use ``apache_1776_druid-14092`` in the filename/tag.
        """
        if not image_ref.startswith("sweb.eval.x86_64."):
            return None
        instance_part = image_ref.split("sweb.eval.x86_64.", 1)[-1].split(":", 1)[0]
        if "__" not in instance_part:
            return None
        org, rest = instance_part.split("__", 1)
        ml_suffix = f"{org}_1776_{rest}"
        filename_patterns = [
            f"swebench_sweb.eval.x86_64.{ml_suffix}_latest.sif",
            f"sweb.eval.x86_64.{ml_suffix}_latest.sif",
        ]
        for image_dir in _candidate_image_dirs():
            for pattern in filename_patterns:
                candidate = image_dir / pattern
                if candidate.exists():
                    return candidate
            for meta_path in image_dir.glob("*.json"):
                try:
                    tags = json.loads(meta_path.read_text()).get("tags", [])
                except Exception:
                    continue
                if not any(ml_suffix in tag for tag in tags):
                    continue
                candidate = meta_path.with_suffix(".sif")
                if candidate.exists():
                    return candidate
        return None

    def _resolve_sif_by_instance_suffix(self, image_ref: str) -> Optional[Path]:
        """Find a pulled SIF when SWE-bench namespace aliases rename the image tag."""
        match = re.search(r"([\w]+-\d+)(?::latest)?$", image_ref.split("/")[-1])
        if not match:
            return None
        suffix = match.group(1)
        for image_dir in _candidate_image_dirs():
            for meta_path in image_dir.glob("*.json"):
                try:
                    tags = json.loads(meta_path.read_text()).get("tags", [])
                except Exception:
                    continue
                if not any(suffix in tag for tag in tags):
                    continue
                candidate = meta_path.with_suffix(".sif")
                if candidate.exists():
                    return candidate
        return None

    def ensure(
        self,
        name: str,
        pull: bool = True,
        platform: Optional[str] = None,
    ) -> Path:
        try:
            return self.resolve_sif(name)
        except container_errors.ImageNotFound:
            if not pull:
                raise
        self.pull(name, platform=platform)
        return self.resolve_sif(name)

    def _pull_to_sif(self, image_ref: str, platform: Optional[str] = None) -> Path:
        image_dir = get_image_dir()
        image_dir.mkdir(parents=True, exist_ok=True)
        sif = _sif_path_for_tag(image_ref, image_dir)
        if sif.exists():
            return sif
        with _lock_for_path(sif):
            if sif.exists():
                return sif
            tmp = sif.with_suffix(".sif.tmp")
            if tmp.exists():
                tmp.unlink()
            uri = _docker_uri_for_local_tag(image_ref)
            pull_args = ["pull", str(tmp), uri]
            if platform:
                pull_args = ["pull", "--platform", platform, str(tmp), uri]
            proc = _run_apptainer(pull_args, timeout=self._client.timeout, check=False)
            if proc.returncode != 0:
                err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
                if tmp.exists():
                    tmp.unlink()
                raise container_errors.APIError(f"apptainer pull failed: {err}")
            tmp.rename(sif)
            tags = [image_ref]
            if image_ref.startswith("sweb.eval.x86_64.") and "__" in image_ref:
                try:
                    from utils.swebench_compat import make_test_spec
                    from datasets import load_dataset

                    instance_part = image_ref.split("sweb.eval.x86_64.", 1)[-1].split(
                        ":", 1
                    )[0]
                    rows = load_dataset("princeton-nlp/SWE-bench_Verified")["test"]
                    instance = next(
                        (row for row in rows if row["instance_id"] == instance_part),
                        None,
                    )
                    if instance is not None:
                        ns_key = make_test_spec(
                            instance, namespace="swebench"
                        ).instance_image_key
                        tags.append(ns_key)
                except Exception:
                    pass
            self._write_meta(sif, tags)
        return sif

    def build(
        self,
        path: Union[str, Path] = ".",
        tag: Optional[str] = None,
        rm: bool = True,
        nocache: bool = False,
        **kwargs: Any,
    ) -> Tuple[ApptainerImage, List[dict]]:
        path = Path(path).resolve()
        if not tag:
            tag = path.name
        image_dir = get_image_dir()
        image_dir.mkdir(parents=True, exist_ok=True)
        sif = _sif_path_for_tag(tag, image_dir)
        if sif.exists() and not nocache:
            return ApptainerImage(tags=[tag], id=sif.stem), []
        if sif.exists() and nocache:
            sif.unlink()

        dockerfile = path / "Dockerfile"
        if not dockerfile.exists():
            raise container_errors.BuildError(
                f"No Dockerfile in {path}", "missing Dockerfile"
            )

        dockerfile_text = dockerfile.read_text(encoding="utf-8")
        if dockerfile_text.lstrip().upper().startswith("FROM"):
            logs = _build_sif_from_dockerfile(
                path, tag, sif, timeout=self._client.timeout
            )
        else:
            build_args = ["build", "--force"]
            if USE_FAKEROOT_BUILD:
                build_args.append("--fakeroot")
            build_args.extend([str(sif), str(dockerfile)])
            logs = []
            proc = subprocess.Popen(
                [APPTAINER_BIN] + build_args,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                cwd=str(path),
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                text = line.decode("utf-8", errors="replace")
                logs.append({"stream": text})
            rc = proc.wait()
            if rc != 0:
                raise container_errors.BuildError(
                    f"apptainer build failed for {tag}",
                    "\n".join(x["stream"] for x in logs),
                )
        self._write_meta(sif, [tag])
        return ApptainerImage(tags=[tag], id=sif.stem), logs

    def _write_meta(self, sif: Path, tags: List[str]) -> None:
        meta = {"tags": tags, "sif": str(sif), "created": time.time()}
        sif.with_suffix(".json").write_text(json.dumps(meta))


class ApptainerContainer:
    def __init__(
        self,
        client: "ApptainerClient",
        image_ref: str,
        name: str,
        user: Optional[str] = None,
        network_mode: Optional[str] = None,
        platform: Optional[str] = None,
        nano_cpus: Optional[int] = None,
        cap_add: Optional[List[str]] = None,
        entrypoint: Optional[str] = None,
        command: Optional[List[str]] = None,
    ) -> None:
        self.client = client
        self.image_ref = image_ref
        self.name = name
        self.id = f"apptainer-{uuid.uuid4().hex[:12]}"
        self._user = user
        self._network_mode = network_mode
        self._platform = platform
        self._workspace = WORKSPACE_ROOT / _sanitize_tag(name)
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._binds: Dict[str, Path] = {}
        self._started = False
        self._instance_name: Optional[str] = None
        self._lock = threading.Lock()

    def _host_path_for(self, container_path: str) -> Path:
        container_path = container_path.rstrip("/") or "/"
        if container_path in self._binds:
            return self._binds[container_path]
        host = self._workspace / container_path.lstrip("/")
        host.mkdir(parents=True, exist_ok=True)
        self._binds[container_path] = host
        return host

    def _host_path_for_file(self, container_path: str) -> Path:
        """Bind a single file path (Apptainer cannot overlay bind /)."""
        container_path = "/" + container_path.lstrip("/")
        if container_path in self._binds:
            return self._binds[container_path]
        rel = container_path.lstrip("/").replace("/", "_")
        host = self._workspace / "files" / rel
        host.parent.mkdir(parents=True, exist_ok=True)
        self._binds[container_path] = host
        return host

    def _sif(self) -> Path:
        return self.client.images.ensure(self.image_ref, pull=True)

    def _sandbox_dir(self) -> Optional[Path]:
        sandbox = self._workspace / "rootfs"
        if sandbox.is_dir() and (sandbox / "bin").exists():
            return sandbox
        return None

    def _writable_rootfs(self) -> Path:
        """Extract image to a per-container writable sandbox (SIF rootfs is read-only)."""
        sandbox = self._sandbox_dir()
        if sandbox is not None:
            return sandbox
        sandbox = self._workspace / "rootfs"
        sif = self._sif()
        if sandbox.exists():
            shutil.rmtree(sandbox, ignore_errors=True)
        proc = _run_apptainer(
            ["build", "--sandbox", str(sandbox), str(sif)],
            timeout=self.client.timeout,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or b"").decode("utf-8", errors="replace")
            raise container_errors.APIError(f"apptainer sandbox build failed: {err}")
        self._prepare_sandbox_bind_targets(sandbox)
        return sandbox

    def _prepare_sandbox_bind_targets(self, sandbox: Path) -> None:
        """Writable sandboxes require bind destinations to exist in the rootfs."""
        for cpath, host in self._binds.items():
            rel = cpath.lstrip("/")
            if not rel:
                continue
            target = sandbox / rel
            if host.is_file():
                target.parent.mkdir(parents=True, exist_ok=True)
                if not target.exists():
                    target.touch()
            else:
                target.mkdir(parents=True, exist_ok=True)

    def _bind_args(self) -> List[str]:
        args: List[str] = []
        for cpath, hpath in sorted(self._binds.items()):
            args.extend(["--bind", f"{hpath}:{cpath}"])
        extra = os.environ.get("APPTAINER_BINDPATH", "")
        if extra:
            for part in extra.split(","):
                part = part.strip()
                if part:
                    args.extend(["--bind", part])
        return args

    def _exec_base_args(
        self,
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
    ) -> List[str]:
        args = ["exec", "--writable"]
        if USE_NV:
            args.append("--nv")
        if self._network_mode == "none":
            args.append("--network", "none")
        elif self._network_mode == "host" and USE_HOST_NETWORK:
            args.extend(["--net", "--network", "host"])
        args.extend(self._bind_args())
        if workdir:
            args.extend(["--pwd", workdir])
        for key, val in (environment or {}).items():
            if val is not None:
                args.extend(["--env", f"{key}={val}"])
        args.append(str(self._writable_rootfs()))
        return args

    def _wrap_user_cmd(self, run_cmd: List[str]) -> List[str]:
        """Apptainer exec has no --user; run non-root payloads via su inside the image."""
        if not self._user or self._user in ("root", "0"):
            return run_cmd
        inner = " ".join(shlex.quote(str(c)) for c in run_cmd)
        return ["/bin/sh", "-c", f"exec su -s /bin/sh {shlex.quote(self._user)} -c {shlex.quote(inner)}"]

    def start(self) -> None:
        self._started = True

    def stop(self, timeout: int = 15) -> None:
        if self._instance_name:
            try:
                _run_apptainer(["instance stop", self._instance_name], check=False)
            except Exception:
                pass
            self._instance_name = None

    def remove(self, force: bool = False) -> None:
        self.stop()
        if force and self._workspace.exists():
            shutil.rmtree(self._workspace, ignore_errors=True)

    def exec_run(
        self,
        cmd: Union[str, List[str]],
        workdir: Optional[str] = None,
        environment: Optional[Dict[str, str]] = None,
        detach: bool = False,
        **kwargs: Any,
    ) -> ExecResult:
        if isinstance(cmd, list):
            run_cmd = [str(c) for c in cmd]
        else:
            run_cmd = ["/bin/sh", "-c", cmd]

        run_cmd = self._wrap_user_cmd(run_cmd)

        with self._lock:
            sandbox = self._writable_rootfs()
            self._prepare_sandbox_bind_targets(sandbox)

            base = self._exec_base_args(workdir=workdir, environment=environment)

            full_cmd = [APPTAINER_BIN] + base + run_cmd
            proc = subprocess.run(
                full_cmd,
                capture_output=True,
                timeout=self.client.timeout,
            )
        return ExecResult(exit_code=proc.returncode, output=proc.stdout + proc.stderr)

    def put_archive(self, path: str, data: bytes) -> bool:
        dest_dir = path.rstrip("/") or "/"
        with self._lock:
            sandbox = self._writable_rootfs()
            self._prepare_sandbox_bind_targets(sandbox)
        stream = io.BytesIO(data)
        with tarfile.open(fileobj=stream, mode="r") as tar:
            for member in tar.getmembers():
                if dest_dir == "/":
                    container_path = "/" + member.name.lstrip("/")
                else:
                    container_path = f"{dest_dir}/{member.name}".replace("//", "/")

                # Never bind-mount an entire sandbox path (e.g. /testbed, /tmp):
                # that replaces the writable rootfs dir with an empty host folder.
                use_sandbox = dest_dir not in self._binds

                if member.isdir():
                    if use_sandbox:
                        target = sandbox / container_path.lstrip("/")
                        target.mkdir(parents=True, exist_ok=True)
                        continue
                    host_root = self._host_path_for(dest_dir)
                    (host_root / member.name).mkdir(parents=True, exist_ok=True)
                elif member.isfile():
                    src = tar.extractfile(member)
                    if src is None:
                        continue
                    payload = src.read()
                    if use_sandbox:
                        target = sandbox / container_path.lstrip("/")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload)
                        continue
                    if dest_dir == "/":
                        target = sandbox / member.name.lstrip("/")
                        target.parent.mkdir(parents=True, exist_ok=True)
                        target.write_bytes(payload)
                        continue
                    host_root = self._host_path_for(dest_dir)
                    target = host_root / member.name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    with open(target, "wb") as out:
                        out.write(payload)
        return True

    def get_archive(self, path: str) -> Tuple[List[bytes], dict]:
        container_path = Path(path)
        host_path: Optional[Path] = None
        for cpath, hpath in self._binds.items():
            if path == cpath or path.startswith(cpath + "/"):
                rel = path[len(cpath) :].lstrip("/")
                host_path = hpath / rel if rel else hpath
                break
        if host_path is None or not host_path.exists():
            tmp = self._workspace / "archive_extract"
            tmp.mkdir(parents=True, exist_ok=True)
            result = self.exec_run(f"test -e {path}")
            if result.exit_code != 0:
                raise FileNotFoundError(f"Path not found in container: {path}")
            is_file = self.exec_run(f"test -f {path}").exit_code == 0
            if is_file:
                host_path = tmp / "file"
                cp = self.exec_run(f"cat {path}", workdir="/")
                host_path.write_bytes(cp.output if isinstance(cp.output, bytes) else b"")
            else:
                host_path = tmp / container_path.name
                self.exec_run(f"cp -a {path} {host_path}", workdir="/")

        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w") as tar:
            if host_path.is_file():
                tar.add(host_path, arcname=host_path.name)
            else:
                tar.add(host_path, arcname=host_path.name)
        data = buf.getvalue()
        return [data], {"name": host_path.name}


class ApptainerContainers:
    def __init__(self, client: "ApptainerClient") -> None:
        self._client = client
        self._registry: Dict[str, ApptainerContainer] = {}

    def create(self, image: str, name: str, **kwargs: Any) -> ApptainerContainer:
        container = ApptainerContainer(
            self._client,
            image_ref=image,
            name=name,
            user=kwargs.get("user"),
            network_mode=kwargs.get("network_mode"),
            platform=kwargs.get("platform"),
            nano_cpus=kwargs.get("nano_cpus"),
            cap_add=kwargs.get("cap_add"),
            entrypoint=kwargs.get("entrypoint"),
            command=kwargs.get("command"),
        )
        self._registry[name] = container
        return container

    def run(self, image: str, name: str, detach: bool = True, **kwargs: Any) -> ApptainerContainer:
        container = self.create(image=image, name=name, **kwargs)
        container.start()
        return container

    def get(self, name: str) -> ApptainerContainer:
        if name in self._registry:
            return self._registry[name]
        raise container_errors.NotFound(f"Container {name} not found")

    def list(self, all: bool = False, **kwargs: Any) -> List[ApptainerContainer]:
        return list(self._registry.values())


class ApptainerClientAPI:
    def __init__(self, client: "ApptainerClient") -> None:
        self._client = client

    def build(
        self,
        path: str,
        tag: str,
        rm: bool = True,
        forcerm: bool = True,
        decode: bool = True,
        platform: Optional[str] = None,
        nocache: bool = False,
    ) -> Iterator[dict]:
        image, logs = self._client.images.build(
            path=path, tag=tag, rm=rm, nocache=nocache
        )
        for entry in logs:
            yield entry

    def inspect_container(self, container_id: str) -> dict:
        return {"State": {"Pid": 0}}


class ApptainerClient:
    """DockerClient-compatible facade using Apptainer."""

    def __init__(self, timeout: Optional[int] = None) -> None:
        self.timeout = timeout or int(
            os.environ.get("APPTAINER_API_TIMEOUT", os.environ.get("CONTAINER_API_TIMEOUT", "600"))
        )
        img_dir = get_image_dir()
        img_dir.mkdir(parents=True, exist_ok=True)
        WORKSPACE_ROOT.mkdir(parents=True, exist_ok=True)
        self.images = ApptainerImages(self)
        self.containers = ApptainerContainers(self)
        self.api = ApptainerClientAPI(self)

    def ping(self) -> bool:
        proc = _run_apptainer(["version"], timeout=30, check=False)
        return proc.returncode == 0

    def info(self) -> dict:
        proc = _run_apptainer(["version"], timeout=30, check=False)
        version = (proc.stdout or b"").decode("utf-8", errors="replace").strip()
        return {"Name": f"apptainer ({version.splitlines()[0] if version else 'unknown'})"}

    def version(self) -> dict:
        proc = _run_apptainer(["version"], timeout=30, check=False)
        text = (proc.stdout or b"").decode("utf-8", errors="replace")
        for line in text.splitlines():
            if "apptainer version" in line.lower():
                return {"Version": line.split()[-1]}
        return {"Version": "unknown"}
