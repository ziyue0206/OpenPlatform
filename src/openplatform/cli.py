import json
import signal
from importlib import import_module
from pathlib import Path
from threading import Event
from typing import Annotated

import typer
from pydantic import ValidationError
from yaml import YAMLError

from openplatform.benchmark import Registry
from openplatform.runner import ExperimentRunner
from openplatform.schemas import EvalSpec, ExperimentSpec
from openplatform.storage import read_status

app = typer.Typer(help="Local tools for embodied policy evaluation.", add_completion=False)


@app.callback()
def main() -> None:
    """OpenPlatform command line interface."""


def _load_config(config: Path) -> ExperimentSpec:
    try:
        return ExperimentSpec.from_yaml(config)
    except ValidationError as error:
        typer.echo(f"Invalid configuration: {config}", err=True)
        for issue in error.errors(include_input=False):
            field = ".".join(map(str, issue["loc"])) or "configuration"
            typer.echo(f"  {field}: {issue['msg']}", err=True)
        raise typer.Exit(2) from None
    except (OSError, UnicodeError, YAMLError) as error:
        typer.echo(f"Cannot read configuration {config}: {error}", err=True)
        raise typer.Exit(2) from None


@app.command()
def validate(
    config: Annotated[Path, typer.Argument(help="Path to a submission or experiment YAML file.")],
    check_paths: Annotated[
        bool,
        typer.Option(help="Also check local model, adapter, interpreter and checkpoint paths."),
    ] = False,
) -> None:
    """Validate eval-only configuration structure."""
    spec = _load_config(config)
    typer.echo(f"Configuration structure is valid: {config}")
    if check_paths:
        try:
            issues = spec.check_paths()
        except OSError as error:
            issues = [str(error)]
        if issues:
            typer.echo("Local path check failed:", err=True)
            for issue in issues:
                typer.echo(f"  {issue}", err=True)
            raise typer.Exit(1)
        typer.echo("Local path check passed.")


@app.command("list")
def list_benchmarks() -> None:
    """List installed benchmark manifests and their support status."""
    typer.echo(
        json.dumps(
            [manifest.model_dump(mode="json", exclude_none=True) for manifest in Registry().list()],
            indent=2,
        )
    )


def _preparation(bench: str, recipe: str | None, checkout: Path, *, include_plan: bool) -> None:
    try:
        manifest = Registry().describe(bench)
        selected = recipe or manifest.default_recipe
        if selected not in manifest.recipes:
            raise ValueError(f"unknown recipe {selected!r} for {bench}")
        if manifest.preparation is None:
            raise ValueError(f"{bench} does not provide environment preparation")
        report = import_module(manifest.preparation).inspect(selected, checkout)
    except (OSError, ValueError) as error:
        typer.echo(f"Cannot inspect benchmark: {error}", err=True)
        raise typer.Exit(2) from None
    if not include_plan:
        del report["commands"]
    typer.echo(json.dumps(report, indent=2))
    if not include_plan and not report["ready"]:
        raise typer.Exit(1)


@app.command()
def doctor(
    bench: Annotated[str, typer.Option("--bench", help="Benchmark ID.")],
    checkout: Annotated[Path, typer.Option(help="Path to the upstream checkout.")],
    recipe: Annotated[str | None, typer.Option(help="Benchmark recipe.")] = None,
) -> None:
    """Check benchmark inputs and local runtime prerequisites."""
    _preparation(bench, recipe, checkout, include_plan=False)


@app.command()
def prepare(
    bench: Annotated[str, typer.Option("--bench", help="Benchmark ID.")],
    checkout: Annotated[Path, typer.Option(help="Path to the upstream checkout.")],
    recipe: Annotated[str | None, typer.Option(help="Benchmark recipe.")] = None,
) -> None:
    """Print a benchmark preparation plan and inspect local caches."""
    _preparation(bench, recipe, checkout, include_plan=True)


def _run_spec(runner: ExperimentRunner, spec: ExperimentSpec, output_dir: Path | None) -> None:
    try:
        plan = runner.plan(spec)
    except ValueError as error:
        typer.echo(f"Invalid experiment: {error}", err=True)
        raise typer.Exit(2) from None

    cancel = Event()
    previous = signal.signal(signal.SIGINT, lambda *_: cancel.set())
    try:
        result = runner.run(plan, output_dir=output_dir, cancel_event=cancel)
    except (OSError, ValueError, RuntimeError) as error:
        typer.echo(f"Run failed: {error}", err=True)
        raise typer.Exit(1) from None
    finally:
        signal.signal(signal.SIGINT, previous)

    typer.echo(result.model_dump_json(indent=2))
    if result.status != "succeeded":
        raise typer.Exit(130 if result.status == "canceled" else 1)


@app.command()
def eval(
    model_dir: Annotated[Path, typer.Argument(help="Directory containing submission.yaml.")],
    bench: Annotated[str, typer.Option("--bench", help="Benchmark ID.")],
    recipe: Annotated[str | None, typer.Option(help="Benchmark recipe.")] = None,
    suite: Annotated[str | None, typer.Option(help="Benchmark suite.")] = None,
    preset: Annotated[str | None, typer.Option(help="Benchmark preset.")] = None,
    output_dir: Annotated[Path | None, typer.Option(help="Root directory for local runs.")] = None,
) -> None:
    """Run one eval from a model submission."""
    spec = _load_config(model_dir / "submission.yaml")
    runner = ExperimentRunner()
    try:
        manifest = runner.registry.describe(bench)
        declared = [e for e in spec.evals if e.benchmark == bench]
        selected = recipe or (declared[0].recipe if len(declared) == 1 else manifest.default_recipe)
        matches = [e for e in declared if e.recipe == selected]
        if len(matches) > 1:
            raise ValueError("multiple matching evals in submission; use openplatform run")
        fields = matches[0].model_dump(exclude_unset=True) if matches else {}
        fields.update(id=bench, benchmark=bench, recipe=selected)
        if suite is not None or preset is not None:
            fields.update(suite=suite, preset=preset)
        item = EvalSpec.model_validate(fields)
    except ValueError as error:
        typer.echo(f"Invalid eval selection: {error}", err=True)
        raise typer.Exit(2) from None
    _run_spec(runner, spec.model_copy(update={"evals": [item]}), output_dir)


@app.command()
def run(
    config: Annotated[Path, typer.Argument(help="Experiment YAML file.")],
    output_dir: Annotated[Path | None, typer.Option(help="Root directory for local runs.")] = None,
) -> None:
    """Run evals in experiment order."""
    _run_spec(ExperimentRunner(), _load_config(config), output_dir)


@app.command()
def status(run_dir: Annotated[Path, typer.Argument(help="Existing run directory.")]) -> None:
    """Read the status and artifact references for an existing run."""
    try:
        snapshot = read_status(run_dir)
    except (OSError, ValueError, KeyError) as error:
        typer.echo(f"Cannot read run {run_dir}: {error}", err=True)
        raise typer.Exit(1) from None
    typer.echo(json.dumps(snapshot, indent=2, ensure_ascii=False))
