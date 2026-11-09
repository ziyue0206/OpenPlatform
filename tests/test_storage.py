import hashlib
import json
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor

import pytest
import yaml

from openplatform import storage
from openplatform.runtime import ExitResult
from openplatform.storage import RunStore, read_status


@pytest.fixture
def inputs(tmp_path):
    path = tmp_path / "experiment.yaml"
    path.write_bytes(b"# snapshot this verbatim\nname: original\n")
    return {
        "root": tmp_path / "runs",
        "input_config": path,
        "resolved_config": {"name": "resolved"},
        "eval_ids": ["eval"],
    }


def test_repeat_run_preserves_snapshots_and_artifacts(inputs):
    with RunStore.create(**inputs) as first:
        initial = first.artifacts.stat("input_config.yaml")
        assert initial["sha256"] == hashlib.sha256(inputs["input_config"].read_bytes()).hexdigest()
        artifact = "evals/eval/attempts/0001/native/result.json"
        first.artifacts.put(artifact, b"first", kind="native")
        with pytest.raises(FileExistsError):
            first.artifacts.put(artifact, b"overwrite")
        with first.artifacts.open(artifact) as stream:
            assert stream.read() == b"first"
        with pytest.raises(ValueError, match="relative artifact path"):
            first.artifacts.put("../outside", b"escape")
        assert read_status(first.run_dir)["status"] == "pending"

    inputs["input_config"].write_text("name: updated\n", encoding="utf-8")
    with RunStore.create(**inputs) as second:
        assert second.run_id != first.run_id
        second.artifacts.put(artifact, b"second", kind="native")
        assert (first.run_dir / artifact).read_bytes() == b"first"
        assert (second.run_dir / artifact).read_bytes() == b"second"
        assert (
            first.run_dir / "input_config.yaml"
        ).read_bytes() == b"# snapshot this verbatim\nname: original\n"
        assert yaml.safe_load((second.run_dir / "resolved_config.yaml").read_text()) == {
            "name": "resolved"
        }
        assert all(not item["path"].startswith("/") for item in second.artifacts.list())
    assert read_status(first.run_dir)["status"] == "interrupted"


def test_state_replace_failure_preserves_previous_state(inputs, monkeypatch):
    with RunStore.create(**inputs) as run:
        state = run.attempt_dir("eval") / "state.json"
        before = state.read_bytes()

        def fail_replace(staged, target):
            assert target == state
            assert staged.parent == state.parent
            assert json.loads(staged.read_text())["status"] == "running"
            assert state.read_bytes() == before
            raise OSError("simulated replace failure")

        with monkeypatch.context() as patch:
            patch.setattr(storage.os, "replace", fail_replace)
            with pytest.raises(OSError, match="simulated replace failure"):
                run.start_eval("eval")
        assert state.read_bytes() == before
        assert list(state.parent.glob(".state.json.*")) == []
        run.start_eval("eval")
        assert read_status(run.run_dir)["status"] == "running"


def test_incomplete_log_index_can_be_retried_before_terminal_state(inputs, monkeypatch):
    with RunStore.create(**inputs) as run:
        logs = run.start_eval("eval") / "logs"
        stdout, stderr = logs / "stdout.log", logs / "stderr.log"
        stdout.write_bytes(b"output")
        stderr.write_bytes(b"diagnostic")
        result = ExitResult("exited", 0, 0.01, stdout, stderr)
        replace = storage.os.replace

        def fail_second_log(staged, target):
            if target.name == "artifacts.json" and "stderr.log" in staged.read_text():
                raise OSError("simulated artifact index failure")
            replace(staged, target)

        with monkeypatch.context() as patch:
            patch.setattr(storage.os, "replace", fail_second_log)
            with pytest.raises(OSError, match="simulated artifact index failure"):
                run.finish_eval("eval", result)
        assert read_status(run.run_dir)["status"] == "running"
        assert stdout.read_bytes() == b"output" and stderr.read_bytes() == b"diagnostic"
        run.finish_eval("eval", result)
        final = read_status(run.run_dir)["evals"]["eval"]
        assert final["status"] == "succeeded"
        assert run.artifacts.stat(final["logs"]["stderr"])["size_bytes"] == 10


def test_index_serializes_concurrent_writes(inputs):
    with RunStore.create(**inputs) as run, ThreadPoolExecutor(max_workers=4) as pool:
        keys = [f"native/{index}.json" for index in range(8)]
        entries = list(pool.map(lambda key: run.artifacts.put(key, b"result"), keys))
        assert {entry["path"] for entry in entries} == set(keys)
        assert {entry["path"] for entry in run.artifacts.list()} == {
            *keys,
            "input_config.yaml",
            "resolved_config.yaml",
        }


def test_run_without_live_writer_is_interrupted(inputs):
    code = """
import os, sys
from pathlib import Path
from openplatform.storage import RunStore
with RunStore.create(Path(sys.argv[1]), input_config=Path(sys.argv[2]), resolved_config={}, eval_ids=['eval']) as run:
    run.start_eval('eval')
    print(run.run_dir, flush=True)
    os._exit(17)
"""
    child = subprocess.run(
        [sys.executable, "-c", code, inputs["root"], inputs["input_config"]],
        capture_output=True,
        text=True,
        timeout=10,
    )
    assert child.returncode == 17, child.stderr
    [run_dir] = list(inputs["root"].iterdir())
    assert str(run_dir.resolve()) == child.stdout.strip()
    state = run_dir / "evals/eval/attempts/0001/state.json"
    original = state.read_bytes()
    status = read_status(run_dir)
    assert status["status"] == "interrupted" and status["counts"] == {"interrupted": 1}
    assert status["evals"]["eval"]["status"] == "interrupted"
    assert json.loads(original)["status"] == "running" and state.read_bytes() == original
