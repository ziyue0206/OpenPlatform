import json
import sys
from typing import Annotated, Literal

from pydantic import Field

from openplatform.benchmark import BenchmarkFailure, EvalContext, PolicyEndpoint
from openplatform.results import Metric
from openplatform.runtime import ExecutionSpec, ExitResult
from openplatform.schemas import Options, _Schema

EPISODES = (("hold", 0.0), ("move", 1.0))


class DummyParameters(_Schema):
    fault: Literal["none", "environment"] = "none"
    episode_delay: Annotated[float, Field(ge=0, le=30)] = 0.0


class DummyPlugin:
    def resolve_parameters(self, parameters: Options) -> Options:
        return DummyParameters.model_validate(parameters).model_dump(mode="json")

    def build_eval(self, ctx: EvalContext, policy: PolicyEndpoint) -> ExecutionSpec:
        return ExecutionSpec(
            argv=[
                sys.executable,
                "-m",
                "openplatform.benchmarks.dummy.worker",
                "--policy-url",
                policy.url,
                "--profile",
                policy.io_profile,
                "--output",
                ctx.native_dir,
                "--parameters",
                json.dumps(ctx.parameters),
            ],
            cwd=ctx.attempt_dir,
            stdout_path=ctx.attempt_dir / "logs/benchmark.stdout.log",
            stderr_path=ctx.attempt_dir / "logs/benchmark.stderr.log",
            timeout=75,
        )

    def collect(self, ctx: EvalContext, process: ExitResult) -> list[Metric]:
        if process.status != "exited" or process.returncode != 0:
            error = ctx.native_dir / "error.json"
            if error.is_file():
                detail = json.loads(error.read_text(encoding="utf-8"))
                raise BenchmarkFailure(detail["kind"], detail["message"])
            raise BenchmarkFailure(
                "environment", f"dummy process {process.status} ({process.returncode})"
            )

        raw = json.loads((ctx.native_dir / "result.json").read_text(encoding="utf-8"))
        episodes = raw["episodes"]
        if [row["id"] for row in episodes] != [name for name, _ in EPISODES] or any(
            type(row["success"]) is not bool for row in episodes
        ):
            raise BenchmarkFailure("collection", "dummy result is incomplete or invalid")
        rate = sum(row["success"] for row in episodes) / len(episodes)
        if raw.get("success_rate") != rate:
            raise BenchmarkFailure("collection", "dummy result has an inconsistent success rate")
        return [
            Metric(name="success_rate", value=rate, unit="ratio", direction="higher", scope="eval"),
            Metric(
                name="completed_episodes",
                value=len(episodes),
                unit="count",
                direction="none",
                scope="eval",
            ),
        ]
