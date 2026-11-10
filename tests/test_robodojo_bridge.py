import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import numpy as np
import pytest

from openplatform.benchmarks.robodojo.bridge import JOINTS, instrument_environment, validate_chunk
from openplatform.benchmarks.robodojo.episode_index import read_events, write_episode_index
from openplatform.benchmarks.robodojo.trace import Trace, summary, wire_value
from openplatform.runner import _policy_url
from openplatform.runtime import ExecutionSpec, NativeRuntime


def test_named_action_contract_and_native_map_keys():
    keys = tuple(JOINTS)
    action = {
        key: np.arange(start, start + shape[0], dtype=np.float32)
        for (key, shape), start in zip(JOINTS.items(), (0, 6, 7, 13), strict=True)
    }
    assert validate_chunk([action]) == [action]
    assert np.concatenate([action[key] for key in keys]).tolist() == list(range(14))
    with pytest.raises(ValueError, match="named ARX"):
        validate_chunk([{**action, "left_ee_joint_state": np.zeros(6, np.float32)}])
    with pytest.raises(ValueError, match="duplicate"):
        wire_value({0: {}, "0": {}})
    assert wire_value({0: {"shape": (2, 3), "n": np.int64(3)}}) == {"0": {"shape": [2, 3], "n": 3}}


@pytest.mark.parametrize("steps", [None, 24])
def test_smoke_budget_applies_only_when_explicit(tmp_path, steps):
    env = SimpleNamespace(
        step_lim=800, reset=lambda: None, take_action=lambda _: None, run_eval=lambda: None
    )
    module = SimpleNamespace(create_eval_env=lambda: env, WsModelClient=object)
    trace = Trace(tmp_path)
    instrument_environment(module, trace, bridge=False, videos=False, smoke_control_steps=steps)
    assert module.create_eval_env().step_lim == (800 if steps is None else steps)
    events = read_events(tmp_path / "events.jsonl")
    if steps is None:
        assert events == []
    else:
        (budget,) = events
        assert budget["kind"] == "smoke_control_budget"
        assert (budget["native"], budget["requested"]) == (800, steps)


