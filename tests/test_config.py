import sys
from copy import deepcopy
from pathlib import Path

import pytest
import yaml
from typer.testing import CliRunner

from openplatform.cli import app
from openplatform.schemas import ExperimentSpec

EXAMPLES = Path(__file__).resolve().parents[1] / "examples" / "submissions"
runner = CliRunner()


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "project" / "submission.yaml"
    path.parent.mkdir()
    data = {
        "schema_version": "1",
        "name": "test-policy",
        "model": {
            "source": {"kind": "local", "path": "./model"},
            "checkpoint": "weights/base",
            "runtime": {"kind": "native", "python": "./policy-env/bin/python"},
        },
        "adapter": {
            "source": {"kind": "local", "path": "./adapter"},
            "entrypoint": "adapter:Model",
        },
        "evals": [{"id": "smoke", "benchmark": "robodojo", "recipe": "reference"}],
    }
    return path, data


def write_config(path, data):
    path.write_text(yaml.safe_dump(data), encoding="utf-8")


def test_cli_help():
    result = runner.invoke(app, ["--help"], color=False)
    assert result.exit_code == 0
    assert "validate" in result.stdout


@pytest.mark.parametrize("name", ["eval-only.yaml", "weightless-policy.yaml"])
def test_eval_only_examples(name):
    result = runner.invoke(app, ["validate", str(EXAMPLES / name)])
    assert result.exit_code == 0, result.output
    assert "structure is valid" in result.stdout


def test_paths_and_checkpoint_overrides(config, tmp_path, monkeypatch):
    path, data = config
    data["evals"] += [
        {**data["evals"][0], "id": "override", "checkpoint": "weights/alternate.bin"},
        {**data["evals"][0], "id": "weightless", "checkpoint": None},
    ]
    data["output"] = {"root": "./reports"}
    write_config(path, data)

    elsewhere = tmp_path / "elsewhere"
    elsewhere.mkdir()
    monkeypatch.chdir(elsewhere)
    spec = ExperimentSpec.from_yaml(Path("../project/submission.yaml"))
    assert spec.model.source.path.resolve() == path.parent / "model"
    assert spec.adapter.source.path.resolve() == path.parent / "adapter"
    assert spec.output.root.resolve() == path.parent / "reports"
    assert [spec.checkpoint_for(item) for item in spec.evals] == [
        spec.model.source.path / "weights/base",
        spec.model.source.path / "weights/alternate.bin",
        None,
    ]


@pytest.mark.parametrize("checkpoint", ["weights/base", "weights/base.bin", None])
def test_local_path_check_is_opt_in(config, checkpoint):
    path, data = config
    data["model"]["checkpoint"] = checkpoint
    del data["evals"]
    write_config(path, data)
    args = ["validate", str(path)]

    assert runner.invoke(app, args).exit_code == 0
    result = runner.invoke(app, [*args, "--check-paths"])
    assert result.exit_code == 1
    assert "model.source.path" in result.stderr
    assert "model.runtime.python" in result.stderr
    assert ("model.checkpoint" in result.stderr) == (checkpoint is not None)

    spec = ExperimentSpec.from_yaml(path)
    spec.model.source.path.mkdir()
    spec.adapter.source.path.mkdir()
    (spec.adapter.source.path / "adapter.py").write_text('raise RuntimeError("imported")\n')
    python = spec.model.runtime.python
    python.parent.mkdir(parents=True)
    python.symlink_to(sys.executable)
    assert ExperimentSpec.from_yaml(path).model.runtime.python == python

    if checkpoint is not None:
        weights = spec.model.source.path / checkpoint
        weights.parent.mkdir()
        if weights.suffix:
            weights.touch()
        else:
            weights.mkdir()

    result = runner.invoke(app, [*args, "--check-paths"])
    assert result.exit_code == 0, result.output
    assert "Local path check passed" in result.stdout


@pytest.mark.parametrize(
    ("case", "message"),
    [
        ("missing_checkpoint", "model.checkpoint: Field required"),
        ("absolute_checkpoint", "checkpoint must be relative to model.source.path"),
        ("unknown_field", "model.source.typo: Extra inputs are not permitted"),
        ("selection", "suite and preset are mutually exclusive"),
        ("duplicate_id", "duplicate eval id 'smoke'"),
        ("invalid_parameters", "evals.0.parameters: Input should be a valid dictionary"),
        ("numeric_version", "schema_version: Input should be '1'"),
        ("empty_evals", "evals: List should have at least 1 item"),
    ],
)
def test_invalid_configuration(config, case, message):
    path, data = config
    match case:
        case "missing_checkpoint":
            del data["model"]["checkpoint"]
        case "absolute_checkpoint":
            data["evals"][0]["checkpoint"] = str(path.parent / "weights")
        case "unknown_field":
            data["model"]["source"]["typo"] = True
        case "selection":
            data["evals"][0].update(suite="full", preset="smoke")
        case "duplicate_id":
            data["evals"].append(deepcopy(data["evals"][0]))
        case "invalid_parameters":
            data["evals"][0]["parameters"] = []
        case "numeric_version":
            data["schema_version"] = 1
        case "empty_evals":
            data["evals"] = []
    write_config(path, data)
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 2
    assert message in result.stderr


def test_training_execution_is_rejected():
    result = runner.invoke(app, ["validate", str(EXAMPLES / "optional-training.yaml")])
    assert result.exit_code == 2
    assert "evals.0:" in result.stderr
    assert "training execution is unsupported" in result.stderr


@pytest.mark.parametrize("contents", ["model: [", "model: {}\nmodel: {}\n"])
def test_invalid_yaml_is_rejected(config, contents):
    path, _ = config
    path.write_text(contents, encoding="utf-8")
    result = runner.invoke(app, ["validate", str(path)])
    assert result.exit_code == 2
    assert "Cannot read configuration" in result.stderr
