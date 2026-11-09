import fcntl
import hashlib
import json
import os
from collections import Counter
from collections.abc import Iterator, Mapping, Sequence
from contextlib import contextmanager
from pathlib import Path, PurePosixPath
from tempfile import NamedTemporaryFile
from threading import Lock
from typing import Any, BinaryIO
from uuid import uuid4

import yaml
from pydantic import TypeAdapter

from openplatform.results import EvalResult
from openplatform.runtime import ExitResult
from openplatform.schemas import EvalId

_SCHEMA_VERSION = "1"
_ACTIVE = {"pending", "running"}
_EVAL_ID = TypeAdapter(EvalId)


@contextmanager
def _stage_bytes(path: Path, data: bytes) -> Iterator[Path]:
    with NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.", delete=False) as stream:
        staged = Path(stream.name)
        try:
            stream.write(data)
            stream.flush()
            os.fsync(stream.fileno())
            stream.close()
            yield staged
        finally:
            staged.unlink(missing_ok=True)


def _write_json(path: Path, value: dict) -> None:
    data = json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    with _stage_bytes(path, data.encode("utf-8")) as staged:
        os.replace(staged, path)


def _read_json(path: Path) -> dict:
    with path.open(encoding="utf-8") as stream:
        return json.load(stream)


class LocalArtifactStore:
    """Index immutable local files by relative path, type, size and SHA-256."""

    def __init__(self, run_dir: Path):
        self.root = run_dir.resolve()
        self._index_path = self.root / "artifacts.json"

    def _path(self, logical_path: str | PurePosixPath) -> tuple[str, Path]:
        relative = PurePosixPath(logical_path)
        if (
            relative.is_absolute()
            or not relative.parts
            or any(part.startswith(".") for part in relative.parts)
            or relative.as_posix() == self._index_path.name
        ):
            raise ValueError(f"invalid relative artifact path: {logical_path}")
        path = self.root.joinpath(*relative.parts)
        if path.resolve() != path:
            raise ValueError(
                f"artifact path must stay inside the run without symlinks: {logical_path}"
            )
        return relative.as_posix(), path

    def _index(self) -> dict:
        try:
            return _read_json(self._index_path)
        except FileNotFoundError:
            return {"schema_version": _SCHEMA_VERSION, "artifacts": {}}

    @contextmanager
    def _locked_index(self) -> Iterator[dict]:
        with (self.root / ".artifacts.lock").open("a+b") as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield self._index()

    def put(self, logical_path: str, data: bytes, *, kind: str = "file") -> dict:
        name, path = self._path(logical_path)
        entry = {
            "path": name,
            "kind": kind,
            "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(),
        }
        with self._locked_index() as index:
            if name in index["artifacts"]:
                raise FileExistsError(path)
            path.parent.mkdir(parents=True, exist_ok=True)
            with _stage_bytes(path, data) as staged:
                os.link(staged, path)  # Publish atomically without replacing any existing file.
            index["artifacts"][name] = entry
            _write_json(self._index_path, index)
        return entry

    def register(self, logical_path: str, *, kind: str = "file") -> dict:
        """Index a file after the writer has closed it."""
        name, path = self._path(logical_path)
        with self._locked_index() as index:
            if not path.is_file():
                raise FileNotFoundError(path)
            with path.open("rb") as stream:
                digest = hashlib.file_digest(stream, "sha256").hexdigest()
                size = os.fstat(stream.fileno()).st_size
            entry = {"path": name, "kind": kind, "size_bytes": size, "sha256": digest}
            previous = index["artifacts"].get(name)
            if previous is not None and previous != entry:
                raise FileExistsError(f"artifact already indexed with different metadata: {path}")
            if previous is None:
                index["artifacts"][name] = entry
                _write_json(self._index_path, index)
        return entry

    def open(self, logical_path: str) -> BinaryIO:
        _, path = self._path(logical_path)
        return path.open("rb")

    def list(self) -> list[dict]:
        return list(self._index()["artifacts"].values())

    def stat(self, logical_path: str) -> dict:
        name, path = self._path(logical_path)
        try:
            return self._index()["artifacts"][name]
        except KeyError:
            raise FileNotFoundError(path) from None


