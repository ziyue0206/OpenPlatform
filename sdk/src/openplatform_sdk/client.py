import asyncio
from inspect import signature
from math import isfinite
from uuid import uuid4

from websockets.exceptions import WebSocketException

try:
    from websockets.asyncio.client import ClientConnection, connect
except ImportError:  # RoboDojo's simulator pins websockets 12.
    from websockets.legacy.client import WebSocketClientProtocol as ClientConnection
    from websockets.legacy.client import connect

from openplatform_sdk.adapter import ActionChunk, Observation
from openplatform_sdk.codec import MAX_MESSAGE_BYTES, MIN_MESSAGE_BYTES, CodecError, pack, unpack
from openplatform_sdk.protocol import (
    PROTOCOL_VERSION,
    PolicyError,
    validate_chunk,
    validate_request,
)


class PolicyClient:
    """One sequential policy session on the caller's asyncio event loop."""

    def __init__(self, connection: ClientConnection, timeout: float, max_size: int):
        self._connection = connection
        self._timeout = timeout
        self._max_size = max_size
        self._episode_id: str | None = None
        self._closed = False
        self._in_flight = False
        self.info: dict = {}

    @classmethod
    async def connect(
        cls,
        url: str,
        *,
        io_profile: str,
        timeout: float = 30,
        max_message_bytes: int = MAX_MESSAGE_BYTES,
    ) -> "PolicyClient":
        if not isfinite(timeout) or timeout <= 0 or max_message_bytes < MIN_MESSAGE_BYTES:
            raise ValueError(
                f"expected a positive timeout and a message limit of at least {MIN_MESSAGE_BYTES} bytes"
            )
        try:
            connection = await connect(
                url,
                open_timeout=timeout,
                close_timeout=min(timeout, 1),
                max_size=max_message_bytes,
                compression=None,
                **({"proxy": None} if "proxy" in signature(connect).parameters else {}),
            )
        except TimeoutError as error:
            raise PolicyError("timeout", "opening policy connection") from error
        except (OSError, WebSocketException) as error:
            raise PolicyError("transport_error", f"opening policy connection: {error}") from error

        client = cls(connection, timeout, max_message_bytes)
        try:
            info = await client._call("hello", version=PROTOCOL_VERSION, io_profile=io_profile)
            if (
                not isinstance(info, dict)
                or type(info.get("version")) is not int
                or info["version"] != PROTOCOL_VERSION
                or info.get("io_profile") != io_profile
                or not isinstance(info.get("model"), str)
            ):
                raise PolicyError("protocol_error", "invalid hello result")
            client.info = info
        except (PolicyError, asyncio.CancelledError):
            await client._disconnect()
            raise
        return client

    async def reset(self, episode_id: str, *, step: int = 0) -> None:
        await self._call("reset", episode_id=episode_id, step=step)
        self._episode_id = episode_id

    async def update_obs(self, observation: Observation, *, step: int) -> None:
        await self._call(
            "update_obs", episode_id=self._require_episode(), step=step, observation=observation
        )

    async def get_action(self, *, step: int) -> ActionChunk:
        return await self._call("get_action", episode_id=self._require_episode(), step=step)

    def _require_episode(self) -> str:
        if self._closed:
            raise PolicyError("closed", "policy session is closed")
        if self._episode_id is None:
            raise PolicyError("invalid_state", "reset is required first")
        return self._episode_id

    async def _call(self, op: str, **fields):
        if self._closed:
            raise PolicyError("closed", "policy session is closed", op=op)
        if self._in_flight:
            raise PolicyError("session_busy", "another request is in flight", op=op)

        request_id = uuid4().hex
        request = {"request_id": request_id, "op": op, **fields}
        try:
            validate_request(request)
            frame = pack(request, max_size=self._max_size)
        except (PolicyError, CodecError) as error:
            message = error.message if isinstance(error, PolicyError) else str(error)
            raise PolicyError("invalid_request", message, op=op, request_id=request_id) from error

        self._in_flight = True
        try:
            async with asyncio.timeout(self._timeout):
                await self._connection.send(frame)
                reply = unpack(await self._connection.recv(), max_size=self._max_size)
            if reply.get("request_id") != request_id:
                raise CodecError("response request_id does not match")
            if set(reply) == {"request_id", "result"}:
                result = reply["result"]
                if op == "get_action":
                    try:
                        validate_chunk(result)
                    except PolicyError as error:
                        raise CodecError(str(error)) from error
                elif op != "hello" and result is not None:
                    raise CodecError(f"unexpected result for {op}")
                return result
            remote = reply.get("error")
            if (
                set(reply) != {"request_id", "error"}
                or not isinstance(remote, dict)
                or not all(isinstance(remote.get(key), str) for key in ("code", "message"))
            ):
                raise CodecError("malformed response")
            raise PolicyError(remote["code"], remote["message"], op=op, request_id=request_id)
        except TimeoutError as error:
            await self._disconnect()
            raise PolicyError(
                "timeout", "policy response deadline exceeded", op=op, request_id=request_id
            ) from error
        except (OSError, WebSocketException, CodecError) as error:
            await self._disconnect()
            code = "protocol_error" if isinstance(error, CodecError) else "transport_error"
            raise PolicyError(code, str(error), op=op, request_id=request_id) from error
        except asyncio.CancelledError:
            await self._disconnect()
            raise
        finally:
            self._in_flight = False

    async def _disconnect(self) -> None:
        if not self._closed:
            self._closed = True
            await self._connection.close()

    async def close(self) -> None:
        if self._closed:
            return
        try:
            await self._call("close")
        finally:
            await self._disconnect()

    async def __aenter__(self) -> "PolicyClient":
        return self

    async def __aexit__(self, exc_type, exc, traceback) -> None:
        if exc_type is None:
            await self.close()
        else:
            await self._disconnect()
