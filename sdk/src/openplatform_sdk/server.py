import argparse
import asyncio
import importlib
import json
import logging
import sys
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from websockets.exceptions import ConnectionClosed

try:
    from websockets.asyncio.server import ServerConnection, serve
except ImportError:
    from websockets.legacy.server import WebSocketServerProtocol as ServerConnection
    from websockets.legacy.server import serve

from openplatform_sdk.adapter import PolicyAdapter
from openplatform_sdk.codec import MAX_MESSAGE_BYTES, MIN_MESSAGE_BYTES, CodecError, pack, unpack
from openplatform_sdk.protocol import (
    PROTOCOL_VERSION,
    PolicyError,
    is_identifier,
    validate_chunk,
    validate_request,
)

_LOGGER = logging.getLogger(__name__)


def _load_adapter(entrypoint: str, config: dict) -> PolicyAdapter:
    module, _, class_name = entrypoint.partition(":")
    if not class_name.isidentifier() or not all(part.isidentifier() for part in module.split(".")):
        raise ValueError("adapter entrypoint must be module:Class")
    adapter_dir = Path(config["adapter_dir"])
    if not adapter_dir.is_absolute() or not adapter_dir.is_dir():
        raise ValueError("adapter_dir must be an existing absolute directory")
    sys.path.insert(0, str(adapter_dir))
    adapter = getattr(importlib.import_module(module), class_name)(config)
    for name in ("reset", "update_obs", "get_action"):
        if not callable(getattr(adapter, name, None)):
            raise TypeError(f"adapter requires a callable {name}")
    return adapter


async def _read(connection: ServerConnection, max_size: int) -> dict:
    try:
        return unpack(await connection.recv(), max_size=max_size)
    except CodecError as error:
        raise PolicyError("invalid_request", str(error)) from error


async def _send_error(
    connection: ServerConnection, request: dict, error: PolicyError, max_size: int
) -> None:
    request_id = request.get("request_id")
    frame = {
        "request_id": request_id if is_identifier(request_id) else None,
        "error": {"code": error.code, "message": error.message[:256]},
    }
    await connection.send(pack(frame, max_size=max_size))


async def _run_session(
    connection: ServerConnection, adapter: PolicyAdapter, worker: ThreadPoolExecutor, max_size: int
) -> None:
    loop = asyncio.get_running_loop()
    episode_id = None
    observed = False
    while True:
        request = {}
        try:
            request = await _read(connection, max_size)
            validate_request(request)
            op = request["op"]
            if op == "hello":
                raise PolicyError("invalid_state", "session already started")
            if op in {"update_obs", "get_action"} and request["episode_id"] != episode_id:
                raise PolicyError("invalid_state", "reset is required for this episode")
            if op == "get_action" and not observed:
                raise PolicyError("invalid_state", "update_obs is required before get_action")
        except PolicyError as error:
            await _send_error(connection, request, error, max_size)
            continue

        result = None
        try:
            if op == "reset":
                await loop.run_in_executor(worker, adapter.reset)
                episode_id, observed = request["episode_id"], False
            elif op == "update_obs":
                await loop.run_in_executor(worker, adapter.update_obs, request["observation"])
                observed = True
            elif op == "get_action":
                result = await loop.run_in_executor(worker, adapter.get_action)
        except Exception as error:
            episode_id, observed = None, False
            _LOGGER.exception("Adapter failed in %s (request %s)", op, request["request_id"])
            await _send_error(
                connection,
                request,
                PolicyError("adapter_error", f"{type(error).__name__}: {error}"),
                max_size,
            )
            continue

        try:
            if op == "get_action":
                validate_chunk(result)
            frame = pack({"request_id": request["request_id"], "result": result}, max_size=max_size)
        except (CodecError, PolicyError) as error:
            episode_id, observed = None, False
            message = error.message if isinstance(error, PolicyError) else str(error)
            await _send_error(connection, request, PolicyError("invalid_action", message), max_size)
            continue
        await connection.send(frame)
        if op == "close":
            return


async def _serve_policy(entrypoint: str, config: dict, port: int, max_size: int) -> None:
    profile = config.get("io_profile")
    if not is_identifier(profile):
        raise ValueError("io_profile must be a nonempty string of up to 128 characters")
    stop = asyncio.Event()
    claimed = False

    async def handle(connection: ServerConnection) -> None:
        nonlocal claimed
        owns_session = False
        request = {}
        try:
            try:
                async with asyncio.timeout(5):
                    request = await _read(connection, max_size)
                validate_request(request)
                if request["op"] != "hello":
                    raise PolicyError("invalid_state", "hello is required first")
                if request["version"] != PROTOCOL_VERSION:
                    raise PolicyError("version_mismatch", f"expected protocol {PROTOCOL_VERSION}")
                if request["io_profile"] != profile:
                    raise PolicyError("profile_mismatch", f"expected profile {profile}")
                if claimed:
                    raise PolicyError("session_busy", "the policy already has a client")
            except (PolicyError, TimeoutError) as error:
                if isinstance(error, TimeoutError):
                    error = PolicyError("timeout", "waiting for hello")
                await _send_error(connection, request, error, max_size)
                return

            claimed = owns_session = True
            info = {"version": PROTOCOL_VERSION, "io_profile": profile, "model": entrypoint}
            await connection.send(
                pack({"request_id": request["request_id"], "result": info}, max_size=max_size)
            )
            await _run_session(connection, adapter, worker, max_size)
        except ConnectionClosed:
            pass
        finally:
            if owns_session:
                stop.set()

    loop = asyncio.get_running_loop()
    with ThreadPoolExecutor(max_workers=1) as worker:
        adapter = await loop.run_in_executor(worker, _load_adapter, entrypoint, config)
        try:
            async with serve(
                handle, "127.0.0.1", port, max_size=max_size, compression=None, close_timeout=1
            ) as server:
                bound_port = server.sockets[0].getsockname()[1]
                print(f"OPENPLATFORM_POLICY_READY ws://127.0.0.1:{bound_port}", flush=True)
                await stop.wait()
        finally:
            close = getattr(adapter, "close", None)
            if callable(close):
                await loop.run_in_executor(worker, close)


def main() -> None:
    parser = argparse.ArgumentParser(description="Serve one OpenPlatform policy on loopback.")
    parser.add_argument("--entrypoint", required=True, help="Adapter as module:Class")
    parser.add_argument("--config", required=True, type=Path, help="Adapter configuration as JSON")
    parser.add_argument("--port", type=int, default=0, help="TCP port; 0 chooses a free port")
    parser.add_argument("--max-message-bytes", type=int, default=MAX_MESSAGE_BYTES)
    args = parser.parse_args()
    if not 0 <= args.port <= 65535 or args.max_message_bytes < MIN_MESSAGE_BYTES:
        parser.error(
            f"expected a port from 0 to 65535 and a message limit of at least {MIN_MESSAGE_BYTES} bytes"
        )
    try:
        config = json.loads(args.config.read_text(encoding="utf-8"))
        if not isinstance(config, dict):
            raise ValueError("adapter configuration must be a JSON object")
        asyncio.run(_serve_policy(args.entrypoint, config, args.port, args.max_message_bytes))
    except KeyboardInterrupt:
        return
    except Exception as error:
        parser.exit(1, f"Policy server: {type(error).__name__}: {error}\n")


if __name__ == "__main__":
    main()
