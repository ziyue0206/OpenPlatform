import numpy as np

PROTOCOL_VERSION = 1


class PolicyError(RuntimeError):
    """An SDK failure with a stable code and optional RPC context."""

    def __init__(
        self, code: str, message: str, *, op: str | None = None, request_id: str | None = None
    ):
        self.code = code
        self.message = message
        self.op = op
        self.request_id = request_id
        context = f" ({op}, {request_id})" if op is not None else ""
        super().__init__(f"{code}{context}: {message}")


_SESSION_FIELDS = {"episode_id", "step"}
_OP_FIELDS = {
    "hello": {"version", "io_profile"},
    "reset": _SESSION_FIELDS,
    "update_obs": _SESSION_FIELDS | {"observation"},
    "get_action": _SESSION_FIELDS,
    "close": set(),
}


def is_identifier(value: object) -> bool:
    return isinstance(value, str) and bool(value.strip()) and len(value) <= 128


def validate_request(request: dict) -> None:
    op = request.get("op")
    if not isinstance(op, str) or op not in _OP_FIELDS:
        raise PolicyError("invalid_request", "unknown operation")
    if not is_identifier(request.get("request_id")) or set(request) != _OP_FIELDS[op] | {
        "op",
        "request_id",
    }:
        raise PolicyError("invalid_request", f"invalid fields or request_id for {op}")
    if op == "hello":
        if type(request["version"]) is not int or not is_identifier(request["io_profile"]):
            raise PolicyError("invalid_request", "hello requires a version integer and io_profile")
    elif op in {"reset", "update_obs", "get_action"}:
        if (
            not is_identifier(request["episode_id"])
            or type(request["step"]) is not int
            or request["step"] < 0
        ):
            raise PolicyError(
                "invalid_request", "expected episode_id and a nonnegative integer step"
            )
        if op == "update_obs" and not isinstance(request["observation"], dict):
            raise PolicyError("invalid_request", "observation must be a map")


def validate_chunk(chunk: object) -> None:
    if not isinstance(chunk, list) or not chunk:
        raise PolicyError("invalid_action", "get_action must return a nonempty list of actions")
    for index, action in enumerate(chunk):
        if not isinstance(action, dict) or not action:
            raise PolicyError("invalid_action", f"action {index} must be a nonempty map")
        for name, array in action.items():
            if not isinstance(name, str) or not name or not isinstance(array, np.ndarray):
                raise PolicyError("invalid_action", f"action {index} must contain named ndarrays")
            if array.dtype.kind not in "biuf" or not np.isfinite(array).all():
                raise PolicyError(
                    "invalid_action", f"action {index}.{name} must be finite and numeric"
                )
