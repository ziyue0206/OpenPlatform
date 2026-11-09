"""Acceptance-only probe for a partial chunk and an adapter exception."""

from openplatform.benchmarks.robodojo.reference_policy import ReferencePolicy


class ProbePolicy(ReferencePolicy):
    def __init__(self, config):
        super().__init__(config)
        self.fail_at = config.get("fail_at")
        self.calls = 0

    def reset(self):
        super().reset()
        self.calls = 0

    def get_action(self):
        self.calls += 1
        if self.calls == self.fail_at:
            raise RuntimeError("V07 injected model inference exception")
        first, second = super().get_action()
        return [first, second, first]