@pytest.mark.parametrize("collect", [False, True])
def test_pinned_deploy_replay_over_sdk(tmp_path, collect):
    upstream = os.environ.get("OPENPLATFORM_TEST_ROBODOJO")
    if not upstream:
        pytest.skip("set OPENPLATFORM_TEST_ROBODOJO to the V05 pinned checkout")
    deploy = runpy.run_path(str(Path(upstream) / "XPolicyLab/policy/demo_policy/deploy.py"))[
        "eval_one_episode"
    ]
    profile = "robodojo-arx-x5-joint"
    record = tmp_path / "policy-boundary.jsonl"
    config = tmp_path / "config.json"
    config.write_text(
        json.dumps(
            {
                "adapter_dir": str(Path(__file__).parent / "fixtures"),
                "io_profile": profile,
                "record": str(record),
            }
        )
    )
    runtime = NativeRuntime()
    handle = runtime.launch(
        ExecutionSpec(
            argv=[
                sys.executable,
                "-m",
                "openplatform_sdk.server",
                "--entrypoint",
                "robodojo_policy:FixturePolicy",
                "--config",
                config,
            ],
            cwd=tmp_path,
            stdout_path=tmp_path / "stdout",
            stderr_path=tmp_path / "stderr",
        )
    )
    observed = []

    class Env:
        step_lim = 4
        success_nums = fail_nums = unstable_nums = 0

        def __init__(self, client):
            self.model_client = client
            self.taken = []
            self.take_action_cnt = [0]
            self.end_flag = [False]
            self.sim = SimpleNamespace(_sim_step_counter=200)
            self.video_writers = {0: {"cam_head": SimpleNamespace(n_frames=0)}}

        def reset(self, seed=None):
            self.model_client.call(func_name="reset")

        def get_obs(self):
            rgb = np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
            obs = {
                "instruction": "Stack the bowls",
                "vision": {"cam_head": {"color": rgb, "shape": rgb.shape}},
                "state": {
                    key: np.full(shape, i + len(self.taken), dtype=np.float32)
                    for i, (key, shape) in enumerate(JOINTS.items())
                },
                "additional_info": {0: {"control_step": len(self.taken)}},
                "env_idx": 0,
            }
            self._stream_vision(0, obs)
            if not self.end_flag[0]:
                observed.append(summary(wire_value(obs)))
            return obs

        def _stream_vision(self, env_idx, frame):
            self.video_writers[env_idx]["cam_head"].n_frames += 1

        def take_action(self, action):
            self.taken.append(np.concatenate([action[key] for key in JOINTS]).tolist())
            self.take_action_cnt[0] += 1
            self.sim._sim_step_counter += 10
            if self.is_episode_end():
                self.end_flag[0] = True
                self.get_obs()

        def is_episode_end(self):
            return len(self.taken) >= self.step_lim

        def run_eval(self):
            deploy(self, self.model_client)
            self.fail_nums += 1
            self.eval_result = {"details": {0: {"layout_id": 7, "success": False, "score": 0}}}

    def native_client(**kwargs):
        pytest.fail("bridge tried to use the original transport")

    module = SimpleNamespace(WsModelClient=native_client)
    try:
        url = _policy_url(handle, Event(), 10)
        module.create_eval_env = lambda: Env(module.WsModelClient(url=url))
        trace = Trace(tmp_path, trace=collect, policy_calls=collect, observations=collect)
        instrument_environment(module, trace, bridge=True, profile=profile)
        env = module.create_eval_env()
        assert module.WsModelClient is native_client
        env.reset(seed=[7])
        env.run_eval()
        env.model_client.close()
        assert runtime.wait(handle).returncode == 0
    finally:
        if not handle.done():
            runtime.stop(handle)

    policy = [json.loads(line) for line in record.read_text().splitlines()]
    assert [row["method"] for row in policy] == [
        "reset",
        "reset",
        "update_obs",
        "get_action",
        "update_obs",
        "update_obs",
        "update_obs",
        "get_action",
    ]
    assert [row["obs"] for row in policy if "obs" in row] == observed
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [action[0] for action in env.taken] == [100, 120, 140, 200]
    assert next(e for e in events if e["kind"] == "episode_result")["termination"] == "step_limit"
    assert env.video_writers[0]["cam_head"].n_frames == 5
    if not collect:
        assert {"policy_call", "chunk", "executed_action", "video_frame"}.isdisjoint(
            e["kind"] for e in events
        )
        assert not (tmp_path / "observations").exists()
        return

    chunks = [e for e in events if e["kind"] == "chunk"]
    taken = [e for e in events if e["kind"] == "executed_action"]
    frames = [e for e in events if e["kind"] == "video_frame"]
    images = [e for e in events if e["kind"] == "observation_images"]
    assert [len(e["actions"]) for e in chunks] == [3, 3]
    assert [(e["chunk"], e["index"]) for e in taken] == [(1, 0), (1, 1), (1, 2), (2, 0)]
    calls = {e["seq"]: e for e in events if e["kind"] == "policy_call"}
    for action in taken:
        assert calls[action["policy_call_id"]]["method"] == "get_action"
        assert calls[action["observation_call_id"]]["method"] == "update_obs"
        before = frames[action["control_step"]]
        after = frames[action["completed_control_step"]]
        assert before["physics_step"] == action["physics_step_before"]
        assert after["physics_step"] == action["physics_step_after"]
    assert [f["frames"]["cam_head"] for f in frames] == list(range(5))
    assert frames[-1]["terminal"] and frames[-1]["control_step"] == 4
    assert len(images) == 4
    with np.load(tmp_path / images[0]["file"]) as image:
        np.testing.assert_array_equal(
            image["cam_head"], np.arange(18, dtype=np.uint8).reshape(2, 3, 3)
        )


def test_partial_episode_index_keeps_native_denominator(tmp_path):
    trace = Trace(tmp_path)
    trace.begin([4])
    trace.event("episode_result", native_result_index=0)
    trace.begin([5])
    trace.event("policy_error")
    with (tmp_path / "events.jsonl").open("a") as stream:
        stream.write('{"kind":')
    events = read_events(tmp_path / "events.jsonl")
    write_episode_index(tmp_path, events, {"eval_time": 1}, "canceled")
    result = json.loads((tmp_path / "episode-index.json").read_text())
    assert result["native_scored_episodes"] == 1
    assert [e["status"] for e in result["episodes"]] == ["completed", "errored"]
    assert result["episodes"][1]["cameras"] == "unavailable"


def test_policy_fault_escapes_simulator_shutdown_after_writing_diagnostic(tmp_path):
    code = """
import atexit
import os
import sys
from pathlib import Path
from types import SimpleNamespace

from openplatform.benchmarks.robodojo.bridge import TracedClient
from openplatform.benchmarks.robodojo.trace import Trace

directory = Path(sys.argv[1])
atexit.register(lambda: (directory / 'implicit-shutdown').touch())

def fail(*args):
    raise RuntimeError('injected failure')

transport = SimpleNamespace(call=fail, close=lambda: None)
client = TracedClient(Trace(directory), transport, bridge=True, exit_on_policy_error=os._exit)
client.call('get_action')
"""
    run = subprocess.run([sys.executable, "-c", code, tmp_path], timeout=5, check=False)
    assert run.returncode == 1
    assert not (tmp_path / "implicit-shutdown").exists()
    error = json.loads((tmp_path / "bridge-error.jsonl").read_text())
    assert error == {"kind": "policy", "message": "injected failure"}
    assert [e["kind"] for e in read_events(tmp_path / "events.jsonl")] == [
        "policy_call",
        "policy_error",
        "policy_close",
    ]
