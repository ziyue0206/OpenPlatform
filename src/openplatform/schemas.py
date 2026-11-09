import os
from pathlib import Path
from typing import Annotated, Literal

import yaml
from pydantic import (
    AfterValidator,
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    JsonValue,
    PrivateAttr,
    StringConstraints,
    field_validator,
    model_validator,
)
from yaml.constructor import ConstructorError
from yaml.nodes import MappingNode

Name = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
EvalId = Annotated[Name, Field(pattern=r"^[a-zA-Z0-9][a-zA-Z0-9_.-]*$")]
NonNegativeInt = Annotated[int, Field(ge=0)]
Options = dict[str, JsonValue]


def _as_path(value: object) -> Path:
    if isinstance(value, Path):
        return value
    if not isinstance(value, str) or not value.strip() or "\0" in value:
        raise ValueError("expected a non-empty path string")
    return Path(value)


def _relative_checkpoint(path: Path) -> Path:
    if path.anchor:
        raise ValueError("checkpoint must be relative to model.source.path")
    return path


ConfigPath = Annotated[Path, BeforeValidator(_as_path)]
CheckpointPath = Annotated[ConfigPath, AfterValidator(_relative_checkpoint)]


class _ConfigLoader(yaml.SafeLoader):
    def construct_mapping(self, node: MappingNode, deep: bool = False) -> dict:
        self.flatten_mapping(node)
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            if not isinstance(key, str) or key in mapping:
                reason = (
                    "expected a string key"
                    if not isinstance(key, str)
                    else f"duplicate key {key!r}"
                )
                raise ConstructorError(
                    "while reading a mapping", node.start_mark, reason, key_node.start_mark
                )
            mapping[key] = self.construct_object(value_node, deep=deep)
        return mapping


class _Schema(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, allow_inf_nan=False)


class LocalSource(_Schema):
    kind: Literal["local"]
    path: ConfigPath


class RuntimeSpec(_Schema):
    kind: Literal["native"]
    python: ConfigPath
    startup_timeout: Annotated[float, Field(gt=0, le=3600)] = 15


class ModelSpec(_Schema):
    source: LocalSource
    checkpoint: CheckpointPath | None
    runtime: RuntimeSpec


class AdapterSpec(_Schema):
    source: LocalSource
    entrypoint: Name
    config: Options = Field(default_factory=dict)

    @field_validator("entrypoint")
    @classmethod
    def check_entrypoint(cls, value: str) -> str:
        module, _, class_name = value.partition(":")
        if not class_name.isidentifier() or not all(
            part.isidentifier() for part in module.split(".")
        ):
            raise ValueError("expected an adapter entrypoint in module:Class form")
        return value


class CollectionSpec(_Schema):
    videos: bool = True
    trace: bool = True
    policy_calls: bool = True
    observations: bool = False


class ResourcesSpec(_Schema):
    policy_gpu_ids: list[NonNegativeInt] | None = None
    environment_gpu_ids: list[NonNegativeInt] | None = None


class OutputSpec(_Schema):
    root: ConfigPath = Path("runs")


class TrainingInfo(_Schema):
    regime: Literal["unknown", "no_target_finetuning", "target_finetuning", "from_scratch"] = (
        "unknown"
    )
    datasets: list[Name] = Field(default_factory=list)
    steps: NonNegativeInt | None = None
    seed: NonNegativeInt | None = None
    data_source: str | None = None
    notes: str | None = None


class EvalSpec(_Schema):
    id: EvalId
    benchmark: Name
    recipe: Name
    suite: Name | None = None
    preset: Name | None = None
    checkpoint: CheckpointPath | None = None
    adapter_config: Options = Field(default_factory=dict)
    parameters: Options = Field(default_factory=dict)
    resources: ResourcesSpec | None = None
    training_info: TrainingInfo | None = None

    @model_validator(mode="before")
    @classmethod
    def reject_training(cls, value: object) -> object:
        if isinstance(value, dict) and "training" in value:
            raise ValueError(
                "training execution is unsupported in V01; use training_info for metadata"
            )
        return value

    @model_validator(mode="after")
    def check_selection(self) -> "EvalSpec":
        if self.suite is not None and self.preset is not None:
            raise ValueError("suite and preset are mutually exclusive")
        return self


class ExperimentSpec(_Schema):
    schema_version: Literal["1"]
    name: Name
    model: ModelSpec
    adapter: AdapterSpec
    evals: list[EvalSpec] = Field(default_factory=list, min_length=1)
    collection: CollectionSpec = Field(default_factory=CollectionSpec)
    resources: ResourcesSpec | None = None
    output: OutputSpec = Field(default_factory=OutputSpec)
    training_info: TrainingInfo = Field(default_factory=TrainingInfo)
    _input_yaml: bytes | None = PrivateAttr(default=None)

    @property
    def input_yaml(self) -> bytes | None:
        return self._input_yaml

    @field_validator("evals")
    @classmethod
    def check_unique_ids(cls, evals: list[EvalSpec]) -> list[EvalSpec]:
        seen = set()
        for index, item in enumerate(evals):
            if item.id in seen:
                raise ValueError(f"duplicate eval id {item.id!r} at evals[{index}].id")
            seen.add(item.id)
        return evals

    @classmethod
    def from_yaml(cls, path: str | Path) -> "ExperimentSpec":
        """Load the config and anchor paths without probing referenced files."""
        path = Path(path).absolute()
        original = path.read_bytes()
        spec = cls.model_validate(yaml.load(original.decode("utf-8"), Loader=_ConfigLoader))
        spec._input_yaml = original

        spec.model.source.path = path.parent / spec.model.source.path
        spec.model.runtime.python = path.parent / spec.model.runtime.python
        spec.adapter.source.path = path.parent / spec.adapter.source.path
        output_base = path.parent if "root" in spec.output.model_fields_set else Path.cwd()
        spec.output.root = output_base / spec.output.root
        return spec

    def checkpoint_for(self, eval_spec: EvalSpec | None = None) -> Path | None:
        checkpoint = self.model.checkpoint
        if eval_spec is not None and "checkpoint" in eval_spec.model_fields_set:
            checkpoint = eval_spec.checkpoint
        return self.model.source.path / checkpoint if checkpoint is not None else None

    def check_paths(self, *, eval_spec: EvalSpec | None = None) -> list[str]:
        """Check local input paths without launching the interpreter or importing the adapter."""
        issues = []
        for field, directory in (
            ("model.source.path", self.model.source.path),
            ("adapter.source.path", self.adapter.source.path),
        ):
            if not directory.is_dir():
                issues.append(f"{field}: expected an existing directory at {directory}")

        python = self.model.runtime.python
        if not python.is_file() or not os.access(python, os.X_OK):
            issues.append(f"model.runtime.python: expected an executable file at {python}")

        checkpoints = [("model.checkpoint", self.checkpoint_for())]
        if eval_spec is not None:
            checkpoints = [(f"{eval_spec.id}.checkpoint", self.checkpoint_for(eval_spec))]
        elif self.evals:
            checkpoints = [
                (f"evals.{index}.checkpoint", self.checkpoint_for(item))
                for index, item in enumerate(self.evals)
            ]
        for field, checkpoint in checkpoints:
            if checkpoint is not None and not (checkpoint.is_file() or checkpoint.is_dir()):
                issues.append(f"{field}: expected an existing file or directory at {checkpoint}")
        return issues
