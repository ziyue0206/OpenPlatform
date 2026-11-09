import argparse
import asyncio
import sys
from pathlib import Path

import numpy as np
from openplatform_sdk import PolicyClient, PolicyError

from openplatform.benchmarks.dummy.plugin import EPISODES, DummyParameters
from openplatform.storage import _write_json


def observe(position: float, episode_id: str, step: int) -> dict:
    return {
        "instruction": "Control the position",
        "vision": {},
        "state": {"position": np.array([position], dtype=np.float32)},
        "metadata": {"episode_id": episode_id, "step": step, "profile": "dummy-delta-v1"},
    }


async def evaluate(url: str, profile: str, output: Path, params: DummyParameters) -> None:
    episodes = []
    async with await PolicyClient.connect(url, io_profile=profile, timeout=5) as client:
        for episode_id, target in EPISODES:
            if episodes:
                if params.fault == "environment":
                    raise RuntimeError("injected dummy environment error")
                await asyncio.sleep(params.episode_delay)
            await client.reset(episode_id)
            position = 0.0
            await client.update_obs(observe(position, episode_id, 0), step=0)
            chunk = await client.get_action(step=0)
            executed = chunk[:2]
            for index, action in enumerate(executed):
                delta = action.get("delta")
                if set(action) != {"delta"} or delta.shape != (1,) or delta.dtype != np.float32:
                    raise PolicyError(
                        "invalid_action", "dummy requires one float32 delta of shape (1,)"
                    )
                position += float(delta[0])
                if index + 1 < len(executed):
                    await client.update_obs(
                        observe(position, episode_id, index + 1), step=index + 1
                    )
            episodes.append(
                {
                    "id": episode_id,
                    "success": position == target,
                    "steps": len(executed),
                    "position": position,
                }
            )
            rate = sum(row["success"] for row in episodes) / len(episodes)
            _write_json(output / "result.json", {"episodes": episodes, "success_rate": rate})
            print(f"{episode_id}: success={episodes[-1]['success']}", flush=True)


def main() -> int:
    parser = argparse.ArgumentParser(description="Run the OpenPlatform CPU fixture")
    parser.add_argument("--policy-url", required=True)
    parser.add_argument("--profile", required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--parameters", required=True)
    args = parser.parse_args()
    try:
        params = DummyParameters.model_validate_json(args.parameters)
        asyncio.run(evaluate(args.policy_url, args.profile, args.output, params))
    except Exception as error:
        kind = "inference" if isinstance(error, PolicyError) else "environment"
        message = f"{type(error).__name__}: {error}"
        _write_json(args.output / "error.json", {"kind": kind, "message": message})
        print(message, file=sys.stderr, flush=True)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
