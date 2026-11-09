"""Run with the client environment's Python and pass the policy environment's Python."""

import argparse
import asyncio
import importlib.util
import json
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from tempfile import TemporaryDirectory
from urllib.parse import urlparse

import numpy as np
from openplatform_sdk import PolicyClient, PolicyError
from openplatform_sdk.codec import pack, unpack
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve
from websockets.exceptions import ConnectionClosedError

PROFILE = "toy-v1"
FIXTURES = Path(__file__).resolve().parent / "fixtures"


@asynccontextmanager
async def policy_process(python, directory):
    directory.mkdir()
    trace = directory / "calls.txt"
    config = directory / "config.json"
    config.write_text(
        json.dumps(
            {
                "checkpoint_path": None,
                "model_dir": str(FIXTURES),
                "adapter_dir": str(FIXTURES),
                "device": "cpu",
                "io_profile": PROFILE,
                "trace_path": str(trace),
            }
        ),
        encoding="utf-8",
    )
    log_path = directory / "server.stderr"
    with log_path.open("wb") as log:
        process = await asyncio.create_subprocess_exec(
            python,
            "-m",
            "openplatform_sdk.server",
            "--entrypoint",
            "toy_policy:ToyPolicy",
            "--config",
            str(config),
            "--port",
            "0",
            "--max-message-bytes",
            "4096",
            cwd=directory,
            stdout=asyncio.subprocess.PIPE,
            stderr=log,
        )
        try:
            async with asyncio.timeout(15):
                while line := await process.stdout.readline():
                    if line.startswith(b"OPENPLATFORM_POLICY_READY "):
                        url = line.decode().split()[1]
                        break
                else:
                    raise AssertionError(log_path.read_text(encoding="utf-8"))
            yield url, trace
            status = await asyncio.wait_for(process.wait(), timeout=5)
            assert status == 0, log_path.read_text(encoding="utf-8")
            endpoint = urlparse(url)
            try:
                _, writer = await asyncio.open_connection(endpoint.hostname, endpoint.port)
            except ConnectionRefusedError:
                print(
                    f"{directory.name}: policy PID {process.pid} exited; port {endpoint.port} closed"
                )
            else:
                writer.close()
                await writer.wait_closed()
                raise AssertionError(f"policy port still listening: {url}")
        finally:
            if process.returncode is None:
                process.kill()
                await process.wait()


async def expect_error(operation, code, *, op=None):
    try:
        await operation
    except PolicyError as error:
        assert error.code == code, error
        if op is not None:
            assert error.op == op and error.request_id, error
        return error
    raise AssertionError(f"expected policy error {code}")


async def check_bad_requests(url):
    hello = {"op": "hello", "version": 999, "io_profile": PROFILE, "request_id": "version-test"}
    async with connect(url, proxy=None) as connection:
        await connection.send(pack(hello))
        reply = unpack(await connection.recv())
        assert reply["request_id"] == "version-test"
        assert reply["error"]["code"] == "version_mismatch", reply

    async with connect(url, proxy=None) as connection:
        await connection.send(b"\xc1")
        reply = unpack(await connection.recv())
        assert reply["request_id"] is None and reply["error"]["code"] == "invalid_request", reply

    async with connect(url, proxy=None) as connection:
        await connection.send(b"x" * 4097)
        try:
            await connection.recv()
        except ConnectionClosedError as error:
            assert error.rcvd is not None and error.rcvd.code == 1009, error
        else:
            raise AssertionError("oversized frame should close the connection")

    await expect_error(
        PolicyClient.connect(url, io_profile="wrong-profile"), "profile_mismatch", op="hello"
    )


def observation(value, mode=None):
    return {
        "instruction": "toy task",
        "vision": {"head": np.full((2, 3, 3), value, dtype=np.uint8)},
        "state": {"joints": np.arange(6, dtype=">i2")[4::-2]},
        "metadata": {"mode": mode},
    }


