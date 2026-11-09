import os
import select
import signal
import subprocess
import sys
import time
from collections.abc import Callable, Iterator, Mapping, Sequence
from concurrent.futures import Future
from contextlib import closing, contextmanager
from ctypes import CDLL, c_int, c_void_p, get_errno, set_errno, sizeof
from dataclasses import dataclass, field
from math import isfinite
from pathlib import Path
from threading import Event, Thread
from typing import Literal

from openplatform.schemas import RuntimeSpec


@dataclass(frozen=True)
class PreparedRuntime:
    python: Path


@dataclass(frozen=True)
class ExecutionSpec:
    argv: Sequence[str | Path]
    cwd: Path
    stdout_path: Path
    stderr_path: Path
    env: Mapping[str, str] = field(default_factory=dict)
    timeout: float | None = None


@dataclass(frozen=True)
class ExitResult:
    status: Literal["exited", "timed_out", "canceled"]
    returncode: int
    duration_seconds: float
    stdout_path: Path
    stderr_path: Path


@dataclass(frozen=True)
class ProcessHandle:
    pid: int
    stdout_path: Path
    stderr_path: Path
    _cancel: Event = field(default_factory=Event, repr=False)
    _result: Future[ExitResult] = field(default_factory=Future, repr=False)

    def done(self) -> bool:
        return self._result.done()


@contextmanager
def _exit_probe(pid: int) -> Iterator[Callable[[], bool]]:
    # Observe exit without reaping the group leader. Its PID must stay reserved
    # until the final group signal has been sent.
    if sys.platform == "darwin":
        with closing(select.kqueue()) as queue:
            watch = select.kevent(
                pid,
                filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            try:
                queue.control([watch], 0, 0)
            except ProcessLookupError:
                # The child exited before registration and is still waitable.
                yield lambda: True
            else:
                yield lambda: bool(queue.control(None, 1, 0))
    else:
        yield lambda: os.waitid(os.P_PID, pid, os.WEXITED | os.WNOHANG | os.WNOWAIT) is not None


def _signal_group(pid: int, sig: signal.Signals) -> None:
    try:
        os.killpg(pid, sig)
    except ProcessLookupError:
        pass
    except PermissionError:
        # Darwin also reports EPERM if the group contains only exited children.
        # Confirm that case rather than hiding a failure to signal a live process.
        if sys.platform != "darwin" or not _darwin_group_exited(pid):
            raise


def _darwin_group_exited(pid: int) -> bool:
    try:
        list_group = CDLL("/usr/lib/libproc.dylib", use_errno=True).proc_listpgrppids
        list_group.argtypes = (c_int, c_void_p, c_int)
        list_group.restype = c_int
        capacity = list_group(pid, None, 0)
        if capacity <= 0:
            return False
        members = (c_int * capacity)()
        set_errno(0)
        count = list_group(pid, members, sizeof(members))
        if get_errno() or not 0 <= count < capacity:
            return False
        for member in members[:count]:
            with _exit_probe(member) as exited:
                if not exited():
                    return False
    except OSError:
        return False
    return True


class NativeRuntime:
    """Launch argv in an owned POSIX process group with separate on-disk logs."""

    def __init__(self, *, terminate_grace: float = 1.0):
        if sys.platform != "darwin" and not hasattr(os, "waitid"):
            raise OSError("NativeRuntime requires macOS or POSIX waitid support")
        if not isfinite(terminate_grace) or terminate_grace < 0:
            raise ValueError("terminate_grace must be finite and nonnegative")
        self._terminate_grace = terminate_grace

    def prepare(self, spec: RuntimeSpec) -> PreparedRuntime:
        python = spec.python.absolute()
        if not python.is_file() or not os.access(python, os.X_OK):
            raise ValueError(f"expected an executable Python interpreter at {python}")
        return PreparedRuntime(python)

    def launch(self, spec: ExecutionSpec) -> ProcessHandle:
        if signal.getsignal(signal.SIGCHLD) == signal.SIG_IGN:
            raise RuntimeError(
                "NativeRuntime requires waitable child processes; SIGCHLD is ignored"
            )
        if isinstance(spec.argv, (str, bytes)) or not spec.argv:
            raise ValueError("argv must be a nonempty argument sequence")
        if spec.timeout is not None and (not isfinite(spec.timeout) or spec.timeout <= 0):
            raise ValueError("timeout must be finite and positive")

        stdout_path = spec.stdout_path.absolute()
        stderr_path = spec.stderr_path.absolute()
        if stdout_path.resolve() == stderr_path.resolve():
            raise ValueError("stdout and stderr require separate paths")
        stdout_path.parent.mkdir(parents=True, exist_ok=True)
        stderr_path.parent.mkdir(parents=True, exist_ok=True)
        with stdout_path.open("xb") as stdout, stderr_path.open("xb") as stderr:
            try:
                process = subprocess.Popen(
                    spec.argv,
                    cwd=spec.cwd,
                    env={**os.environ, **spec.env},
                    stdin=subprocess.DEVNULL,
                    stdout=stdout,
                    stderr=stderr,
                    start_new_session=True,
                )
            except (OSError, ValueError, TypeError) as error:
                stderr.write(f"OpenPlatform launch failed: {error}\n".encode(errors="replace"))
                raise

        handle = ProcessHandle(process.pid, stdout_path, stderr_path)
        worker = Thread(
            target=self._supervise,
            args=(process, handle, time.monotonic(), spec.timeout),
            name=f"openplatform-{process.pid}",
            daemon=True,
        )
        try:
            worker.start()
        except RuntimeError:
            _signal_group(process.pid, signal.SIGKILL)
            process.wait()
            raise
        return handle

    def _supervise(
        self,
        process: subprocess.Popen,
        handle: ProcessHandle,
        started: float,
        timeout: float | None,
    ) -> None:
        deadline = started + timeout if timeout is not None else float("inf")
        try:
            with _exit_probe(process.pid) as exited:
                status = self._wait_for_exit(exited, handle._cancel, deadline)
        except ChildProcessError as error:
            # Another reaper took the child. The numeric group ID is no longer ours.
            handle._result.set_exception(error)
            return
        except Exception as error:
            try:
                _signal_group(process.pid, signal.SIGKILL)
                process.wait()
            finally:
                handle._result.set_exception(error)
            return

        try:
            if status != "exited":
                _signal_group(process.pid, signal.SIGTERM)
                time.sleep(self._terminate_grace)
            _signal_group(process.pid, signal.SIGKILL)
            returncode = process.wait()
        except Exception as error:
            process.poll()  # Reap an exited leader after the final signal attempt.
            handle._result.set_exception(error)
        else:
            handle._result.set_result(
                ExitResult(
                    status,
                    returncode,
                    time.monotonic() - started,
                    handle.stdout_path,
                    handle.stderr_path,
                )
            )

    @staticmethod
    def _wait_for_exit(
        exited: Callable[[], bool], cancel: Event, deadline: float
    ) -> Literal["exited", "timed_out", "canceled"]:
        while not exited():
            if cancel.is_set():
                return "canceled"
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return "timed_out"
            cancel.wait(min(remaining, 0.02))
        return "exited"

    def wait(self, handle: ProcessHandle) -> ExitResult:
        try:
            return handle._result.result()
        except KeyboardInterrupt:
            handle._cancel.set()
            handle._result.result()
            raise

    def stop(self, handle: ProcessHandle) -> ExitResult:
        handle._cancel.set()
        return self.wait(handle)
