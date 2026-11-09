import errno
import fcntl
import json
import os
import signal
import subprocess
import sys
import time

import pytest

from openplatform import runtime as runtime_module
from openplatform.runtime import ExecutionSpec, NativeRuntime
from openplatform.schemas import RuntimeSpec
from openplatform.storage import RunStore, read_status

_REPORT = """
import json, os, sys
print(json.dumps([sys.executable, os.getcwd(), os.environ['OPENPLATFORM_V03_TEST'], sys.argv[1]]))
print('stderr before exit', file=sys.stderr)
sys.exit(int(sys.argv[2]))
"""

_DESCENDANT = """
import fcntl, os, pathlib, signal, sys, time
signal.signal(signal.SIGTERM, signal.SIG_IGN)
with open(sys.argv[1], 'w') as lock:
    fcntl.flock(lock, fcntl.LOCK_EX)
    pathlib.Path(sys.argv[2]).write_text(str(os.getpgrp()))
    while True:
        time.sleep(1)
"""

_LEADER = """
import pathlib, signal, subprocess, sys, time
mode, lock, ready, descendant = sys.argv[1:]
def on_term(*args):
    print('leader received SIGTERM', file=sys.stderr, flush=True)
    if mode == 'cancel':
        sys.exit(0)
signal.signal(signal.SIGTERM, on_term)
subprocess.Popen([sys.executable, '-c', descendant, lock, ready])
while not pathlib.Path(ready).is_file():
    time.sleep(.01)
print('leader ready', flush=True)
print('leader stderr', file=sys.stderr, flush=True)
if mode == 'natural':
    sys.exit(0)
if mode == 'nonzero':
    sys.exit(23)
while True:
    time.sleep(1)
"""


@pytest.fixture
def config(tmp_path):
    path = tmp_path / "config.yaml"
    path.write_text("name: native-test\n", encoding="utf-8")
    return path


def _execution(attempt, argv, *, cwd, env=None, timeout=None):
    return ExecutionSpec(
        argv=argv,
        cwd=cwd,
        stdout_path=attempt / "logs/stdout.log",
        stderr_path=attempt / "logs/stderr.log",
        env=env or {},
        timeout=timeout,
    )


@pytest.mark.parametrize(("exit_code", "state"), [(0, "succeeded"), (19, "failed")])
def test_native_argv_and_logs(config, tmp_path, exit_code, state):
    runtime = NativeRuntime()
    python = tmp_path / "policy-python"
    python.symlink_to(sys.executable)
    prepared = runtime.prepare(RuntimeSpec(kind="native", python=python))
    assert prepared.python == python
    cwd = tmp_path / "model cwd"
    cwd.mkdir()
    literal = "$(printf shell); literal"

    with RunStore.create(
        tmp_path / "runs", input_config=config, resolved_config={}, eval_ids=["test"]
    ) as run:
        attempt = run.start_eval("test")
        spec = _execution(
            attempt,
            [prepared.python, "-u", "-c", _REPORT, literal, str(exit_code)],
            cwd=cwd,
            env={"OPENPLATFORM_V03_TEST": "policy value"},
            timeout=5,
        )
        handle = runtime.launch(spec)
        try:
            result = runtime.wait(handle)
            assert result.status == "exited" and result.returncode == exit_code
            assert json.loads(result.stdout_path.read_text()) == [
                str(python),
                str(cwd),
                "policy value",
                literal,
            ]
            assert result.stderr_path.read_text() == "stderr before exit\n"
            run.finish_eval("test", result)
            status = read_status(run.run_dir)
            assert status["status"] == state and status["counts"] == {state: 1}
            record = status["evals"]["test"]
            assert record["returncode"] == exit_code
            assert run.artifacts.stat(record["logs"]["stdout"])["size_bytes"] > 0
            with pytest.raises(FileExistsError):
                runtime.launch(spec)
            assert result.stderr_path.read_text() == "stderr before exit\n"
        finally:
            runtime.stop(handle)
    assert read_status(run.run_dir)["status"] == state


def _wait_until(predicate):
    deadline = time.monotonic() + 5
    while not predicate():
        assert time.monotonic() < deadline, "timed out waiting for the test child"
        time.sleep(0.01)