class RunStore:
    """One owned run with one attempt per eval and atomic per-attempt state."""

    def __init__(self, run_dir: Path, eval_ids: Sequence[str]):
        self.run_dir = run_dir
        self.run_id = run_dir.name
        self.artifacts = LocalArtifactStore(run_dir)
        self._eval_ids = frozenset(eval_ids)
        self._active = True
        self._mutex = Lock()

    @classmethod
    @contextmanager
    def create(
        cls,
        root: Path,
        *,
        input_config: Path | bytes,
        resolved_config: Mapping[str, Any],
        eval_ids: Sequence[str],
    ) -> Iterator["RunStore"]:
        if isinstance(eval_ids, str):
            raise ValueError("eval_ids must be a sequence of eval IDs")
        ids = tuple(_EVAL_ID.validate_python(value, strict=True) for value in eval_ids)
        if not ids or len(ids) != len(set(ids)):
            raise ValueError("expected at least one unique eval ID")
        original = input_config if isinstance(input_config, bytes) else input_config.read_bytes()
        resolved = yaml.safe_dump(
            dict(resolved_config), allow_unicode=True, sort_keys=False
        ).encode("utf-8")

        run_dir = root.resolve() / uuid4().hex
        run_dir.parent.mkdir(parents=True, exist_ok=True)
        run_dir.mkdir()
        with (run_dir / ".owner.lock").open("xb") as owner:
            fcntl.flock(owner, fcntl.LOCK_EX)
            run = cls(run_dir, ids)
            run.artifacts.put("input_config.yaml", original, kind="config")
            run.artifacts.put("resolved_config.yaml", resolved, kind="config")
            refs = {}
            for eval_id in ids:
                attempt = run.attempt_dir(eval_id)
                (attempt / "logs").mkdir(parents=True)
                state = attempt / "state.json"
                _write_json(state, {"eval_id": eval_id, "attempt": 1, "status": "pending"})
                refs[eval_id] = state.relative_to(run_dir).as_posix()
            _write_json(
                run_dir / "run.json",
                {"schema_version": _SCHEMA_VERSION, "run_id": run.run_id, "evals": refs},
            )
            try:
                yield run
            finally:
                with run._mutex:
                    run._active = False

    def attempt_dir(self, eval_id: str) -> Path:
        if eval_id not in self._eval_ids:
            raise KeyError(f"unknown eval ID: {eval_id}")
        return self.run_dir / "evals" / eval_id / "attempts" / "0001"

    def _state(self, attempt: Path, expected: str) -> dict:
        if not self._active:
            raise RuntimeError("run is no longer open for writing")
        state = _read_json(attempt / "state.json")
        if state["status"] != expected:
            raise ValueError(f"{state['eval_id']} is {state['status']}, expected {expected}")
        return state

    def start_eval(self, eval_id: str) -> Path:
        attempt = self.attempt_dir(eval_id)
        with self._mutex:
            state = self._state(attempt, "pending")
            _write_json(attempt / "state.json", {**state, "status": "running"})
        return attempt

    def finish_eval(self, eval_id: str, result: ExitResult) -> None:
        if result.status == "canceled":
            status = "canceled"
        elif result.status == "exited" and result.returncode == 0:
            status = "succeeded"
        else:
            status = "failed"
        self._finish(
            eval_id,
            {
                "status": status,
                "process_status": result.status,
                "returncode": result.returncode,
                "duration_seconds": result.duration_seconds,
            },
            {"stdout": result.stdout_path, "stderr": result.stderr_path},
        )

    def fail_launch(self, eval_id: str, error: Exception) -> None:
        logs = self.attempt_dir(eval_id) / "logs"
        paths = {name: logs / f"{name}.log" for name in ("stdout", "stderr")}
        self._finish(
            eval_id,
            {
                "status": "failed",
                "process_status": "launch_error",
                "error": f"{type(error).__name__}: {error}",
            },
            {name: path for name, path in paths.items() if path.is_file()},
        )

    def record_eval(
        self, result: EvalResult, *, processes: Mapping[str, ExitResult], metrics_path: str | None
    ) -> None:
        attempt = self.attempt_dir(result.eval_id)
        outcome = {
            "status": result.status,
            "processes": {
                name: {
                    "status": exit.status,
                    "returncode": exit.returncode,
                    "duration_seconds": exit.duration_seconds,
                }
                for name, exit in processes.items()
            },
        }
        if result.error is not None:
            outcome["error"] = result.error.model_dump(mode="json")
        if metrics_path is not None:
            self.artifacts.stat(metrics_path)
            outcome["metrics"] = metrics_path
        log_dir = attempt / "logs"
        logs = {
            path.relative_to(log_dir).as_posix(): path
            for path in sorted(log_dir.rglob("*"))
            if path.is_file()
        }
        self._finish(result.eval_id, outcome, logs)

    def skip_eval(self, eval_id: str, *, canceled: bool) -> EvalResult:
        attempt = self.attempt_dir(eval_id)
        result = EvalResult(eval_id=eval_id, status="canceled" if canceled else "skipped")
        with self._mutex:
            state = self._state(attempt, "pending")
            _write_json(
                attempt / "state.json", {**state, "status": result.status, "started": False}
            )
        return result

    def _finish(self, eval_id: str, outcome: dict, logs: dict[str, Path]) -> None:
        attempt = self.attempt_dir(eval_id)
        with self._mutex:
            state = self._state(attempt, "running")
            if any(not path.is_relative_to(attempt / "logs") for path in logs.values()):
                raise ValueError("process logs must be inside this attempt's logs directory")
            refs = {name: path.relative_to(self.run_dir).as_posix() for name, path in logs.items()}
            for path in refs.values():
                self.artifacts.register(path, kind="log")
            _write_json(attempt / "state.json", {**state, **outcome, "logs": refs})


def read_status(run_dir: Path) -> dict:
    """Summarize a run, deriving interruption from a released owner lock."""
    run_dir = run_dir.resolve(strict=True)
    if not run_dir.is_dir():
        raise NotADirectoryError(run_dir)
    try:
        owner = (run_dir / ".owner.lock").open("rb")
    except FileNotFoundError:
        return _summarize(run_dir, active=False)
    with owner:
        try:
            fcntl.flock(owner, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except BlockingIOError:
            return _summarize(run_dir, active=True)
        return _summarize(run_dir, active=False)


def _summarize(run_dir: Path, *, active: bool) -> dict:
    try:
        manifest = _read_json(run_dir / "run.json")
    except FileNotFoundError:
        return {
            "run_id": run_dir.name,
            "status": "pending" if active else "interrupted",
            "evals": {},
            "counts": {},
        }

    evals = {eval_id: _read_json(run_dir / path) for eval_id, path in manifest["evals"].items()}
    for state in evals.values():
        if not active and state["status"] in _ACTIVE:
            state["status"] = "interrupted"
    counts = Counter(state["status"] for state in evals.values())
    for status in ("interrupted", "running", "pending", "canceled"):
        if counts[status]:
            break
    else:
        if counts["succeeded"] == len(evals):
            status = "succeeded"
        elif counts["succeeded"]:
            status = "partially_succeeded"
        else:
            status = "failed"
    return {"run_id": manifest["run_id"], "status": status, "evals": evals, "counts": dict(counts)}
