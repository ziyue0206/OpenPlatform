import json
import os
import shutil
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from threading import Event, Thread

import pytest
import yaml

from openplatform.runner import ExperimentRunner
from openplatform.schemas import ExperimentSpec
from openplatform.storage import read_status

_CLI = Path(sys.executable).with_name("openplatform")
_FIXTURE = Path(__file__).resolve().parents[1] / "examples/submissions/dummy"


def _eval(eval_id, **overrides):
    return {"id": eval_id, "benchmark": "dummy", "recipe": "reference", **overrides}


@pytest.fixture
def submission(tmp_path):
    model = tmp_path / "model space"
    model.mkdir()
    shutil.copyfile(_FIXTURE / "policy.py", model / "policy.py")
    data = yaml.safe_load((_FIXTURE / "submission.yaml").read_text())
    data["model"]["runtime"]["python"] = os.environ.get(
        "OPENPLATFORM_TEST_POLICY_PYTHON", sys.executable
    )
    return model / "submission.yaml", data


def _write(submission, evals):
    path, config = submission
    config["evals"] = evals
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    return path


def _cli(cwd, *args):
    return subprocess.run([_CLI, *args], cwd=cwd, text=True, capture_output=True, timeout=30)


def _attempt(run_dir, eval_id):
    return run_dir / "evals" / eval_id / "attempts/0001"


def _check_policy_port_closed(attempt):
    ready = (attempt / "logs/policy.stdout.log").read_text()
    port = int(ready.split("OPENPLATFORM_POLICY_READY ws://127.0.0.1:")[1].splitlines()[0])
    with (
        pytest.raises(ConnectionRefusedError),
        socket.create_connection(("127.0.0.1", port), timeout=1),
    ):
        pytest.fail("policy is still listening")


def test_cli_eval_and_status_from_outside_workspace(submission, tmp_path):
    config = _write(submission, [_eval("ignored-by-single-eval")])
    listing = _cli(tmp_path, "list")
    assert listing.returncode == 0, listing.stderr
    manifest = next(item for item in json.loads(listing.stdout) if item["id"] == "dummy")
    assert manifest["id"] == "dummy" and manifest["status"] == "development_fixture"

    command = _cli(tmp_path, "eval", config.parent, "--bench", "dummy", "--preset", "smoke")
    assert command.returncode == 0, command.stderr or command.stdout
    result = json.loads(command.stdout)
    assert result["status"] == "succeeded"
    run_dir = Path(result["run_dir"])
    assert run_dir.parent == tmp_path / "runs"
    status = _cli(tmp_path, "status", run_dir)
    assert status.returncode == 0, status.stderr
    record = json.loads(status.stdout)["evals"]["dummy"]
    metrics = json.loads((run_dir / record["metrics"]).read_text())["metrics"]
    assert {row["name"]: row["value"] for row in metrics} == {
        "success_rate": 0.5,
        "completed_episodes": 2,
    }
    attempt = _attempt(run_dir, "dummy")
    raw = json.loads((attempt / "native/result.json").read_text())
    assert [episode["success"] for episode in raw["episodes"]] == [True, False]
    assert record["processes"]["policy"]["returncode"] == 0
    assert record["processes"]["benchmark"]["returncode"] == 0
    index = json.loads((run_dir / "artifacts.json").read_text())["artifacts"]
    assert set(record["logs"].values()) <= index.keys()
    assert (attempt / "native/result.json").relative_to(run_dir).as_posix() in index
    resolved = yaml.safe_load((run_dir / "resolved_config.yaml").read_text())["evals"][0]
    assert resolved["preset"] == "smoke" and resolved["io_profile"] == "dummy-delta-v1"
    assert resolved["checkpoint"] is None and resolved["recipe_version"] == "1"
    _check_policy_port_closed(attempt)


def test_cli_run_continues_after_policy_and_environment_errors(submission, tmp_path):
    config = _write(
        submission,
        [
            _eval("inference-error", adapter_config={"fail_inference": True}),
            _eval("environment-error", parameters={"fault": "environment"}),
            _eval("still-runs"),
        ],
    )
    command = _cli(tmp_path, "run", config, "--output-dir", "evidence")
    assert command.returncode == 1, command.stderr or command.stdout
    result = json.loads(command.stdout)
    assert result["status"] == "partially_succeeded"
    assert [(row["eval_id"], row["status"]) for row in result["evals"]] == [
        ("inference-error", "failed"),
        ("environment-error", "failed"),
        ("still-runs", "succeeded"),
    ]
    assert [row["error"]["kind"] for row in result["evals"][:2]] == ["inference", "environment"]
    assert all(not row["metrics"] for row in result["evals"][:2])
    run_dir = Path(result["run_dir"])
    assert run_dir.parent == tmp_path / "evidence"
    records = read_status(run_dir)["evals"]
    assert "injected dummy inference error" in records["inference-error"]["error"]["message"]
    for eval_id in ("inference-error", "environment-error", "still-runs"):
        _check_policy_port_closed(_attempt(run_dir, eval_id))
    partial = _attempt(run_dir, "environment-error") / "native/result.json"
    assert len(json.loads(partial.read_text())["episodes"]) == 1


