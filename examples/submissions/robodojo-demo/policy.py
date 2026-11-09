"""Use RoboDojo's zero-action model through the four-method SDK adapter."""

from XPolicyLab.policy.demo_policy.model import Model


class ReferencePolicy:
    def __init__(self, config):
        if config["checkpoint_path"] is not None:
            raise ValueError("demo_policy requires checkpoint: null")
        self.model = Model({"env_cfg_type": "arx_x5", "action_type": "joint"})

    def reset(self):
        self.model.reset()

    def update_obs(self, observation):
        self.model.update_obs(observation)

    def get_action(self):
        return self.model.get_action()
