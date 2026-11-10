"""Launch the pinned upstream image and collect only this attempt's files."""

import json
import os
import subprocess
from pathlib import Path

from openplatform.benchmark import BenchmarkFailure, EvalContext
from openplatform.benchmarks.robodojo.preparation import CONTAINER_ROOT

_LABEL = "io.openplatform.robodojo.run"
_EVIDENCE = Path("/run/openplatform/evidence")
_RESULTS = CONTAINER_ROOT / "eval_result"


def _mount(source: Path, target: Path, *, readonly: bool = False) -> list[str]:
    if ":" in str(source):
        raise BenchmarkFailure("preflight", f"Docker volume path cannot contain a colon: {source}")
    return ["--volume", f"{source}:{target}" + (":ro" if readonly else "")]


def command(ctx: EvalContext, report: dict, run_id: str, gpu: int, launch: list, env: dict) -> list:
    if os.environ.get("OMNI_KIT_ACCEPT_EULA") != "YES":
        raise BenchmarkFailure(
            "preflight", "accept the Isaac Sim license and set OMNI_KIT_ACCEPT_EULA=YES"
        )
    results = ctx.attempt_dir / ".robodojo-results"
    core = Path(env["OPENPLATFORM_RD_CORE_DIR"])
    sdk = Path(env["OPENPLATFORM_RD_SDK_DIR"])
    container_packages = Path("/opt/openplatform")
    mounts = [
        *report["docker_volume_args"],
        *_mount(core, container_packages / "openplatform", readonly=True),
        *_mount(sdk, container_packages / "openplatform_sdk", readonly=True),
        *_mount(ctx.native_dir, _EVIDENCE),
        *_mount(results, _RESULTS),
    ]
    results.mkdir()
    container_env = {
        **env,
        "PYTHONPATH": str(container_packages / "openplatform/benchmarks/robodojo/hooks"),
        "OPENPLATFORM_RD_CORE_DIR": str(container_packages / "openplatform"),
        "OPENPLATFORM_RD_SDK_DIR": str(container_packages / "openplatform_sdk"),
        "OPENPLATFORM_RD_EVIDENCE": str(_EVIDENCE),
        "OMNI_KIT_ACCEPT_EULA": "YES",
        "NVIDIA_DRIVER_CAPABILITIES": "all",
        "PYTHONDONTWRITEBYTECODE": "1",
    }
    argv = [
        "docker",
        "run",
        "--rm",
        "--init",
        "--pull=never",
        "--name",
        f"openplatform-{run_id}",
        "--label",
        f"{_LABEL}={run_id}",
        "--network=host",
        "--ipc=host",
        "--gpus",
        f"device={gpu}",
        "--volume",
        "openplatform-robodojo-f57e163-cache:/root/.cache",
        "--volume",
        "openplatform-robodojo-f57e163-ov:/root/.local/share/ov",
        *mounts,
    ]
    for key, value in container_env.items():
        argv.extend(("--env", f"{key}={value}"))
    return [*argv, report["image_cache"]["id"], *launch]


def finish(ctx: EvalContext, run_id: str) -> Path:
    """Stop any daemon-owned simulator and return its host output directory."""
    results = ctx.attempt_dir / ".robodojo-results"
    launch = json.loads((ctx.native_dir / "launch.json").read_text(encoding="utf-8"))
    try:
        running = subprocess.run(
            ["docker", "container", "ls", "-aq", "--filter", f"label={_LABEL}={run_id}"],
            check=True,
            capture_output=True,
            text=True,
            timeout=15,
        ).stdout.split()
        for container in running:
            # The daemon may auto-remove a container after it appears in this listing.
            removed = subprocess.run(
                ["docker", "container", "rm", "--force", container],
                capture_output=True,
                text=True,
                timeout=20,
            )
            if removed.returncode and "No such container:" not in removed.stderr:
                removed.check_returncode()
        subprocess.run(
            [
                "docker",
                "run",
                "--rm",
                "--pull=never",
                "--entrypoint",
                "chown",
                *_mount(ctx.native_dir, _EVIDENCE),
                *_mount(results, _RESULTS),
                launch["image_id"],
                "-hR",
                f"{os.getuid()}:{os.getgid()}",
                _EVIDENCE,
                _RESULTS,
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=30,
        )
    except (OSError, subprocess.SubprocessError) as error:
        detail = getattr(error, "stderr", None) or str(error)
        raise BenchmarkFailure("cleanup", f"Docker cleanup failed: {detail.strip()}") from error
    return results
