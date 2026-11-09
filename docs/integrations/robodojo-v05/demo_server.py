"""Serve the pinned upstream joint-space demo policy through vla-eval."""

import numpy as np
from vla_eval.model_servers.predict import PredictModelServer
from vla_eval.model_servers.serve import run_server
from vla_eval.specs import DimSpec
from XPolicyLab.policy.demo_policy.model import Model

JOINT_ORDER = (
    ("left_arm_joint_state", 6),
    ("left_ee_joint_state", 1),
    ("right_arm_joint_state", 6),
    ("right_ee_joint_state", 1),
)


def pack_joint_action(action):
    components = []
    for name, width in JOINT_ORDER:
        value = np.asarray(action[name], dtype=np.float32)
        if value.shape != (width,):
            raise ValueError(f"{name} has shape {value.shape}, expected ({width},)")
        components.append(value)
    return np.concatenate(components)


class DemoServer(PredictModelServer):
    def __init__(self, env_config: str = "arx_x5") -> None:
        super().__init__(chunk_size=2)
        self.model = Model({"env_cfg_type": env_config, "action_type": "joint"})

    def get_action_spec(self):
        return {"actions": DimSpec("actions", 14, "absolute_joint_positions_with_grippers")}

    def get_observation_spec(self):
        return {}

    async def on_episode_start(self, config, ctx):
        await super().on_episode_start(config, ctx)
        self.model.reset()

    def predict(self, obs, ctx):
        self.model.update_obs(obs)
        return {
            "actions": np.stack([pack_joint_action(action) for action in self.model.get_action()])
        }


if __name__ == "__main__":
    run_server(DemoServer)
