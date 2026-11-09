"""Adapt the pinned EvalEnv client without replacing its evaluation loop."""

import asyncio
import os
import sys
import time
from contextlib import suppress
from functools import wraps
from pathlib import Path
from threading import Thread

from openplatform.benchmarks.robodojo.trace import (
    UNAVAILABLE,
    Trace,
    append,
    physics_step,
    record_frames,
    summary,
    wire_value,
)

JOINTS = {
    "left_arm_joint_state": (6,),
    "left_ee_joint_state": (1,),
    "right_arm_joint_state": (6,),
    "right_ee_joint_state": (1,),
}


def validate_chunk(chunk):
    import numpy as np

    if not isinstance(chunk, list) or not chunk:
        raise ValueError("expected a nonempty joint action chunk")
    for action in chunk:
        if set(action) != JOINTS.keys() or any(
            not isinstance(action[key], np.ndarray)
            or action[key].shape != shape
            or action[key].dtype.kind != "f"
            or not np.isfinite(action[key]).all()
            for key, shape in JOINTS.items()
        ):
            raise ValueError("expected finite named ARX X5 joint arrays of widths 6,1,6,1")
    return chunk


class SDKTransport:
    """Keep SDK WS heartbeats live while the synchronous simulator runs."""

    def __init__(self, *, url, profile, timeout):
        from openplatform_sdk import PolicyClient

        self.loop = asyncio.new_event_loop()
        self.thread = Thread(target=self.loop.run_forever, daemon=True)
        self.thread.start()
        try:
            self.client = self._run(PolicyClient.connect(url, io_profile=profile, timeout=timeout))
        except BaseException:
            self._stop()
            raise

    def _run(self, coroutine):
        return asyncio.run_coroutine_threadsafe(coroutine, self.loop).result()

    def call(self, method, obs, trace):
        if method == "reset":
            return self._run(self.client.reset(trace.episode_id, step=trace.step))
        if method == "update_obs":
            return self._run(self.client.update_obs(obs, step=trace.step))
        if method == "get_action":
            return self._run(self.client.get_action(step=trace.step))
        raise ValueError(f"unsupported single-environment RoboDojo call {method!r}")

    def _stop(self):
        self.loop.call_soon_threadsafe(self.loop.stop)
        self.thread.join()
        self.loop.close()

    def close(self):
        if not self.loop.is_closed():
            try:
                self._run(self.client.close())
            finally:
                self._stop()


class TracedClient:
    def __init__(self, trace, transport, *, bridge, exit_on_policy_error=None):
        self.trace, self.transport, self.bridge = trace, transport, bridge
        self.exit_on_policy_error = exit_on_policy_error

    def call(self, func_name, obs=None):
        trace = self.trace
        normalized = (
            wire_value(obs) if self.bridge or trace.policy_calls or trace.observations else obs
        )
        call_id = UNAVAILABLE
        if trace.policy_calls:
            fields = {"observation": summary(normalized)} if func_name == "update_obs" else {}
            call_id = trace.event("policy_call", method=func_name, **fields)
        try:
            if self.bridge:
                result = self.transport.call(func_name, normalized, trace)
            else:
                result = self.transport.call(func_name=func_name, obs=obs)
            if func_name == "update_obs":
                trace.observation_call = call_id
                if trace.observations:
                    images = trace.save_images(normalized, trace.seq + 1)
                    trace.event("observation_images", policy_call_id=call_id, file=images)
            if func_name == "get_action":
                validate_chunk(result)
                trace.chunk += 1
                trace.chunk_index = 0
                trace.action_call = call_id
                if trace.policy_calls:
                    trace.event(
                        "chunk",
                        chunk=trace.chunk,
                        policy_call_id=call_id,
                        observation_call_id=trace.observation_call,
                        actions=summary(result),
                    )
            return result
        except Exception as error:
            trace.event("policy_error", method=func_name, policy_call_id=call_id, error=str(error))
            if self.bridge:
                append(
                    trace.directory / "bridge-error.jsonl",
                    {"kind": "policy", "message": str(error)},
                )
                with suppress(Exception):
                    self.close()
                if self.exit_on_policy_error is not None:
                    self.exit_on_policy_error(1)
                raise SystemExit(1) from error
            raise

    def close(self):
        self.transport.close()
        self.trace.event("policy_close")


