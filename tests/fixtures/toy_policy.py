import importlib.util
import sys
import time
from pathlib import Path

import numpy as np


class ToyPolicy:
    def __init__(self, config):
        assert config["checkpoint_path"] is None
        assert config["device"] == "cpu"
        assert config["io_profile"] == "toy-v1"
        assert config["model_dir"] == config["adapter_dir"]
        assert all(
            importlib.util.find_spec(name) is None for name in ("openplatform", "torch", "jax")
        )
        self.trace = Path(config["trace_path"])
        self.reset_count = 0
        self._record(sys.prefix)

    def _record(self, event):
        with self.trace.open("a", encoding="utf-8") as stream:
            stream.write(f"{event}\n")

    def reset(self):
        self.reset_count += 1
        self.history = 0
        self.queries = 0
        self._record("reset")

    def update_obs(self, observation):
        image = observation["vision"]["head"]
        joints = observation["state"]["joints"]
        assert image.dtype == np.uint8 and image.shape == (2, 3, 3)
        assert joints.dtype.str == ">i2" and joints.shape == (3,)
        np.testing.assert_array_equal(joints, [4, 2, 0])
        self.history = 10 * self.history + int(image[0, 0, 0])
        self.mode = observation["metadata"].get("mode")
        self._record(f"obs:{self.history}")

    def get_action(self):
        self._record("get_action")
        self.queries += 1
        if self.mode == "slow":
            time.sleep(1)
        if self.mode == "error":
            raise RuntimeError("toy inference failed")
        if self.mode == "invalid":
            return [{"joints": np.array([np.nan])}]
        matrix = np.array([[self.reset_count, self.history], [self.queries, -1]], dtype=">f4")
        return [
            {"joints": matrix, "gripper": np.array([0], dtype=np.uint8)},
            {"joints": matrix[:, ::-1], "gripper": np.array([1], dtype=np.uint8)},
        ]

    def close(self):
        self._record("close")