def assert_chunk(chunk, resets, history, queries):
    expected = np.array([[resets, history], [queries, -1]], dtype=">f4")
    assert len(chunk) == 2
    for index, action in enumerate(chunk):
        assert action["joints"].dtype.str == ">f4" and action["joints"].shape == (2, 2)
        assert action["gripper"].dtype == np.uint8 and action["gripper"].shape == (1,)
        np.testing.assert_array_equal(
            action["joints"], expected if index == 0 else expected[:, ::-1]
        )
        np.testing.assert_array_equal(action["gripper"], [index])


async def check_rollout(url):
    async with await PolicyClient.connect(url, io_profile=PROFILE) as client:
        assert client.info == {"version": 1, "io_profile": PROFILE, "model": "toy_policy:ToyPolicy"}
        await expect_error(
            PolicyClient.connect(url, io_profile=PROFILE), "session_busy", op="hello"
        )
        await expect_error(client.get_action(step=0), "invalid_state")
        await client.reset("episode-a")
        await client.reset("episode-a")
        await expect_error(client.get_action(step=0), "invalid_state", op="get_action")
        await client.update_obs(observation(1), step=0)
        assert_chunk(await client.get_action(step=0), 2, 1, 1)
        await client.update_obs(observation(2), step=1)
        await client.update_obs(observation(3), step=2)
        assert_chunk(await client.get_action(step=3), 2, 123, 2)

        await client.reset("episode-b")
        await client.update_obs(observation(4, "error"), step=0)
        error = await expect_error(client.get_action(step=0), "adapter_error", op="get_action")
        assert "RuntimeError: toy inference failed" in str(error)
        await expect_error(client.get_action(step=1), "invalid_state", op="get_action")
        await client.reset("episode-b")
        await client.update_obs(observation(5, "invalid"), step=0)
        await expect_error(client.get_action(step=0), "invalid_action", op="get_action")
        await client.reset("episode-c")
        await client.update_obs(observation(6), step=0)
        assert_chunk(await client.get_action(step=0), 5, 6, 1)


async def check_bad_reply():
    async def send_wrong_id(connection):
        await connection.recv()
        await connection.send(pack({"request_id": "wrong", "result": None}))

    async with serve(send_wrong_id, "127.0.0.1", 0) as server:
        port = server.sockets[0].getsockname()[1]
        await expect_error(
            PolicyClient.connect(f"ws://127.0.0.1:{port}", io_profile=PROFILE),
            "protocol_error",
            op="hello",
        )


async def main(python):
    assert all(importlib.util.find_spec(name) is None for name in ("openplatform", "torch", "jax"))
    with TemporaryDirectory(prefix="openplatform-v02-") as temp:
        async with policy_process(python, Path(temp) / "rollout") as (url, trace):
            await check_bad_requests(url)
            await check_rollout(url)
        policy_prefix, *calls = trace.read_text(encoding="utf-8").splitlines()
        assert Path(policy_prefix).resolve() != Path(sys.prefix).resolve(), (
            "two isolated environments required"
        )
        assert calls[:8] == [
            "reset",
            "reset",
            "obs:1",
            "get_action",
            "obs:12",
            "obs:123",
            "get_action",
            "reset",
        ], calls
        assert calls[-1] == "close", calls
        print(f"isolated client={sys.prefix}; policy={policy_prefix}")
        print("rollout: dtype/shape, repeated resets, chunk updates, errors and recovery passed")

        async with policy_process(python, Path(temp) / "timeout") as (url, trace):
            client = await PolicyClient.connect(url, io_profile=PROFILE, timeout=0.5)
            async with client:
                await client.reset("slow-episode")
                await client.update_obs(observation(7, "slow"), step=0)
                await expect_error(client.get_action(step=0), "timeout", op="get_action")
                await expect_error(client.get_action(step=0), "closed")
        assert trace.read_text(encoding="utf-8").splitlines()[1:] == [
            "reset",
            "obs:7",
            "get_action",
            "close",
        ]
        print("timeout: one adapter call, no retry, connection closed")
    await check_bad_reply()
    print("malformed reply: mismatched request ID rejected")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--policy-python", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(main(args.policy_python.absolute()))