def instrument_environment(
    module,
    trace,
    *,
    bridge,
    profile="robodojo-arx-x5-joint",
    timeout=120,
    videos=True,
    exit_on_policy_error=None,
):
    """Install the replacement at the constructor's WsModelClient call."""
    native_create = module.create_eval_env
    native_client = module.WsModelClient

    def client(**kwargs):
        if bridge:
            transport = SDKTransport(url=kwargs["url"], profile=profile, timeout=timeout)
            trace.event(
                "policy_connected",
                profile=transport.client.info["io_profile"],
                sdk_path=sys.modules["openplatform_sdk"].__file__,
            )
        else:
            transport = native_client(**kwargs)
            trace.event("policy_connected", profile="XPolicyLab")
        return TracedClient(
            trace, transport, bridge=bridge, exit_on_policy_error=exit_on_policy_error
        )

    @wraps(native_create)
    def create(*args, **kwargs):
        module.WsModelClient = client
        try:
            env = native_create(*args, **kwargs)
        finally:
            module.WsModelClient = native_client

        reset, take_action, run_eval = env.reset, env.take_action, env.run_eval
        if not videos:
            env._stream_vision = lambda env_idx, frame: None
        elif trace.actions and hasattr(env, "_stream_vision"):
            env._stream_vision = record_frames(env, env._stream_vision, trace)

        @wraps(reset)
        def reset_recorded(seed=None, **options):
            trace.begin(seed)
            try:
                return reset(seed=seed, **options)
            except Exception as error:
                trace.event("reset_error", error_type=type(error).__name__, error=str(error))
                raise

        @wraps(take_action)
        def take_recorded(action):
            before = env.take_action_cnt[0]
            start_step = physics_step(env) if trace.actions else UNAVAILABLE
            start_ns = time.monotonic_ns() if trace.actions else None
            returned = take_action(action)
            after = env.take_action_cnt[0]
            if after > before:
                if trace.actions:
                    trace.event(
                        "executed_action",
                        control_step=before,
                        completed_control_step=after,
                        chunk=trace.chunk,
                        index=trace.chunk_index,
                        policy_call_id=trace.action_call,
                        observation_call_id=trace.observation_call,
                        physics_step_before=start_step,
                        physics_step_after=physics_step(env),
                        elapsed_ns=time.monotonic_ns() - start_ns,
                        action=summary(action),
                    )
                trace.step = after
                trace.chunk_index += 1
            return returned

        @wraps(run_eval)
        def eval_recorded():
            count = env.success_nums + env.fail_nums
            try:
                returned = run_eval()
            except Exception as error:
                trace.event("eval_error", error_type=type(error).__name__, error=str(error))
                raise
            after = env.success_nums + env.fail_nums
            if after > count:
                result = wire_value(env.eval_result["details"][after - 1])
                trace.event(
                    "episode_result",
                    result=result,
                    native_result_index=after - 1,
                    step_limit=int(env.step_lim),
                    termination=(
                        "success"
                        if result["success"]
                        else "step_limit"
                        if trace.step >= env.step_lim
                        else "unavailable"
                    ),
                )
            else:
                trace.event("episode_excluded", unstable_count=env.unstable_nums)
            return returned

        env.reset, env.take_action, env.run_eval = reset_recorded, take_recorded, eval_recorded
        return env

    module.create_eval_env = create


def install_from_environment(module):
    def enabled(option, default="1"):
        return os.environ.get(f"OPENPLATFORM_RD_{option}", default) == "1"

    trace = Trace(
        Path(os.environ["OPENPLATFORM_RD_EVIDENCE"]),
        trace=enabled("TRACE"),
        policy_calls=enabled("POLICY_CALLS"),
        observations=enabled("OBSERVATIONS", "0"),
    )
    instrument_environment(
        module,
        trace,
        bridge=os.environ["OPENPLATFORM_RD_MODE"] == "bridge",
        profile=os.environ.get("OPENPLATFORM_RD_PROFILE", "robodojo-arx-x5-joint"),
        timeout=float(os.environ.get("OPENPLATFORM_RD_RPC_TIMEOUT", "120")),
        videos=enabled("VIDEOS"),
        # This hook runs in the owned simulator subprocess. Kit's implicit
        # shutdown can stall with open video pipes after a policy exception.
        exit_on_policy_error=os._exit,
    )
