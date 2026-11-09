from typing import Any, Protocol

import numpy as np

Observation = dict[str, Any]
Action = dict[str, np.ndarray]
ActionChunk = list[Action]


class PolicyAdapter(Protocol):
    def __init__(self, config: dict[str, Any]) -> None: ...

    def reset(self) -> None: ...

    def update_obs(self, observation: Observation) -> None: ...

    def get_action(self) -> ActionChunk: ...
