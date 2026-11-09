"""Inspect local inputs without importing the simulator or modifying caches."""

import json
import os
import platform
import shutil
import subprocess
from pathlib import Path

from openplatform.benchmarks.robodojo.verify import DOCKER_RECEIPT

PACKAGE = Path(__file__).parent
CONTAINER_ROOT = Path("/workspace/RoboDojo")
ASSET_MARKER = ".openplatform-assets-revision"


def _probe(*argv: str, cwd: Path | None = None) -> str | None:
    try:
        result = subprocess.run(
            argv,
            cwd=cwd,
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=10,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    return result.stdout.rstrip()


def inspect(recipe: str, checkout: Path) -> dict:
    pins = json.loads((PACKAGE / "versions.json").read_text(encoding="utf-8"))
    root = checkout.resolve()
    assets, results = root / "Assets", root / "eval_result"
    generated = assets / "Robots/x5/curobo.yml"
    layouts = sorted(
        path
        for path in (assets / "Eval_Layout/RoboDojo/arx_x5/0").glob("stack_bowls_*.json")
        if path.stem.rsplit("_", 1)[-1].isdigit() and path.is_file() and path.stat().st_size
    )
    image = pins["image"]
    issues = []

    def require(condition: bool, issue: str) -> None:
        if not condition:
            issues.append(issue)

    require(
        platform.system() == "Linux" and platform.machine() == "x86_64",
        "RoboDojo smoke requires a Linux x86_64 RTX host",
    )
    for tool in ("git", "bash", "python3", "conda", "ffprobe", "nvidia-smi"):
        require(shutil.which(tool) is not None, f"missing executable: {tool}")
    gpu = _probe("nvidia-smi", "-L")
    require(bool(gpu), "NVIDIA driver probe did not report a GPU")
    if recipe == "conda":
        vulkan = _probe("vulkaninfo", "--summary")
        require(bool(vulkan and "NVIDIA" in vulkan), "Vulkan did not report an NVIDIA device")
        libraries = _probe("ldconfig", "-p") or ""
        for library in ("libGLU.so.1", "libXt.so.6"):
            require(f"{library} " in libraries, f"missing rendering library: {library}")
    envs = _probe("conda", "env", "list", "--json")
    conda_names = {Path(path).name for path in json.loads(envs)["envs"]} if envs else set()
    for env in ("RoboDojo", "demo-policy") if recipe == "conda" else ("demo-policy",):
        require(env in conda_names, f"missing Conda environment: {env}")

    actual_revision = _probe("git", "rev-parse", "HEAD", cwd=root)
    require(
        actual_revision == pins["robodojo"]["revision"], "checkout is missing or at wrong revision"
    )
    submodule_status = _probe("git", "submodule", "status", cwd=root)
    present = set()
    for line in (submodule_status or "").splitlines():
        fields = line[1:].split()
        if line.startswith(" ") and len(fields) >= 2:
            present.add((fields[1], fields[0]))
    for name, revision in pins["robodojo"]["submodules"].items():
        require((name, revision) in present, f"submodule is missing or at wrong revision: {name}")
    marker = root / ASSET_MARKER
    actual_assets = marker.read_text(encoding="utf-8").strip() if marker.is_file() else None
    require(
        actual_assets == pins["assets"]["revision"], f"missing or wrong asset revision: {marker}"
    )
    require(bool(layouts), f"no nonempty seed 0 stack_bowls layouts under {assets}")
    require(
        generated.is_file() and f"{assets}/" in generated.read_text(encoding="utf-8"),
        f"missing or stale generated paths: {generated}",
    )
    require(
        (results.is_dir() and os.access(results, os.W_OK))
        or (not results.exists() and root.is_dir() and os.access(root, os.W_OK)),
        f"result directory is not writable or creatable: {results}",
    )

    mounts = []
    image_cache = None
    if recipe == "docker":
        for path in (assets, results):
            require(":" not in str(path), f"Docker volume path cannot contain a colon: {path}")
        mounts = ["-v", f"{assets}:{CONTAINER_ROOT}/Assets:ro"]
        if assets != CONTAINER_ROOT / "Assets":
            mounts.extend(["-v", f"{assets}:{assets}:ro"])
        mounts.extend(["-v", f"{results}:{CONTAINER_ROOT}/eval_result"])
        docker_info = _probe("docker", "info", "--format", "{{json .Runtimes}}")
        if not docker_info and shutil.which("sg") and shutil.which("docker"):
            docker_info = _probe("sg", "docker", "-c", "docker info --format '{{json .Runtimes}}'")
        require(bool(docker_info), "Docker daemon unavailable")
        image_cache = {"tag": image, "status": "unchecked"}
        if docker_info:
            require("nvidia" in json.loads(docker_info), "Docker NVIDIA runtime unavailable")
            image_id = _probe("docker", "image", "inspect", "--format", "{{.Id}}", image)
            if not image_id and shutil.which("sg"):
                image_id = _probe(
                    "sg", "docker", "-c", f"docker image inspect --format '{{{{.Id}}}}' {image}"
                )
            image_cache.update(status="present_unverified" if image_id else "missing", id=image_id)
            receipt_path = root / DOCKER_RECEIPT
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, ValueError):
                receipt = {}
            if (
                image_id
                and isinstance(receipt, dict)
                and receipt.get("image_id") == image_id
                and receipt.get("robodojo_revision") == pins["robodojo"]["revision"]
                and receipt.get("asset_revision") == pins["assets"]["revision"]
                and receipt.get("effective_episodes") == 1
                and type(receipt.get("layout_id")) is int
                and receipt["layout_id"] >= 0
            ):
                image_cache.update(status="present_verified", receipt=str(receipt_path))

    script = str(PACKAGE / "recipe.sh")
    steps = ["source", "install" if recipe == "conda" else "policy", "assets"]
    commands = [{"argv": ["bash", script, step, str(root)]} for step in steps]
    smoke_argv = ["bash", script, f"{recipe}-smoke", str(root), "<new-absolute-evidence-dir>"]
    smoke = {"argv": smoke_argv}
    if image_cache is not None:
        if image_cache["status"] == "present_verified":
            smoke["env"] = {
                "ROBODOJO_SKIP_BUILD": "1",
                "ROBODOJO_EXPECTED_IMAGE_ID": image_cache["id"],
            }
        else:
            smoke["env"] = {"ROBODOJO_SKIP_BUILD": "0"}
            smoke["cached_wheel_alternative"] = [
                {
                    "argv": [
                        "bash",
                        str(PACKAGE / "build-cached-torch.sh"),
                        str(root),
                        image,
                        "<official-torch-wheel>",
                        "<new-absolute-build-evidence-dir>",
                    ]
                },
                {
                    "argv": smoke_argv,
                    "env": {
                        "ROBODOJO_SKIP_BUILD": "1",
                        "ROBODOJO_EXPECTED_IMAGE_ID": "<id-from-build-image-json>",
                    },
                },
            ]
    commands.append(smoke)
    return {
        "benchmark": "robodojo",
        "recipe": recipe,
        "ready": not issues,
        "issues": issues,
        "versions": pins,
        "checkout": str(root),
        "checkout_revision": actual_revision,
        "asset_revision_marker": actual_assets,
        "assets": str(assets),
        "layouts": [str(path) for path in layouts],
        "results": str(results),
        "docker_volume_args": mounts,
        "image_cache": image_cache,
        "commands": commands,
    }
