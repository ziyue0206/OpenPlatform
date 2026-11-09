"""Compare the pinned π0 model over the two actual transports on fixed input."""

import argparse
import asyncio
import json
import os
import socket
import sys
import time
from pathlib import Path
from threading import Event

import numpy as np
from openplatform_sdk import PolicyClient

from openplatform.benchmarks.robodojo.bridge import JOINTS, validate_chunk
from openplatform.benchmarks.robodojo.reference_policy import trace_model
from openplatform.benchmarks.robodojo.trace import append
from openplatform.runner import _policy_url
from openplatform.runtime import ExecutionSpec, NativeRuntime

PROFILE = "robodojo-arx-x5-joint"
CHECKPOINT = "RoboDojo-sim-arx_x5-joint-0"
STARTUP_TIMEOUT = 900


def fixture(step):
    obs = {"instruction": "Stack the bowls.", "env_idx": 0, "state": {}, "vision": {}}
    for key, values in zip(JOINTS, np.split(np.arange(14) * 0.01, [6, 7, 13]), strict=True):
        obs["state"][key] = (values + 0.01 * step).astype(np.float32)
    image = np.arange(480 * 640 * 3, dtype=np.uint32).reshape(480, 640, 3)
    for i, key in enumerate(("cam_head", "cam_left_wrist", "cam_right_wrist")):
        obs["vision"][key] = {"color": (image + 41 * i + 17 * step).astype(np.uint8)}
    return obs


def official_server(checkout, port, directory):
    from XPolicyLab.policy.Pi_0 import model
    from XPolicyLab.setup_policy_server import main, parse_args_and_config

    original = model.Model.get_model

    def get_model(instance, model_cfg):
        checkpoint = model._resolve_pi_model_root(model_cfg).resolve()
        append(
            directory / "native-policy-config.jsonl",
            {
                **model_cfg,
                "resolved_checkpoint": str(checkpoint),
            },
        )
        return original(instance, model_cfg)

    init = model.Model.__init__

    def record_init(instance, config):
        init(instance, config)
        trace_model(instance, directory / "model-boundary.jsonl")

    model.Model.get_model = get_model
    model.Model.__init__ = record_init
    sys.argv = [
        "setup_policy_server.py",
        "--config_path",
        str(checkout / "XPolicyLab/policy/Pi_0/deploy.yml"),
        "--overrides",
        f"port={port}",
        "host=127.0.0.1",
        "bench_name=RoboDojo",
        "task_name=stack_bowls",
        f"ckpt_name={CHECKPOINT}",
        "env_cfg_type=arx_x5",
        "seed=0",
        "policy_name=Pi_0",
        "action_type=joint",
        "action_dim=14",
    ]
    main(parse_args_and_config())


def wait_tcp(handle, port):
    deadline = time.monotonic() + STARTUP_TIMEOUT
    while not handle.done() and time.monotonic() < deadline:
        try:
            with socket.create_connection(("127.0.0.1", port), timeout=0.5):
                return
        except OSError:
            time.sleep(0.25)
    raise RuntimeError("official policy failed to listen; see replay server logs")


def native_actions(url):
    from client_server.ws.model_client import WsModelClient

    with WsModelClient(
        url=url,
        evaluation_id="v06-replay",
        trial_id="v06-replay",
        action_case_id="stack_bowls_case",
        request_timeout_s=300,
    ) as client:
        client.call(func_name="reset")
        client.call(func_name="reset")
        output = []
        for step in range(2):
            client.call(func_name="update_obs", obs=fixture(step))
            output.append(client.call(func_name="get_action"))
    return output


async def sdk_actions(url):
    async with await PolicyClient.connect(url, io_profile=PROFILE, timeout=300) as client:
        await client.reset("v06-replay")
        await client.reset("v06-replay")
        output = []
        for step in range(2):
            await client.update_obs(fixture(step), step=step)
            output.append(await client.get_action(step=step))
        return output


