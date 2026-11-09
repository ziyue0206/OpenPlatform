"""Delegate to the pinned XPolicyLab Model using the native configuration."""

from importlib import import_module
from pathlib import Path

import yaml

from openplatform.benchmarks.robodojo.trace import append, summary, wire_value


def trace_model(model, evidence):
    """Observe the native encoded input and the result of its input transforms."""
    original_infer = model.policy.infer
    original_transform = model.policy._input_transform
    inference = 0

    def record(kind, value):
        append(
            evidence, {"kind": kind, "inference": inference, "input": summary(wire_value(value))}
        )

    def traced_infer(inputs, **kwargs):
        nonlocal inference
        inference += 1
        record("encoded_input", inputs)
        return original_infer(inputs, **kwargs)

    def traced_transform(inputs):
        normalized = original_transform(inputs)
        record("normalized_input", normalized)
        return normalized

    model.policy.infer = traced_infer
    model.policy._input_transform = traced_transform


class ReferencePolicy:
    def __init__(self, config):
        name = config["policy"]
        if name not in {"demo_policy", "Pi_0"}:
            raise ValueError("expected demo_policy or Pi_0")
        root = Path(config["model_dir"])
        policy_dir = root / "XPolicyLab" / "policy" / name
        model_config = yaml.safe_load((policy_dir / "deploy.yml").read_text(encoding="utf-8"))
        model_config.update(
            bench_name="RoboDojo",
            task_name="stack_bowls",
            env_cfg_type="arx_x5",
            action_type="joint",
            seed=0,
            action_dim=14,
            policy_name=name,
        )
        checkpoint = config["checkpoint_path"]
        if name == "Pi_0":
            if checkpoint is None:
                raise ValueError("Pi_0 requires the published checkpoint step directory")
            model_config.update(checkpoint_path=checkpoint, ckpt_name="RoboDojo-sim-arx_x5-joint-0")
        elif checkpoint is not None:
            raise ValueError("demo_policy requires checkpoint: null")
        else:
            model_config["ckpt_name"] = "demo"

        module = import_module(f"XPolicyLab.policy.{name}.model")
        evidence = Path(config["evidence_dir"])
        if name == "Pi_0":
            resolved = module._resolve_pi_model_root(model_config).resolve()
            if resolved != Path(checkpoint).resolve():
                raise ValueError(f"unexpected checkpoint step selected: {resolved}")
            model_config["checkpoint_path"] = str(resolved)
        append(evidence / "native-policy-config.jsonl", model_config)
        self.model = module.Model(model_config)
        if name == "Pi_0" and config.get("collection", {}).get("policy_calls", True):
            trace_model(self.model, evidence / "model-boundary.jsonl")

    def reset(self):
        self.model.reset()

    def update_obs(self, observation):
        self.model.update_obs(observation)

    def get_action(self):
        return self.model.get_action()