def _lock_released(path):
    with path.open("rb") as stream:
        try:
            fcntl.flock(stream, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        return True


@pytest.mark.parametrize(
    ("mode", "outcome", "returncode", "state"),
    [
        ("natural", "exited", 0, "succeeded"),
        ("nonzero", "exited", 23, "failed"),
        ("timeout", "timed_out", -signal.SIGKILL, "failed"),
        ("cancel", "canceled", 0, "canceled"),
    ],
)
def test_runtime_cleans_owned_group(config, tmp_path, mode, outcome, returncode, state):
    runtime = NativeRuntime(terminate_grace=0.1)
    lock, ready = tmp_path / "child.lock", tmp_path / "child.pgid"
    unrelated = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )
    try:
        with RunStore.create(
            tmp_path / "runs", input_config=config, resolved_config={}, eval_ids=["test"]
        ) as run:
            attempt = run.start_eval("test")
            handle = runtime.launch(
                _execution(
                    attempt,
                    [sys.executable, "-u", "-c", _LEADER, mode, lock, ready, _DESCENDANT],
                    cwd=tmp_path,
                    timeout=1.5 if mode == "timeout" else 10,
                )
            )
            try:
                _wait_until(lambda: ready.is_file() and bool(ready.read_text()))
                assert int(ready.read_text()) == handle.pid
                _wait_until(lambda: "leader ready" in handle.stdout_path.read_text())
                if mode in {"timeout", "cancel"}:
                    assert not _lock_released(lock)
                if mode == "cancel":
                    assert runtime.stop(handle).status == "canceled"
                # Even before wait(), the deadline must have cleaned up the descendant.
                _wait_until(lambda: _lock_released(lock))
                result = runtime.wait(handle)
                assert result.status == outcome and result.returncode == returncode
                assert runtime.stop(handle) is result
                assert unrelated.poll() is None
                with pytest.raises(ChildProcessError):
                    os.waitpid(handle.pid, os.WNOHANG)

                run.finish_eval("test", result)
                record = read_status(run.run_dir)["evals"]["test"]
                assert record["status"] == state and record["process_status"] == outcome
                with run.artifacts.open(record["logs"]["stdout"]) as stdout:
                    assert b"leader ready" in stdout.read()
                with run.artifacts.open(record["logs"]["stderr"]) as stderr:
                    content = stderr.read()
                    assert b"leader stderr" in content
                    if mode in {"timeout", "cancel"}:
                        assert b"leader received SIGTERM" in content
            finally:
                runtime.stop(handle)
    finally:
        unrelated.terminate()
        unrelated.wait(timeout=5)


def test_launch_error_keeps_logs_and_diagnostic(config, tmp_path):
    runtime = NativeRuntime()
    with RunStore.create(
        tmp_path / "runs", input_config=config, resolved_config={}, eval_ids=["test"]
    ) as run:
        attempt = run.start_eval("test")
        spec = _execution(attempt, [tmp_path / "missing-executable"], cwd=tmp_path)
        with pytest.raises(FileNotFoundError) as error:
            runtime.launch(spec)
        run.fail_launch("test", error.value)
        record = read_status(run.run_dir)["evals"]["test"]
        assert record["status"] == "failed" and record["process_status"] == "launch_error"
        assert "missing-executable" in record["error"]
        assert spec.stdout_path.read_bytes() == b""
        assert "OpenPlatform launch failed" in spec.stderr_path.read_text()
        assert run.artifacts.stat(record["logs"]["stderr"])["size_bytes"] > 0


@pytest.mark.skipif(sys.platform != "darwin", reason="Darwin-only killpg fallback")
def test_darwin_keeps_permission_error_for_live_group(monkeypatch):
    child = subprocess.Popen(
        [sys.executable, "-c", "import time; time.sleep(30)"], start_new_session=True
    )

    def deny_signal(*args):
        raise PermissionError(errno.EPERM, "signal denied")

    try:
        with monkeypatch.context() as patch:
            patch.setattr(runtime_module.os, "killpg", deny_signal)
            with pytest.raises(PermissionError, match="signal denied"):
                runtime_module._signal_group(child.pid, signal.SIGKILL)
        assert child.poll() is None
    finally:
        child.kill()
        child.wait(timeout=5)