def run_route(route, checkout, checkpoint, adapter, evidence):
    out = evidence / route
    out.mkdir()
    runtime = NativeRuntime()
    if route == "official":
        with socket.socket() as listener:
            listener.bind(("127.0.0.1", 0))
            port = listener.getsockname()[1]
        argv = [sys.executable, Path(__file__).resolve(), "server", checkout, out, str(port)]
    else:
        config = out / "config.json"
        config.write_text(
            json.dumps(
                {
                    "model_dir": str(checkout),
                    "adapter_dir": str(adapter),
                    "policy": "Pi_0",
                    "checkpoint_path": str(checkpoint),
                    "io_profile": PROFILE,
                    "evidence_dir": str(out),
                }
            )
        )
        argv = [
            sys.executable,
            "-m",
            "openplatform_sdk.server",
            "--entrypoint",
            "policy:ReferencePolicy",
            "--config",
            config,
        ]
    (out / "command.json").write_text(json.dumps(list(map(str, argv)), indent=2))
    handle = runtime.launch(
        ExecutionSpec(
            argv=argv,
            cwd=checkout,
            stdout_path=out / "policy.stdout.log",
            stderr_path=out / "policy.stderr.log",
            timeout=1800,
            env={"PYTHONUNBUFFERED": "1"},
        )
    )
    try:
        if route == "official":
            wait_tcp(handle, port)
            chunks = native_actions(f"ws://127.0.0.1:{port}")
        else:
            chunks = asyncio.run(sdk_actions(_policy_url(handle, Event(), STARTUP_TIMEOUT)))
            result = runtime.wait(handle)
            if result.returncode != 0:
                raise RuntimeError(f"SDK replay policy exited {result.returncode}")
    finally:
        if not handle.done():
            runtime.stop(handle)
    actions = [
        np.stack(
            [np.concatenate([action[key] for key in JOINTS]) for action in validate_chunk(chunk)]
        )
        for chunk in chunks
    ]
    np.savez(out / "actions.npz", *actions)
    return actions


def compare_replay(checkout, checkpoint, adapter, evidence):
    evidence.mkdir()
    if os.environ.get("CUDA_VISIBLE_DEVICES") != "1":
        raise RuntimeError("this paired replay requires physical policy GPU 1")
    a = run_route("official", checkout, checkpoint, adapter, evidence)
    b = run_route("openplatform", checkout, checkpoint, adapter, evidence)
    left = evidence / "official/model-boundary.jsonl"
    right = evidence / "openplatform/model-boundary.jsonl"
    if not left.stat().st_size or left.read_bytes() != right.read_bytes():
        raise ValueError("encoded or normalized model inputs differ")
    for x, y in zip(a, b, strict=True):
        if x.shape != y.shape or x.dtype != y.dtype:
            raise ValueError("action chunk dtype or shape differs")
        np.testing.assert_allclose(x, y, rtol=1e-5, atol=1e-5)
    report = {
        "execution_status": "succeeded",
        "comparison_status": "paired",
        "encoded_and_normalized_inputs": "identical",
        "action_chunk_shapes": [list(x.shape) for x in a],
        "dtype": str(a[0].dtype),
        "atol": 1e-5,
        "rtol": 1e-5,
        "max_absolute_action_delta": max(
            float(np.max(np.abs(x - y))) for x, y in zip(a, b, strict=True)
        ),
        "joint_order": list(JOINTS),
    }
    (evidence / "comparison.json").write_text(json.dumps(report, indent=2) + "\n")
    print(json.dumps(report, indent=2), flush=True)


if __name__ == "__main__":
    if sys.argv[1:2] == ["server"]:
        official_server(Path(sys.argv[2]), int(sys.argv[4]), Path(sys.argv[3]))
    else:
        parser = argparse.ArgumentParser()
        for name in ("checkout", "checkpoint", "adapter", "evidence"):
            parser.add_argument(name, type=Path)
        args = parser.parse_args()
        compare_replay(args.checkout, args.checkpoint, args.adapter, args.evidence)
