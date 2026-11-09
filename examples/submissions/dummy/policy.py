import numpy as np


class Model:
    def __init__(self, config):
        self.fail_inference = config.get("fail_inference", False)

    def reset(self):
        self.observation = None

    def update_obs(self, observation):
        self.observation = observation

    def get_action(self):
        if self.fail_inference:
            raise RuntimeError("injected dummy inference error")
        return [{"delta": np.zeros(1, dtype=np.float32)} for _ in range(2)]
