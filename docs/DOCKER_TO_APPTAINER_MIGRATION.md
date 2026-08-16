# MendelGM：Docker → Apptainer 业务逻辑修改详解

本文档按**业务模块**整理 MendelGM 从 Docker 迁移到 Apptainer 的具体代码修改，每个模块包含：

- 原来 Docker 做什么
- 改了什么、为什么改
- **仓库中的实际代码**

相关总览文档见仓库根目录 [`APPTAINER.md`](../APPTAINER.md)。

---

## 目录

1. [公共兼容层](#1-公共兼容层)
2. [SWE-bench Agent 评测](#2-swe-bench-agent-评测)
3. [SWE-bench 评分（Grading）](#3-swe-bench-评分grading)
4. [HGM 自进化（sample_child）](#4-hgm-自进化sample_child)
5. [Polyglot 多语言评测](#5-polyglot-多语言评测)
6. [SWE-bench Pro / Multilingual](#6-swe-bench-pro--multilingual)
7. [镜像拉取与管理](#7-镜像拉取与管理)
8. [Slurm 运行时与环境变量](#8-slurm-运行时与环境变量)
9. [vLLM 网络访问](#9-vllm-网络访问)
10. [修改文件索引](#10-修改文件索引)

---

## 1. 公共兼容层

### 1.1 原来（Docker）

业务代码统一通过 docker-py 连接 Docker daemon：

```python
import docker
client = docker.from_env(timeout=600)
client.images.pull("sweb.eval.x86_64.django__django-10973:latest")
container = client.containers.create(image=..., name=..., detach=True)
container.start()
container.exec_run("python /hgm/coding_agent.py")
```

### 1.2 现在（Apptainer）

**策略**：保留 `docker_from_env()` 等函数名和 docker-py 风格 API，底层换成 `ApptainerClient`。

#### 入口：`utils/container_runtime.py`

```python
from utils.apptainer_compat import ApptainerClient

def container_from_env(timeout: int | None = None) -> ApptainerClient:
    """Return the Apptainer client used by all harness and self-improve paths."""
    return ApptainerClient(timeout=timeout)
```

#### 兼容别名：`utils/docker_utils.py`

```python
def docker_from_env(timeout: Optional[int] = None):
    """Return the local Apptainer client (docker-py compatible API)."""
    from utils.container_runtime import container_from_env

    if timeout is None:
        timeout = get_container_api_timeout()
    return container_from_env(timeout=timeout)
```

#### 异常类：`utils/apptainer_errors.py`

```python
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
```

业务代码 `from utils import apptainer_errors as container_errors` 后，`except container_errors.ImageNotFound` 无需改动。

---

### 1.3 核心实现：`utils/apptainer_compat.py`

#### 1.3.1 镜像 tag → `.sif` 文件路径

```python
def _sif_path_for_tag(image_ref: str, image_dir: Optional[Path] = None) -> Path:
    repo, tag = _split_image_ref(image_ref)
    safe = _sanitize_tag(f"{repo}_{tag}")
    return (image_dir or get_image_dir()) / f"{safe}.sif"
```

示例：

```
Docker tag:  sweb.eval.x86_64.django__django-10973:latest
Apptainer:   {APPTAINER_IMAGE_DIR}/sweb.eval.x86_64.django__django-10973_latest.sif
```

#### 1.3.2 `images.pull()` → `apptainer pull`

本地 SWE-bench tag 自动映射到 Epoch GHCR URI：

```python
def _docker_uri_for_local_tag(image_ref: str) -> str:
    repo, tag = _split_image_ref(image_ref)
    if repo.startswith("sweb.eval.") or repo.startswith("sweb.base.") or repo.startswith("sweb.env."):
        suffix = repo[len("sweb.") :]
        remote = f"{GHCR_EPOCH_PREFIX}/swe-bench.{suffix}:{tag}"
        return f"docker://{remote}"
    return f"docker://{repo}:{tag}"
```

实际拉取（带并发锁，先写 `.sif.tmp` 再 rename）：

```python
def _pull_to_sif(self, image_ref: str, platform: Optional[str] = None) -> Path:
    sif = _sif_path_for_tag(image_ref, image_dir)
    with _lock_for_path(sif):
        tmp = sif.with_suffix(".sif.tmp")
        uri = _docker_uri_for_local_tag(image_ref)
        pull_args = ["pull", str(tmp), uri]
        proc = _run_apptainer(pull_args, timeout=self._client.timeout, check=False)
        ...
        tmp.rename(sif)
        self._write_meta(sif, tags)
    return sif
```

等价 shell：

```bash
apptainer pull /path/to/image.sif.tmp docker://ghcr.io/epoch-research/swe-bench.eval.x86_64.django__django-10973:latest
mv image.sif.tmp image.sif
```

#### 1.3.3 `containers.create()` → 创建 workspace（非 daemon 容器）

```python
class ApptainerContainer:
    def __init__(self, client, image_ref, name, user=None, network_mode=None, ...):
        self.id = f"apptainer-{uuid.uuid4().hex[:12]}"
        self._workspace = WORKSPACE_ROOT / _sanitize_tag(name)
        self._workspace.mkdir(parents=True, exist_ok=True)
        self._binds: Dict[str, Path] = {}
```

`start()` 仅设标记，**不启动长期进程**：

```python
def start(self) -> None:
    self._started = True
```

#### 1.3.4 SIF 只读 → 先建可写 sandbox

```python
def _writable_rootfs(self) -> Path:
    """Extract image to a per-container writable sandbox (SIF rootfs is read-only)."""
    sandbox = self._workspace / "rootfs"
    sif = self._sif()
    proc = _run_apptainer(
        ["build", "--sandbox", str(sandbox), str(sif)],
        timeout=self.client.timeout,
        check=False,
    )
    ...
    return sandbox
```

等价 shell：

```bash
apptainer build --sandbox /workspace/rootfs/ /path/to/image.sif
```

#### 1.3.5 `exec_run()` → `apptainer exec --writable`

```python
def exec_run(self, cmd, workdir=None, environment=None, detach=False, **kwargs):
    run_cmd = self._wrap_user_cmd(run_cmd)  # 非 root 用 su 包装
    with self._lock:
        sandbox = self._writable_rootfs()
        base = self._exec_base_args(workdir=workdir, environment=environment)
        full_cmd = [APPTAINER_BIN] + base + run_cmd
        proc = subprocess.run(full_cmd, capture_output=True, timeout=self.client.timeout)
    return ExecResult(exit_code=proc.returncode, output=proc.stdout + proc.stderr)
```

`_exec_base_args` 组装 bind、网络、环境变量：

```python
def _exec_base_args(self, workdir=None, environment=None):
    args = ["exec", "--writable"]
    if self._network_mode == "host" and USE_HOST_NETWORK:
        args.extend(["--net", "--network", "host"])
    args.extend(self._bind_args())  # --bind host:container
    if workdir:
        args.extend(["--pwd", workdir])
    for key, val in (environment or {}).items():
        args.extend(["--env", f"{key}={val}"])
    args.append(str(self._writable_rootfs()))
    return args
```

非 root 用户（Apptainer 无 `--user`）：

```python
def _wrap_user_cmd(self, run_cmd):
    if not self._user or self._user in ("root", "0"):
        return run_cmd
    inner = " ".join(shlex.quote(str(c)) for c in run_cmd)
    return ["/bin/sh", "-c", f"exec su -s /bin/sh {shlex.quote(self._user)} -c {shlex.quote(inner)}"]
```

#### 1.3.6 `put_archive()` — bind 与 sandbox 语义差异

Apptainer 不能把整个 `/testbed` bind 成空宿主机目录（会覆盖 rootfs），因此写入 sandbox 内文件：

```python
def put_archive(self, path: str, data: bytes) -> bool:
    ...
    # Never bind-mount an entire sandbox path (e.g. /testbed, /tmp):
    # that replaces the writable rootfs dir with an empty host folder.
    use_sandbox = dest_dir not in self._binds
    ...
    if use_sandbox:
        target = sandbox / container_path.lstrip("/")
        target.write_bytes(payload)
```

---

## 2. SWE-bench Agent 评测

**文件**：`swe_bench/harness.py`

### 2.1 原来（Docker）

```python
client = docker.from_env()
container = client.containers.create(image=instance_image_key, ...)
container.start()
container.put_archive("/hgm", agent_tar)
container.exec_run("python /hgm/coding_agent.py", environment={"VLLM_HOST": "127.0.0.1"})
```

### 2.2 现在

**调用链不变**，`docker_from_env()` 返回 `ApptainerClient`。容器创建逻辑在 `build_container_with_network()`：

```python
def build_container_with_network(test_spec, client, run_id, logger, nocache, force_rebuild=False):
    instance_image_exists = False
    try:
        client.images.get(test_spec.instance_image_key)
        instance_image_exists = True
    except container_errors.ImageNotFound:
        pass

    if not instance_image_exists:
        if not test_spec.is_remote_image:
            build_instance_image(test_spec, client, logger, nocache)
        else:
            client.images.pull(test_spec.instance_image_key)  # → apptainer pull

    network_mode = os.getenv("SWE_CONTAINER_NETWORK", "host")
    container = client.containers.create(
        image=test_spec.instance_image_key,
        name=test_spec.get_instance_container_name(run_id),
        user=DOCKER_USER,
        detach=True,
        command="tail -f /dev/null",
        network_mode=network_mode,
    )
    return container
```

**关键变化**：

| 点 | Docker 时代 | Apptainer 时代 |
|----|------------|----------------|
| 镜像来源 | `docker pull` 到 daemon | 预拉 `.sif` 或 `apptainer pull` |
| 容器进程 | `docker run -d` 长期存在 | workspace + 每次 `exec` 起子进程 |
| 网络 | `network_mode=host` 直接生效 | 需 `APPTAINER_USE_HOST_NETWORK=1` 才真正加 host 网络 |

vLLM 连通性检查失败时的提示（Apptainer 特有问题）：

```python
raise RuntimeError(
    "vLLM is not reachable from the SWE-bench task container at "
    f"{vllm_url}. Set VLLM_CONTAINER_HOST to this node's IP "
    f"(hostname -I) when APPTAINER_USE_HOST_NETWORK=0. Output: {output}"
)
```

---

## 3. SWE-bench 评分（Grading）

**文件**：`swe_bench/report.py`、`swe_bench/run_evaluation_apptainer.py`

### 3.1 原来（Docker）

```python
# report.py 子进程直接调上游
subprocess.run([sys.executable, "-m", "swebench.harness.run_evaluation", ...])
# 上游内部: docker.from_env() → build_env_images → docker run 跑测试
```

### 3.2 现在

#### 3.2.1 改评分入口：`swe_bench/report.py`

```python
cmd = [
    sys.executable,
    "-m",
    "swe_bench.run_evaluation_apptainer",  # 不再直接调 swebench.harness.run_evaluation
    "--dataset_name", dataset_name,
    "--predictions_path", predictions_jsonl,
    "--max_workers", str(num_eval_procs),
    "--run_id", run_id,
    # SWE-bench 默认 namespace=swebench 会改写 image key，导致 resolve_sif 失败
    "--namespace", "",
]
subprocess.run(cmd, check=True, cwd=root_dir)
```

#### 3.2.2 新建 Apptainer 评分模块：`swe_bench/run_evaluation_apptainer.py`

**Monkey-patch `docker.from_env`**，让上游 swebench 无感知：

```python
import docker as _docker_mod
from utils.docker_utils import docker_from_env

_docker_mod.from_env = lambda timeout=None: docker_from_env(
    timeout=timeout if timeout is not None else 7200
)
```

**Patch 上游构建与评分逻辑**：

```python
def _patch_swebench_for_apptainer() -> None:
    import swebench.harness.constants as harness_constants
    import swebench.harness.docker_build as docker_build
    import swebench.harness.docker_utils as docker_utils
    import swe_bench.harness as mendel_swe_harness

    # /tmp 被 bind 进容器后，patch 文件对 sandbox rootfs 不可见
    harness_constants.DOCKER_PATCH = "/root/patch.diff"

    def _skip_build_env_images(client, dataset, *args, **kwargs):
        print("Apptainer: skipping build_env_images (using pre-pulled instance images)")
        return []

    def _skip_build_base_images(client, *args, **kwargs):
        print("Apptainer: skipping build_base_images")
        return []

    def _preserve_instance_remove_image(client, image_key, logger=None, *args, **kwargs):
        if "sweb.eval." in image_key:
            if logger is not None:
                logger.info(f"Preserving pre-pulled instance image {image_key}")
            return
        return docker_build.remove_image(client, image_key, logger, *args, **kwargs)

    def _apptainer_exec_run_with_timeout(container, cmd, timeout: int | None = 60):
        if timeout is not None and timeout > 0:
            cmd_str = f"timeout {int(timeout)} {cmd_str}"
        result = container.exec_run(cmd_str, workdir="/")
        timed_out = result.exit_code == 124
        return output, timed_out, elapsed

    docker_build.build_env_images = _skip_build_env_images
    docker_build.build_base_images = _skip_build_base_images
    docker_build.build_container = mendel_swe_harness.build_container_with_network
    docker_build.remove_image = _preserve_instance_remove_image
    docker_utils.exec_run_with_timeout = _apptainer_exec_run_with_timeout

_patch_swebench_for_apptainer()

if __name__ == "__main__":
    import runpy
    runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")
```

**修改汇总**：

| 上游 Docker 行为 | Apptainer 适配 |
|----------------|----------------|
| `build_env_images` / `build_base_images` | 跳过，用预拉 SIF |
| `DOCKER_PATCH=/tmp/patch.diff` | 改为 `/root/patch.diff` |
| 评分后 `remove_image` | 保留 `sweb.eval.*` |
| `exec_run_with_timeout` | 用 shell `timeout` 包装 |

---

## 4. HGM 自进化（sample_child）

**文件**：`hgm_utils.py`、`utils/docker_utils.py`、`apptainer/hgm.def`

### 4.1 原来（Docker）

```python
client = docker.from_env()
container = build_hgm_container(client, repo_path="./", ...)  # bind 宿主仓库到 /hgm
container.exec_run("cd /hgm && git init && ...")  # 多 worker 并行会破坏共享 .git
```

基础镜像直接用 `python:3.10-slim`（**没有 git**）。

### 4.2 现在

#### 4.2.1 专用 HGM 镜像定义：`apptainer/hgm.def`

```singularity
Bootstrap: docker
From: python:3.10-slim

%post
    apt-get update && apt-get install -y build-essential git \
        && apt-get clean && rm -rf /var/lib/apt/lists/*
    mkdir -p /hgm

%environment
    export LANG=C.UTF-8
```

#### 4.2.2 构建 HGM SIF：`utils/docker_utils.py`

```python
def _build_hgm_image_sif(client, image_name: str) -> None:
    def_file = repo_root / "apptainer" / "hgm.def"
    sif = _sif_path_for_tag(image_name)
    cmd = [os.environ.get("APPTAINER_BIN", "apptainer"), "build", "--force"]
    if os.environ.get("APPTAINER_BUILD_FAKEROOT", "1") == "1":
        cmd.append("--fakeroot")
    cmd.extend([str(sif), str(def_file)])
    subprocess.run(cmd, ...)
    client.images._write_meta(sif, [image_name])
```

#### 4.2.3 创建容器并 bind `/hgm`

```python
def build_hgm_container(client, repo_path="./", image_name="app", container_name="app-container", ...):
    container = client.containers.create(
        image=image_name,
        name=container_name,
        detach=True,
        network_mode="host",
        command="tail -f /dev/null",
    )
    container._binds["/hgm"] = _Path(repo_path)  # Apptainer bind mount
    container.start()
    _ensure_hgm_container_packages(container)  # 检查 git 是否存在
    return container
```

#### 4.2.4 并行自进化：per-run workspace 副本

**问题**：多 worker bind 同一宿主目录 → 容器内 `git init` 互相破坏。

**解决**：`hgm_utils.py`

```python
def _prepare_selfimprove_workspace(host_repo: str, run_id: str) -> Path:
    """Copy host repo into a per-run workspace so parallel workers do not share .git."""
    workspace_root = Path(
        os.environ.get(
            "APPTAINER_WORKSPACE_ROOT",
            os.path.join(os.environ.get("TMPDIR", "/tmp"), "apptainer-workspaces"),
        )
    )
    workspace = workspace_root / f"selfimprove-{run_id}"
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    shutil.copytree(host_repo, workspace, ignore=_selfimprove_copy_ignore, dirs_exist_ok=False)
    return workspace
```

`sample_child()` 中使用：

```python
client = docker_from_env()
hgm_workspace = _prepare_selfimprove_workspace(root_dir, run_id)
container = build_hgm_container(client, str(hgm_workspace), image_name, container_name, ...)
...
finally:
    if hgm_workspace is not None and hgm_workspace.exists():
        shutil.rmtree(hgm_workspace, ignore_errors=True)
```

---

## 5. Polyglot 多语言评测

**文件**：`polyglot/harness.py`、`polyglot/docker_build.py`、`polyglot/docker_utils.py`、`polyglot_scripts/polyglot_agent_runtime.inc.sh`

### 5.1 原来（Docker）

- `docker_from_env()` 起语言任务容器
- agent 在容器内跑（Python 任务）或宿主机跑（非 Python 任务）
- `docker_build.py` 用 `client.images.build` 建实例镜像

### 5.2 现在

#### 5.2.1 harness 仍用 `docker_from_env()`（底层已是 Apptainer）

```python
# polyglot/harness.py
from utils.docker_utils import docker_from_env

client = docker_from_env()
```

#### 5.2.2 非 Python 任务：宿主机 agent venv + `$HOME` auto-mount

Apptainer 会自动把 `$HOME` mount 进容器，因此 agent venv 必须放在 `$HOME` 下：

```bash
# polyglot_scripts/polyglot_agent_runtime.inc.sh
# Non-Python task containers have no Python; Apptainer auto-mounts $HOME into containers.

agent_rt="${POLYGLOT_AGENT_RUNTIME_DIR:-${HOME}/.cache/polyglot_agent_runtime}"
...
export POLYGLOT_HOST_AGENT_PYTHON="${agent_venv_py}"
```

Python 路径解析：

```python
# polyglot/harness.py
def resolve_host_agent_python() -> str | None:
    """Host-built agent venv Python, auto-mounted into Apptainer/Docker containers."""
    for key in ("POLYGLOT_HOST_AGENT_PYTHON", "SWE_ML_HOST_AGENT_PYTHON"):
        val = os.environ.get(key, "").strip()
        if val:
            return val
    return None
```

#### 5.2.3 容器创建：`polyglot/docker_build.py`

```python
network_mode = os.environ.get("POLYGLOT_CONTAINER_NETWORK_MODE", "host")
container = client.containers.create(
    image=test_spec.instance_image_key,
    name=test_spec.get_instance_container_name(run_id),
    user=user,  # nonroot 时 Apptainer 内部用 su
    detach=True,
    command="tail -f /dev/null",
    network_mode=network_mode,
    ...
)
```

#### 5.2.4 Apptainer 容器内 agent 挂起：重试逻辑

Apptainer 下 Python import / API 调用可能长时间无输出，`polyglot/harness.py` 新增：

```python
AGENT_IDLE_TIMEOUT_SECONDS = int(os.environ.get("AGENT_IDLE_TIMEOUT_SECONDS", "600"))
AGENT_MAX_ATTEMPTS = int(os.environ.get("AGENT_MAX_ATTEMPTS", "3"))

def run_agent_with_startup_retry(container, env_vars, cmd, logger, ...):
    """Run the coding agent, retrying when it stalls during startup.

    Some Apptainer container instances intermittently hang during Python
    import (openai/anthropic SSL or DNS init), producing zero stdout...
    """
```

#### 5.2.5 `polyglot/docker_utils.py` 类型标注

```python
from utils import apptainer_errors as container_errors
from utils.apptainer_compat import ApptainerContainer

def copy_to_container(container: ApptainerContainer, src: Path, dst: Path):
    ...
```

---

## 6. SWE-bench Pro / Multilingual

**文件**：`SWEbench_Pro/run_agent_eval.py`、`SWEbench_Multilingual/run_agent_eval.py`

### 6.1 原来（Docker）

```python
import docker
client = docker.from_env(timeout=7200)
```

### 6.2 现在

直接 `container_from_env()`：

```python
# SWEbench_Pro/run_agent_eval.py
from utils.container_runtime import container_from_env

client = container_from_env(timeout=args.container_timeout)
print(f"Connected to Apptainer: {client.info().get('Name', 'unknown')}")
```

### 6.3 Multilingual 特有问题：不能覆盖 PATH

Apptainer `--env PATH=...` 会**完全替换**镜像内 PATH，导致 cargo/go/mvnd 消失：

```python
# SWEbench_Multilingual/run_agent_eval.py
def container_exec_environment(extra=None) -> dict[str, str]:
    # NOTE: intentionally do NOT set PATH here. Each SWE-bench Multilingual task
    # image puts its language toolchain on PATH via the image ENV (e.g. Rust
    # /usr/local/cargo/bin, Go /usr/local/go/bin, Java /usr/local/mvnd/bin). If we
    # pass --env PATH=... Apptainer fully REPLACES the image PATH, so cargo/go/mvnd
    # vanish -> the agent can't run tests...
    env: dict[str, str] = {
        "PIP_CONFIG_FILE": "/dev/null",
        "PYTHONNOUSERSITE": "1",
        "SSL_CERT_FILE": ssl_cert,
        ...
    }
    return env
```

PATH 只在 shell 脚本里 **append**，不 replace：

```python
def wrap_container_script(script: str) -> str:
    return (
        f'export PATH="${{PATH:+$PATH:}}{CONTAINER_PATH}"; '
        ...
        f"{script}"
    )
```

---

## 7. 镜像拉取与管理

**文件**：`scripts/pull_epoch_images.py`、`scripts/pull_epoch_images_proxy.py`

### 7.1 原来（Docker）

```python
import docker
client = docker.from_env(timeout=120)
client.images.pull(f"sweb.eval.x86_64.{id_lower}:latest")
# 或通过 SSH 隧道拉到远端 VM 的 Docker daemon（pull_epoch_images_proxy.py）
```

### 7.2 现在

#### 7.2.1 本地 Apptainer pull：`scripts/pull_epoch_images.py`

```python
from utils.container_runtime import container_from_env

print("Connecting to local Apptainer...")
client = container_from_env(timeout=120)

local_tag = f"sweb.eval.x86_64.{id_lower}:latest"
client.images.pull(local_tag)  # 内部 → apptainer pull xxx.sif docker://ghcr.io/...
```

#### 7.2.2 远端 Docker 代理已废弃

```python
# scripts/pull_epoch_images_proxy.py
"""Deprecated: use scripts/pull_epoch_images.py (local Apptainer pull).

This script previously proxied images into a remote Docker daemon.
MendelGM now uses local Apptainer only.
"""
```

Slurm 预拉取示例（`swe_scripts/mgm.slurm` 等）：

```bash
. "${REPO_ROOT}/swe_scripts/apptainer_runtime.inc.sh"
apptainer_runtime_verify
timeout 600 python -u scripts/pull_epoch_images.py all
```

---

## 8. Slurm 运行时与环境变量

**文件**：`swe_scripts/apptainer_runtime.inc.sh`、`swe_scripts/cache_env.inc.sh`

### 8.1 原来（Docker）

```bash
export ENABLE_REMOTE_DOCKER=1
export REMOTE_DOCKER_USER=ubuntu
export REMOTE_DOCKER_HOST=your.vm.example.com
export DOCKER_HOST="unix://${REMOTE_DOCKER_SOCKET}"
# SSH 隧道转发 docker.sock
```

### 8.2 现在：`swe_scripts/apptainer_runtime.inc.sh`

```bash
export APPTAINER_IMAGE_DIR="${APPTAINER_IMAGE_DIR:-${REPO_ROOT}/apptainer_images}"
export APPTAINER_WORKSPACE_ROOT="${APPTAINER_WORKSPACE_ROOT:-${SLURM_TMPDIR}/apptainer-workspaces-${SLURM_JOB_ID}}"
export APPTAINER_USE_HOST_NETWORK="${APPTAINER_USE_HOST_NETWORK:-0}"

# 无 host 网络时，容器内不能用 127.0.0.1 访问宿主机 vLLM
_apptainer_host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
export VLLM_CONTAINER_HOST="${VLLM_CONTAINER_HOST:-${_apptainer_host_ip:-127.0.0.1}}"

apptainer_runtime_verify() {
    if ! command -v apptainer >/dev/null 2>&1; then
        echo "ERROR: apptainer not found in PATH." >&2
        return 1
    fi
    ...
}
```

`cache_env.inc.sh` 把 `TMPDIR` 指到大文件系统，避免并发 sandbox 撑爆节点 `/tmp`：

```bash
# writable Apptainer sandboxes can exhaust small local tmp and trigger SIGKILL.
export TMPDIR="${SWE_EVAL_TMPDIR:-${JINHE_CACHE_ROOT}/swe_tmp/${SLURM_JOB_ID}}"
```

---

## 9. vLLM 网络访问

### 9.1 原来（Docker）

`network_mode=host` 时，容器内 `127.0.0.1:8000` 即宿主机 vLLM。

### 9.2 现在（Apptainer）

HPC 上默认 **不能** 用 host 网络（需 root/fakeroot），容器内 `127.0.0.1` 指向容器自己。

**解决**：`apptainer_runtime.inc.sh` 设置 `VLLM_CONTAINER_HOST` 为节点 IP；`llm.py` 传入容器：

```python
# llm.py — llm_container_env()
"VLLM_HOST": os.getenv("VLLM_CONTAINER_HOST", "127.0.0.1"),
"VLLM_PORT": os.getenv("REMOTE_VLLM_PORT", os.getenv("VLLM_PORT", "8000")),
```

若确实能用 host 网络：

```bash
export APPTAINER_USE_HOST_NETWORK=1
export VLLM_CONTAINER_HOST=127.0.0.1
```

---

## 10. 修改文件索引

| 模块 | 主要文件 | 修改类型 |
|------|----------|----------|
| 兼容层 | `utils/apptainer_compat.py` | **新增**，docker-py → apptainer CLI |
| 兼容层 | `utils/container_runtime.py` | **新增**，运行时入口 |
| 兼容层 | `utils/apptainer_errors.py` | **新增**，异常类 |
| 兼容层 | `utils/docker_utils.py` | **改**，`docker_from_env` 转发 + HGM SIF 构建 |
| SWE Agent | `swe_bench/harness.py` | **小改**，错误提示、仍用兼容 API |
| SWE Grading | `swe_bench/run_evaluation_apptainer.py` | **新增**，monkey-patch 上游 |
| SWE Grading | `swe_bench/report.py` | **改**，评分入口 |
| HGM | `hgm_utils.py` | **改**，per-run workspace |
| HGM | `apptainer/hgm.def` | **新增**，HGM 镜像定义 |
| Polyglot | `polyglot/harness.py` | **改**，agent 重试、host python |
| Polyglot | `polyglot/docker_build.py` | **改**，底层 shim |
| Polyglot | `polyglot_scripts/polyglot_agent_runtime.inc.sh` | **新增/改**，宿主机 venv |
| SWE-Pro/ML | `SWEbench_Pro/run_agent_eval.py` | **改**，`container_from_env` |
| SWE-Pro/ML | `SWEbench_Multilingual/run_agent_eval.py` | **改**，PATH 不覆盖 |
| 镜像 | `scripts/pull_epoch_images.py` | **改**，Apptainer pull |
| 镜像 | `scripts/pull_epoch_images_proxy.py` | **废弃** |
| Slurm | `swe_scripts/apptainer_runtime.inc.sh` | **新增** |
| 网络 | `llm.py` | **改**，`VLLM_CONTAINER_HOST` |

---

## 附录：一次 SWE-bench 任务的完整调用链

```
swe_bench/harness.py::process_entry()
  └─ docker_from_env()                          # utils/docker_utils.py
       └─ container_from_env()                  # utils/container_runtime.py
            └─ ApptainerClient()                # utils/apptainer_compat.py
  └─ build_container_with_network()
       └─ client.images.get() / .pull()         # resolve .sif 或 apptainer pull
       └─ client.containers.create()           # 建 workspace + binds
  └─ container.put_archive("/hgm", ...)        # 写入 sandbox 或 bind 目录
  └─ container.exec_run("python ...")         # apptainer exec --writable rootfs/ ...
       实际命令 ≈:
       apptainer exec --writable \
         --bind /workspace/hgm:/hgm \
         --pwd /hgm \
         --env VLLM_HOST=10.x.x.x \
         /workspace/rootfs/ \
         python /hgm/coding_agent.py
```

---

*文档生成自 MendelGM 仓库源码，与 [`APPTAINER.md`](../APPTAINER.md) 互为补充。*