def test_python_api_resolves_and_runs_snapshot(submission, tmp_path, monkeypatch):
    config = _write(submission, [_eval("api")])
    monkeypatch.chdir(tmp_path)
    runner = ExperimentRunner()
    plan = runner.plan(ExperimentSpec.from_yaml(config.relative_to(tmp_path)))
    assert plan.evals[0].suite == "full"
    original = config.read_bytes()
    config.write_text("name: replaced after planning\n")
    assert not (tmp_path / "runs").exists()
    result = runner.run(plan)
    assert result.status == "succeeded"
    assert result.evals[0].metrics[0].value == 0.5
    assert (result.run_dir / "input_config.yaml").read_bytes() == original


def _wait_for_episode(root):
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        if list(root.glob("*/evals/active/attempts/0001/native/result.json")):
            return True
        time.sleep(0.02)
    return False


def _assert_canceled(run_dir):
    status = read_status(run_dir)
    assert status["status"] == "canceled"
    assert status["evals"]["active"]["status"] == "canceled"
    assert status["evals"]["active"]["processes"]["benchmark"]["status"] == "canceled"
    assert status["evals"]["later"]["status"] == "canceled"
    assert not status["evals"]["later"]["started"]
    assert not list((_attempt(run_dir, "later") / "logs").iterdir())
    assert (_attempt(run_dir, "active") / "native/result.json").is_file()
    assert "metrics" not in status["evals"]["active"]
    _check_policy_port_closed(_attempt(run_dir, "active"))


def test_python_api_cancel_event_stops_remaining_evals(submission, tmp_path):
    config = _write(submission, [_eval("active", parameters={"episode_delay": 30}), _eval("later")])
    root = tmp_path / "runs"
    runner = ExperimentRunner()
    cancel = Event()
    observed = []

    def cancel_after_episode():
        observed.append(_wait_for_episode(root))
        if observed[0]:
            [result] = list(root.glob("*/evals/active/attempts/0001/native/result.json"))
            (result.parent / ".result.json.staged").write_text("partial write")
        cancel.set()

    watcher = Thread(target=cancel_after_episode)
    watcher.start()
    try:
        result = runner.run(
            runner.plan(ExperimentSpec.from_yaml(config)), root, cancel_event=cancel
        )
    finally:
        watcher.join()
    assert observed == [True]
    assert [row.status for row in result.evals] == ["canceled", "canceled"]
    _assert_canceled(result.run_dir)
    index = (result.run_dir / "artifacts.json").read_text()
    assert ".result.json.staged" not in index


def test_cli_sigint_returns_130_and_stops_remaining_evals(submission, tmp_path):
    config = _write(submission, [_eval("active", parameters={"episode_delay": 30}), _eval("later")])
    root = tmp_path / "runs"
    with subprocess.Popen(
        [_CLI, "run", config, "--output-dir", root],
        cwd=tmp_path,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
    ) as process:
        try:
            assert _wait_for_episode(root)
            process.send_signal(signal.SIGINT)
            stdout, stderr = process.communicate(timeout=15)
            assert process.returncode == 130, stderr or stdout
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.communicate(timeout=15)
    _assert_canceled(Path(json.loads(stdout)["run_dir"]))


def test_plan_rejects_unknown_parameters_without_launch(submission, tmp_path):
    config = _write(submission, [_eval("typo", parameters={"typo": True})])
    command = _cli(tmp_path, "run", config)
    assert command.returncode == 2
    assert "typo: Extra inputs are not permitted" in command.stderr
    assert not (tmp_path / "runs").exists()


def test_python_api_can_stop_after_preflight_error(submission, tmp_path):
    config = _write(submission, [_eval("missing", checkpoint="missing.bin"), _eval("later")])
    runner = ExperimentRunner()
    result = runner.run(
        runner.plan(ExperimentSpec.from_yaml(config)),
        tmp_path / "runs",
        continue_on_error=False,
    )
    assert result.status == "failed"
    assert [row.status for row in result.evals] == ["failed", "skipped"]
    assert result.evals[0].error.kind == "preflight"
    assert not list((_attempt(result.run_dir, "later") / "logs").iterdir())


def test_core_import_leaves_model_and_simulator_dependencies_unloaded(tmp_path):
    subprocess.run(
        [
            sys.executable,
            "-c",
            "from openplatform.runner import Registry; import sys; registry = Registry(); "
            "registry.load(registry.describe('dummy')); "
            "assert not {'numpy', 'torch', 'jax', 'isaacsim', 'openplatform_sdk'} & sys.modules.keys()",
        ],
        cwd=tmp_path,
        check=True,
        timeout=10,
    )
