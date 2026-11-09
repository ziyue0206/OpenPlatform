import json
import re
import time
from pathlib import Path
from threading import Event

from openplatform.benchmark import BenchmarkFailure, EvalContext, PolicyEndpoint, Registry
from openplatform.resolver import ResolvedEval, ResolvedExperiment, resolve
from openplatform.results import EvalError, EvalResult, ExperimentResult
from openplatform.runtime import ExecutionSpec, ExitResult, NativeRuntime, ProcessHandle
from openplatform.schemas import ExperimentSpec
from openplatform.storage import RunStore, read_status

_READY = re.compile(rb"OPENPLATFORM_POLICY_READY (ws://127\.0\.0\.1:([1-9][0-9]{0,4}))\r?\n")


class _Canceled(Exception):
    pass


def _tick(cancel: Event) -> None:
    if cancel.wait(0.02):
        raise _Canceled


def _policy_url(handle: ProcessHandle, cancel: Event, timeout: float) -> str:
    deadline = time.monotonic() + timeout
    with handle.stdout_path.open("rb") as output:
        while True:
            if cancel.is_set():
                raise _Canceled
            position = output.tell()
            line = output.readline()
            match = _READY.search(line)
            if match is not None and int(match[2]) <= 65535:
                return match[1].decode("ascii")
            if time.monotonic() >= deadline:
                raise BenchmarkFailure("policy", "policy readiness timed out; see policy logs")
            if line.endswith(b"\n"):
                continue
            output.seek(position)
            if handle.done():
                raise BenchmarkFailure(
                    "policy", "policy process exited before readiness; see policy logs"
                )
            _tick(cancel)


def _wait(
    runtime: NativeRuntime, handle: ProcessHandle, cancel: Event, *, timeout: float | None = None
) -> ExitResult:
    deadline = time.monotonic() + timeout if timeout is not None else float("inf")
    while not handle.done():
        _tick(cancel)
        if time.monotonic() >= deadline:
            raise BenchmarkFailure(
                "policy", "policy did not exit after the benchmark closed its session"
            )
    return runtime.wait(handle)


