"""Compact observations and exact action evidence at the native control boundary."""

import hashlib
import json
import os
import sys
import time
from pathlib import Path

import numpy as np

UNAVAILABLE = "unavailable"


def wire_value(value):
    """Match XPolicyLab's array and integer-map-key handling."""
    torch = sys.modules.get("torch")
    if torch is not None and isinstance(value, torch.Tensor):
        value = value.detach().cpu()
        value = value.float().numpy() if value.dtype == torch.bfloat16 else value.numpy()
    if isinstance(value, dict):
        result = {}
        for key, child in value.items():
            key = str(key) if type(key) is int else key
            if key in result:
                raise ValueError(f"duplicate RoboDojo observation key {key!r}")
            result[key] = wire_value(child)
        return result
    if isinstance(value, (tuple, list)):
        return [wire_value(child) for child in value]
    if isinstance(value, np.generic):
        return value.item()
    return value


def summary(value):
    if isinstance(value, np.ndarray):
        row = {
            "dtype": value.dtype.str,
            "shape": list(value.shape),
            "sha256": hashlib.sha256(value.tobytes()).hexdigest(),
        }
        if value.size <= 32:
            row["values"] = value.tolist()
        return row
    if isinstance(value, dict):
        return {key: summary(child) for key, child in value.items()}
    if isinstance(value, list):
        return [summary(child) for child in value]
    return value


def append(path: Path, record: dict) -> None:
    with path.open("a", encoding="utf-8") as stream:
        stream.write(json.dumps(record, allow_nan=False, sort_keys=True) + "\n")


class Trace:
    def __init__(self, directory: Path, *, trace=True, policy_calls=True, observations=False):
        self.directory = directory
        self.actions = trace
        self.policy_calls = policy_calls
        self.observations = observations
        self.layout = None
        self.episode = 0
        self.step = 0
        self.chunk = 0
        self.chunk_index = 0
        self.seq = 0
        self.run = int(os.environ.get("ROBODOJO_FATAL_RESTART_COUNT", 0))
        self.pid = os.getpid()
        self.action_call = self.observation_call = UNAVAILABLE

    def event(self, kind, **fields):
        self.seq += 1
        append(
            self.directory / "events.jsonl",
            {
                "kind": kind,
                "seq": self.seq,
                "episode": self.episode,
                "episode_id": self.episode_id if self.layout is not None else None,
                "layout_id": self.layout,
                "restart": self.run,
                "control_step": self.step,
                "monotonic_ns": time.monotonic_ns(),
                **fields,
            },
        )
        return self.seq

    def begin(self, seeds):
        self.episode += 1
        self.layout = int(seeds[0])
        self.step = self.chunk = self.chunk_index = 0
        self.action_call = self.observation_call = UNAVAILABLE
        self.event("episode_start")

    @property
    def episode_id(self):
        return f"{self.pid}-{self.run}-{self.episode}-{self.layout}"

    def save_images(self, observation, call_id):
        images = {
            key: camera["color"]
            for key, camera in observation.get("vision", {}).items()
            if isinstance(camera, dict)
            and isinstance(camera.get("color"), np.ndarray)
            and camera["color"].dtype.kind in "buif"
        }
        if not images:
            return UNAVAILABLE
        path = self.directory / "observations" / f"{self.episode_id}-{call_id}.npz"
        path.parent.mkdir(exist_ok=True)
        pending = path.with_name("." + path.name)
        np.savez(pending, **images)
        pending.replace(path)
        return path.relative_to(self.directory).as_posix()


def physics_step(env):
    step = getattr(getattr(env, "sim", None), "_sim_step_counter", None)
    return step if type(step) is int else UNAVAILABLE


def record_frames(env, stream, trace):
    def record(env_idx, frame):
        writers = env.video_writers.get(env_idx, {})
        before = {key: writer.n_frames for key, writer in writers.items()}
        stream(env_idx, frame)
        frames = {
            key: writer.n_frames - 1
            for key, writer in env.video_writers.get(env_idx, {}).items()
            if writer.n_frames > before.get(key, 0)
        }
        if frames:
            trace.event(
                "video_frame",
                control_step=env.take_action_cnt[env_idx],
                physics_step=physics_step(env),
                frames=frames,
                terminal=bool(env.end_flag[env_idx]),
                state=summary(wire_value(frame.get("state", UNAVAILABLE))),
            )

    return record
