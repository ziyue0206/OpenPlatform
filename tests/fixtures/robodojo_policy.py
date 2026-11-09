import numpy as np

from openplatform.benchmarks.robodojo.bridge import JOINTS
from openplatform.benchmarks.robodojo.trace import append, summary


class FixturePolicy:
    def __init__(self, config):
        from pathlib import Path

        self.path = Path(config["record"])
        self.inference = 0

    def reset(self):
        append(self.path, {"method": "reset"})

    def update_obs(self, obs):
        append(self.path, {"method": "update_obs", "obs": summary(obs)})

    def get_action(self):
        self.inference += 1
        append(self.path, {"method": "get_action"})
        actions = []
        for index in range(3):
            value = self.inference * 100 + index * 20
            action = {}
            for key, shape in JOINTS.items():
                action[key] = np.arange(value, value + shape[0], dtype=np.float32)
                value += shape[0]
            actions.append(action)
        return actions