class ExperimentRunner:
    """Resolve and run local evals in declaration order."""

    def __init__(self, *, registry: Registry | None = None, runtime: NativeRuntime | None = None):
        self.registry = registry if registry is not None else Registry()
        self.runtime = runtime if runtime is not None else NativeRuntime()

    def plan(self, spec: ExperimentSpec) -> ResolvedExperiment:
        return resolve(spec, self.registry)

    def run(
        self,
        plan: ResolvedExperiment,
        output_dir: str | Path | None = None,
        *,
        cancel_event: Event | None = None,
        continue_on_error: bool = True,
    ) -> ExperimentResult:
        cancel = cancel_event if cancel_event is not None else Event()
        root = (
            Path(output_dir).resolve()
            if output_dir is not None
            else plan.spec.output.root.resolve()
        )
        config = {**plan.config, "output": {"root": str(root)}}
        results = []
        stopped_after_error = False
        with RunStore.create(
            root,
            input_config=plan.input_yaml,
            resolved_config=config,
            eval_ids=[item.spec.id for item in plan.evals],
        ) as run:
            for item in plan.evals:
                if cancel.is_set() or stopped_after_error:
                    results.append(run.skip_eval(item.spec.id, canceled=cancel.is_set()))
                    continue
                attempt = run.start_eval(item.spec.id)
                ctx = EvalContext(
                    attempt,
                    item.parameters,
                    item.suite,
                    item.preset,
                    item.environment_gpu_ids,
                    plan.spec.collection,
                )
                ctx.native_dir.mkdir()
                result, processes = self._execute(plan.spec, item, ctx, run, cancel)
                for path in sorted(ctx.native_dir.rglob("*")):
                    relative = path.relative_to(ctx.native_dir)
                    if path.is_file() and not any(part.startswith(".") for part in relative.parts):
                        run.artifacts.register(
                            path.relative_to(run.run_dir).as_posix(), kind="native"
                        )
                metrics_path = None
                if result.status == "succeeded":
                    metrics_path = (attempt / "metrics.json").relative_to(run.run_dir).as_posix()
                    data = json.dumps(
                        {"metrics": [metric.model_dump(mode="json") for metric in result.metrics]},
                        indent=2,
                        allow_nan=False,
                    )
                    run.artifacts.put(metrics_path, (data + "\n").encode("utf-8"), kind="metrics")
                run.record_eval(result, processes=processes, metrics_path=metrics_path)
                results.append(result)
                stopped_after_error = result.status == "failed" and not continue_on_error

        return ExperimentResult(
            run_dir=run.run_dir, evals=results, status=read_status(run.run_dir)["status"]
        )

    def _execute(
        self,
        spec: ExperimentSpec,
        item: ResolvedEval,
        ctx: EvalContext,
        run: RunStore,
        cancel: Event,
    ) -> tuple[EvalResult, dict[str, ExitResult]]:
        result = EvalResult(eval_id=item.spec.id, status="succeeded")
        handles = {}
        processes = {}
        collected = False
        phase = "preflight"
        try:
            _tick(cancel)
            issues = spec.check_paths(eval_spec=item.spec)
            if issues:
                raise BenchmarkFailure(phase, "; ".join(issues))
            python = self.runtime.prepare(spec.model.runtime).python
            config = {
                **item.adapter_config,
                "checkpoint_path": str(item.checkpoint) if item.checkpoint is not None else None,
                "model_dir": str(spec.model.source.path),
                "adapter_dir": str(spec.adapter.source.path),
                "device": "cuda:0" if item.policy_gpu_ids else "cpu",
                "io_profile": item.io_profile,
                "evidence_dir": str(ctx.native_dir),
                "collection": ctx.collection.model_dump(),
            }
            config_path = ctx.attempt_dir / "policy_config.json"
            run.artifacts.put(
                config_path.relative_to(run.run_dir).as_posix(),
                json.dumps(config, allow_nan=False).encode("utf-8"),
                kind="config",
            )

            phase = "policy"
            handles["policy"] = self.runtime.launch(
                ExecutionSpec(
                    argv=[
                        python,
                        "-m",
                        "openplatform_sdk.server",
                        "--entrypoint",
                        spec.adapter.entrypoint,
                        "--config",
                        config_path,
                    ],
                    cwd=spec.model.source.path,
                    env=(
                        {"CUDA_VISIBLE_DEVICES": str(item.policy_gpu_ids[0])}
                        if item.policy_gpu_ids
                        else {}
                    ),
                    stdout_path=ctx.attempt_dir / "logs/policy.stdout.log",
                    stderr_path=ctx.attempt_dir / "logs/policy.stderr.log",
                )
            )
            endpoint = PolicyEndpoint(
                _policy_url(handles["policy"], cancel, spec.model.runtime.startup_timeout),
                item.io_profile,
            )

            phase = "environment"
            execution = item.plugin.build_eval(ctx, endpoint)
            if any(
                not path.resolve().is_relative_to(ctx.attempt_dir / "logs")
                for path in (execution.stdout_path, execution.stderr_path)
            ):
                raise ValueError("benchmark logs must be inside the attempt's logs directory")
            _tick(cancel)
            handles["benchmark"] = self.runtime.launch(execution)
            native = processes["benchmark"] = _wait(self.runtime, handles["benchmark"], cancel)

            phase = (
                "collection"
                if native.status == "exited" and native.returncode == 0
                else "environment"
            )
            collected = True
            result.metrics = item.plugin.collect(ctx, native)
            if phase == "environment":
                raise BenchmarkFailure(
                    phase, f"benchmark process {native.status} ({native.returncode})"
                )
            phase = "policy"
            policy = processes["policy"] = _wait(self.runtime, handles["policy"], cancel, timeout=3)
            if policy.status != "exited" or policy.returncode != 0:
                raise BenchmarkFailure(
                    phase, f"policy process {policy.status} ({policy.returncode})"
                )
        except (_Canceled, KeyboardInterrupt):
            cancel.set()
            result = EvalResult(eval_id=item.spec.id, status="canceled")
        except Exception as error:
            kind = error.kind if isinstance(error, BenchmarkFailure) else phase
            result = EvalResult(
                eval_id=item.spec.id,
                status="failed",
                error=EvalError(kind=kind, message=str(error)),
            )
        finally:
            for name, handle in reversed(handles.items()):
                if name not in processes:
                    try:
                        processes[name] = self.runtime.stop(handle)
                    except Exception as error:
                        message = f"{name} cleanup failed: {error}"
                        if result.error is not None:
                            message = f"{result.error.message}; {message}"
                        result = EvalResult(
                            eval_id=item.spec.id,
                            status="canceled" if cancel.is_set() else "failed",
                            error=EvalError(kind="cleanup", message=message),
                        )
            if not collected and "benchmark" in processes:
                try:
                    item.plugin.collect(ctx, processes["benchmark"])
                except Exception as error:
                    (ctx.native_dir / "collection-error.txt").write_text(
                        str(error) + "\n", encoding="utf-8"
                    )
        return result, processes
