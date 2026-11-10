"""Single-environment RoboDojo evaluation through the upstream control loop."""

import hashlib
import json
import logging
import os
import shutil
from importlib.util import find_spec
from pathlib import Path
from typing import Annotated, Literal
from urllib.parse import urlsplit

import yaml
from pydantic import Field

from openplatform.benchmark import BenchmarkFailure, EvalContext, PolicyEndpoint
from openplatform.benchmarks.robodojo import docker
from openplatform.benchmarks.robodojo.episode_index import read_events, write_episode_index
from openplatform.benchmarks.robodojo.preparation import CONTAINER_ROOT, PACKAGE, inspect
from openplatform.benchmarks.robodojo.verify import verify
from openplatform.results import Metric
from openplatform.runtime import ExecutionSpec, ExitResult
from openplatform.safety import summarize_safety
from openplatform.schemas import ConfigPath, Options, _Schema


class Parameters(_Schema):
    checkout: ConfigPath
    policy: Literal["demo_policy", "Pi_0"] = "demo_policy"
    timeout: Annotated[float, Field(gt=0, le=86400)] | None = None
    rpc_timeout: Annotated[float, Field(gt=0, le=900)] = 120
    smoke_control_steps: Annotated[int, Field(strict=True, gt=0, lt=800)] | None = None


class CondaParameters(Parameters):
    simulator_python: ConfigPath


def _run_id(ctx: EvalContext) -> str:
    return "op-v06-" + hashlib.sha256(os.fsencode(ctx.attempt_dir)).hexdigest()[:20]


def _policy_label(parameters: dict) -> str:
    return "demo" if parameters["policy"] == "demo_policy" else "RoboDojo-sim-arx_x5-joint-0"


def _episode_budget(ctx: EvalContext, root: Path) -> int:
    if ctx.preset != "full":
        return 1
    tasks = yaml.safe_load((root / "task/RoboDojo/config/_task.yml").read_text(encoding="utf-8"))
    return int(tasks["tasks"]["stack_bowls"]["eval_nums"])


