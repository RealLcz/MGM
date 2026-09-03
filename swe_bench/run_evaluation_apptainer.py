"""SWE-bench grading via Apptainer (docker-py shim instead of Docker daemon).

Patches ``docker.from_env`` before swebench harness code runs so
``swebench.harness.run_evaluation`` uses ApptainerClient.
"""

from __future__ import annotations

import docker as _docker_mod
import shlex

from utils.docker_utils import docker_from_env

_docker_mod.from_env = lambda timeout=None: docker_from_env(
    timeout=timeout if timeout is not None else 7200
)


def _patch_swebench_for_apptainer() -> None:
    """Pre-pulled Epoch instance SIFs do not need local env/base image builds."""
    import swebench.harness.constants as harness_constants
    import swebench.harness.docker_build as docker_build
    import swebench.harness.docker_utils as docker_utils
    import swe_bench.harness as mendel_swe_harness

    # Host /tmp is bind-mounted into Apptainer and is not the sandbox rootfs;
    # grading copies DOCKER_PATCH there and patch/git apply cannot see the file.
    harness_constants.DOCKER_PATCH = "/root/patch.diff"

    def _skip_build_env_images(client, dataset, *args, **kwargs):
        print(
            "Apptainer: skipping build_env_images (using pre-pulled instance images)"
        )
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
        import time

        if isinstance(cmd, list):
            cmd_str = " ".join(shlex.quote(str(c)) for c in cmd)
        else:
            cmd_str = str(cmd)
        if timeout is not None and timeout > 0:
            cmd_str = f"timeout {int(timeout)} {cmd_str}"
        start = time.time()
        result = container.exec_run(cmd_str, workdir="/")
        output = result.output if isinstance(result.output, bytes) else b""
        timed_out = result.exit_code == 124
        return output.decode("utf-8", errors="replace"), timed_out, time.time() - start

    docker_build.build_env_images = _skip_build_env_images
    docker_build.build_base_images = _skip_build_base_images
    docker_build.build_container = mendel_swe_harness.build_container_with_network
    docker_build.remove_image = _preserve_instance_remove_image
    docker_utils.exec_run_with_timeout = _apptainer_exec_run_with_timeout


_patch_swebench_for_apptainer()

if __name__ == "__main__":
    import runpy

    runpy.run_module("swebench.harness.run_evaluation", run_name="__main__")
