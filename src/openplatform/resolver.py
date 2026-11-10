from dataclasses import dataclass
from pathlib import Path

import yaml
from pydantic import ValidationError

from openplatform.benchmark import BenchmarkPlugin, Registry
from openplatform.schemas import EvalSpec, ExperimentSpec, Options


@dataclass(frozen=True)
class ResolvedEval:
    spec: EvalSpec
    plugin: BenchmarkPlugin
    io_profile: str
    suite: str | None
    preset: str | None
    checkpoint: Path | None
    parameters: Options
    adapter_config: Options
    policy_gpu_ids: tuple[int, ...]
    environment_gpu_ids: tuple[int, ...]


@dataclass(frozen=True)
class ResolvedExperiment:
    spec: ExperimentSpec
    input_yaml: bytes
    config: dict
    evals: tuple[ResolvedEval, ...]


def resolve(spec: ExperimentSpec, registry: Registry) -> ResolvedExperiment:
    if not spec.evals:
        raise ValueError("an experiment requires at least one eval")

    spec = spec.model_copy(deep=True)
    spec.model.source.path = spec.model.source.path.absolute()
    spec.model.runtime.python = spec.model.runtime.python.absolute()
    spec.adapter.source.path = spec.adapter.source.path.absolute()
    spec.output.root = spec.output.root.absolute()
    config = spec.model_dump(mode="json", exclude_unset=True)
    config["evals"] = [item.model_dump(mode="json", exclude_unset=True) for item in spec.evals]
    original = spec.input_yaml or yaml.safe_dump(config, allow_unicode=True).encode("utf-8")
    config["output"] = {"root": str(spec.output.root)}
    config["collection"] = spec.collection.model_dump()
    planned = []
    for item, snapshot in zip(spec.evals, config["evals"], strict=True):
        manifest = registry.describe(item.benchmark)
        recipe = manifest.recipes.get(item.recipe)
        if recipe is None:
            raise ValueError(f"{item.id}: unknown recipe {item.recipe!r} for {manifest.id}")
        if not recipe.eval_enabled:
            raise ValueError(f"{item.id}: {item.recipe} supports preparation only")
        if spec.collection.safety and not recipe.supports_safety:
            raise ValueError(f"{item.id}: collection.safety is unavailable for {item.recipe}")
        devices = {}
        for role in ("policy", "environment"):
            field = f"{role}_gpu_ids"
            ids = getattr(item.resources, field, None)
            if ids is None:
                ids = getattr(spec.resources, field, None)
            if len(ids or ()) > getattr(recipe, f"max_{role}_gpus"):
                raise ValueError(f"{item.id}: unsupported GPU allocation ({field})")
            devices[field] = tuple(ids or ())
        suite = None if item.preset is not None else item.suite or recipe.default_suite
        if suite is not None and suite not in recipe.suites:
            raise ValueError(f"{item.id}: unknown suite {suite!r} for {item.recipe}")
        if item.preset is not None and item.preset not in recipe.presets:
            raise ValueError(f"{item.id}: unknown preset {item.preset!r} for {item.recipe}")

        plugin = registry.load(manifest, item.recipe)
        try:
            parameters = plugin.resolve_parameters(item.parameters)
        except ValidationError as error:
            details = "; ".join(
                f"{'.'.join(map(str, issue['loc']))}: {issue['msg']}"
                for issue in error.errors(include_input=False)
            )
            raise ValueError(f"{item.id}: invalid benchmark parameters: {details}") from error
        checkpoint = spec.checkpoint_for(item)
        adapter_config = {**spec.adapter.config, **item.adapter_config}
        snapshot.update(
            benchmark_version=manifest.version,
            recipe_version=recipe.version,
            suite=suite,
            preset=item.preset,
            io_profile=recipe.io_profile,
            checkpoint=str(checkpoint) if checkpoint is not None else None,
            parameters=parameters,
            adapter_config=adapter_config,
            resources={field: list(ids) for field, ids in devices.items()},
        )
        planned.append(
            ResolvedEval(
                item,
                plugin,
                recipe.io_profile,
                suite,
                item.preset,
                checkpoint,
                parameters,
                adapter_config,
                **devices,
            )
        )
    return ResolvedExperiment(spec, original, config, tuple(planned))