class RoboDojoPlugin:
    recipe = "conda"
    parameter_schema: type[Parameters] = CondaParameters

    def resolve_parameters(self, parameters: Options) -> Options:
        config = self.parameter_schema.model_validate(parameters).model_dump(mode="json")
        for field in ("checkout", "simulator_python"):
            if field in config and not Path(config[field]).is_absolute():
                raise ValueError(f"RoboDojo {field} must be an absolute path")
        return config

    def build_eval(self, ctx: EvalContext, policy: PolicyEndpoint) -> ExecutionSpec:
        parameters = ctx.parameters
        smoke_steps = parameters.get("smoke_control_steps")
        if smoke_steps is not None and ctx.preset != "smoke":
            raise BenchmarkFailure("preflight", "smoke_control_steps requires the smoke preset")
        root = Path(parameters["checkout"]).resolve()
        is_docker = self.recipe == "docker"
        report = inspect(self.recipe, root)
        if not report["ready"]:
            raise BenchmarkFailure(
                "preflight", "RoboDojo prerequisites: " + ", ".join(report["issues"])
            )
        episodes = _episode_budget(ctx, root)
        if len(report["layouts"]) < episodes:
            raise BenchmarkFailure(
                "preflight", f"need at least {episodes} seed 0 stack_bowls layouts"
            )
        timeout = parameters["timeout"] or (14400 if ctx.preset == "full" else 2700)
        endpoint = urlsplit(policy.url)
        gpu = ctx.environment_gpu_ids[0] if ctx.environment_gpu_ids else 0
        eval_root = CONTAINER_ROOT if is_docker else root
        launch = [
            "bash",
            eval_root / "scripts/eval_policy.sh",
            "--root_dir",
            eval_root,
            "--task_name",
            "stack_bowls",
            "--env_cfg_type",
            "arx_x5",
            "--device_id",
            "0" if is_docker else str(gpu),
            "--policy_name",
            parameters["policy"],
            "--port",
            str(endpoint.port),
            "--host",
            endpoint.hostname,
            "--protocol",
            "ws",
            "--policy_server_url",
            policy.url,
            "--seed",
            "0",
            "--additional_info",
            f"ckpt_name={_policy_label(parameters)},action_type=joint",
            "--eval_batch",
            "false",
        ]
        env = {
            "PYTHONPATH": str(PACKAGE / "hooks"),
            "OPENPLATFORM_RD_CORE_DIR": str(PACKAGE.parents[1]),
            "OPENPLATFORM_RD_SDK_DIR": str(Path(find_spec("openplatform_sdk").origin).parent),
            "ROBODOJO_RUN_ID": _run_id(ctx),
            "EVAL_NUM": "native" if ctx.preset == "full" else "1",
            "EVAL_ENV_TYPE": "sim",
            "OPENPLATFORM_RD_MODE": "bridge",
            "OPENPLATFORM_RD_EVIDENCE": str(ctx.native_dir),
            "OPENPLATFORM_RD_PROFILE": policy.io_profile,
            "OPENPLATFORM_RD_RPC_TIMEOUT": str(parameters["rpc_timeout"]),
            "OPENPLATFORM_RD_SMOKE_CONTROL_STEPS": "" if smoke_steps is None else str(smoke_steps),
            **{
                f"OPENPLATFORM_RD_{key.upper()}": str(int(value))
                for key, value in ctx.collection.model_dump().items()
            },
        }
        image_id = None
        if is_docker:
            image_id = report["image_cache"]["id"]
            launch = docker.command(ctx, report, _run_id(ctx), gpu, launch, env)
            env = {}
        else:
            python = Path(parameters["simulator_python"])
            if not python.is_file() or not os.access(python, os.X_OK):
                raise BenchmarkFailure("preflight", f"simulator Python is not executable: {python}")
            env["PATH"] = os.pathsep.join((str(python.parent), os.environ.get("PATH", "")))
        (ctx.native_dir / "launch.json").write_text(
            json.dumps(
                {
                    "argv": list(map(str, launch)),
                    "env": env,
                    "cwd": str(root),
                    "requested_episodes": episodes,
                    **({"smoke_control_steps": smoke_steps} if smoke_steps is not None else {}),
                    "timeout_seconds": timeout,
                    **({"image_id": image_id} if is_docker else {}),
                },
                indent=2,
            )
            + "\n",
            encoding="utf-8",
        )
        return ExecutionSpec(
            argv=launch,
            cwd=root,
            env=env,
            stdout_path=ctx.attempt_dir / "logs/benchmark.stdout.log",
            stderr_path=ctx.attempt_dir / "logs/benchmark.stderr.log",
            timeout=timeout,
        )

    def collect(self, ctx: EvalContext, process: ExitResult) -> list[Metric]:
        p = ctx.parameters
        if self.recipe == "docker":
            results = docker.finish(ctx, _run_id(ctx))
        else:
            results = Path(p["checkout"]) / "eval_result"
        if ctx.collection.safety:
            try:
                summarize_safety(ctx.native_dir)
            except (OSError, ValueError, KeyError, TypeError) as error:
                logging.getLogger(__name__).warning("Safety aggregation failed: %s", error)
        original = (
            results
            / "RoboDojo/stack_bowls"
            / p["policy"]
            / "arx_x5"
            / f"0_ckpt_name={_policy_label(p)},action_type=joint"
            / _run_id(ctx)
        )
        for path in [original / "_result.json", *sorted(original.glob("episode_*.mp4"))]:
            if path.is_file():
                shutil.copy2(path, ctx.native_dir / path.name)
        resume = original.parent / f"_resume_{_run_id(ctx)}.json"
        if resume.is_file():
            shutil.copy2(resume, ctx.native_dir / "resume.json")

        events = read_events(ctx.native_dir / "events.jsonl")
        raw_path = ctx.native_dir / "_result.json"
        raw = json.loads(raw_path.read_text(encoding="utf-8")) if raw_path.is_file() else {}
        write_episode_index(ctx.native_dir, events, raw, process.status)
        errors = ctx.native_dir / "bridge-error.jsonl"
        if errors.is_file():
            error = json.loads(errors.read_text(encoding="utf-8").splitlines()[-1])
            raise BenchmarkFailure(error["kind"], error["message"])
        if process.status != "exited" or process.returncode != 0:
            raise BenchmarkFailure(
                "environment", f"RoboDojo process {process.status} ({process.returncode})"
            )
        if not any(e["kind"] == "policy_connected" for e in events) or not any(
            e["kind"] == "episode_result" for e in events
        ):
            raise BenchmarkFailure("collection", "missing handshake or matching episode trace")
        verified = verify(
            ctx.native_dir,
            videos=ctx.collection.videos,
            expected_episodes=_episode_budget(ctx, Path(p["checkout"])),
        )
        scored = [e for e in events if e["kind"] == "episode_result"]
        if (
            len(scored) != raw["eval_time"]
            or {str(e["native_result_index"]): e["result"] for e in scored} != raw["details"]
        ):
            raise BenchmarkFailure("collection", "native episodes disagree with episode trace")
        write_episode_index(ctx.native_dir, events, raw, process.status, verified)
        (ctx.native_dir / "verified.json").write_text(json.dumps(verified, indent=2) + "\n")
        return [
            Metric(
                name="success_rate",
                value=raw["success_rate"],
                unit="ratio",
                direction="higher",
                scope="eval",
            ),
            Metric(
                name="score", value=raw["score"], unit="percent", direction="higher", scope="eval"
            ),
            Metric(
                name="completed_episodes",
                value=raw["eval_time"],
                unit="count",
                direction="none",
                scope="eval",
            ),
        ]


class RoboDojoDockerPlugin(RoboDojoPlugin):
    recipe = "docker"
    parameter_schema = Parameters
