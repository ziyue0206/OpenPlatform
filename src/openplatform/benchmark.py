from dataclasses import dataclass, field
from importlib import import_module
from pathlib import Path
from typing import Literal, Protocol

import yaml

from openplatform.results import Metric
from openplatform.runtime import ExecutionSpec, ExitResult
from openplatform.schemas import CollectionSpec, EvalId, Name, Options, _Schema


class Recipe(_Schema):
    version: Name
    io_profile: Name
    plugin: Name | None = None
    default_suite: Name
    suites: list[Name]
    presets: list[Name]
    eval_enabled: bool = True
    supports_safety: bool = False
    max_policy_gpus: Literal[0, 1] = 0
    max_environment_gpus: Literal[0, 1] = 0


class BenchmarkManifest(_Schema):
    schema_version: Literal["1"]
    id: EvalId
    version: Name
    status: Name
    plugin: Name | None = None
    preparation: Name | None = None
    default_recipe: Name
    recipes: dict[str, Recipe]


@dataclass(frozen=True)
class EvalContext:
    attempt_dir: Path
    parameters: Options
    suite: str | None
    preset: str | None
    environment_gpu_ids: tuple[int, ...] = ()
    collection: CollectionSpec = field(default_factory=CollectionSpec)

    @property
    def native_dir(self) -> Path:
        return self.attempt_dir / "native"


@dataclass(frozen=True)
class PolicyEndpoint:
    url: str
    io_profile: str


class BenchmarkFailure(RuntimeError):
    def __init__(self, kind: str, message: str):
        self.kind = kind
        super().__init__(message)


class BenchmarkPlugin(Protocol):
    def resolve_parameters(self, parameters: Options) -> Options: ...
    def build_eval(self, ctx: EvalContext, policy: PolicyEndpoint) -> ExecutionSpec: ...
    def collect(self, ctx: EvalContext, process: ExitResult) -> list[Metric]: ...


class Registry:
    """Discover benchmark manifests beside the installed package."""

    def __init__(self, directory: Path | None = None):
        root = directory if directory is not None else Path(__file__).parent / "benchmarks"
        self._manifests = {}
        for path in sorted(root.glob("*/benchmark.yaml")):
            manifest = BenchmarkManifest.model_validate(
                yaml.safe_load(path.read_text(encoding="utf-8"))
            )
            if manifest.id in self._manifests:
                raise ValueError(f"duplicate benchmark ID: {manifest.id}")
            if manifest.plugin is None and manifest.preparation is None:
                raise ValueError(f"missing benchmark entry point in {path}")
            if manifest.default_recipe not in manifest.recipes or any(
                recipe.default_suite not in recipe.suites for recipe in manifest.recipes.values()
            ):
                raise ValueError(f"invalid recipe defaults in {path}")
            self._manifests[manifest.id] = manifest

    def list(self) -> list[BenchmarkManifest]:
        return list(self._manifests.values())

    def describe(self, benchmark: str) -> BenchmarkManifest:
        try:
            return self._manifests[benchmark]
        except KeyError:
            raise ValueError(f"unknown benchmark {benchmark!r}") from None

    def load(self, manifest: BenchmarkManifest, recipe: str | None = None) -> BenchmarkPlugin:
        entry = (manifest.recipes[recipe].plugin if recipe is not None else None) or manifest.plugin
        if entry is None:
            raise ValueError(f"{manifest.id} supports preparation only; eval is unavailable")
        module, _, name = entry.partition(":")
        return getattr(import_module(module), name)()
