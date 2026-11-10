"""Read persisted off/on/off runs, compare native outcomes, and validate safety coverage."""

import argparse
import json
from itertools import zip_longest
from pathlib import Path


def load(path):
    return json.loads(path.read_text())


def lines(path):
    with path.open() as stream:
        return [json.loads(line) for line in stream]


def view(folder):
    result = load(folder / "cli.json")
    assert result["status"] == "succeeded"
    root = Path(result["run_dir"])
    (native,) = root.glob("evals/*/attempts/0001/native")
    raw = load(native / "_result.json")
    trace = lines(native / "events.jsonl")
    (episode,) = [event for event in trace if event["kind"] == "episode_result"]
    actions = [event for event in trace if event["kind"] == "executed_action"]
    observations = [event for event in trace if event.get("method") == "update_obs"]
    launch = load(native / "launch.json")
    assert len(actions) == episode["control_step"]
    if "smoke_control_steps" in launch:
        assert episode["step_limit"] == launch["smoke_control_steps"]
        (budget,) = [event for event in trace if event["kind"] == "smoke_control_budget"]
        assert budget["requested"] == episode["step_limit"] < budget["native"]
    summary = {
        "run_dir": str(root),
        "layout_id": episode["result"]["layout_id"],
        "success": episode["result"]["success"],
        "score": episode["result"]["score"],
        "termination": episode["termination"],
        "control_steps": episode["control_step"],
        "step_limit": episode["step_limit"],
        "image_id": launch.get("image_id"),
        "wall_seconds": load(folder / "execution.json")["wall_seconds"],
        "run_bytes": sum(file.stat().st_size for file in root.rglob("*") if file.is_file()),
        "native_bytes": sum(file.stat().st_size for file in native.rglob("*") if file.is_file()),
    }
    return raw, summary, actions, observations, native


def first_difference(left, right, field):
    for a, b in zip_longest(left, right):
        if a is None or b is None or a[field] != b[field]:
            return {
                "left": a,
                "right": b,
            }
    return None


def verify_safety(native, episode_id, actions):
    events = lines(native / "safety-events.jsonl")
    metrics = lines(native / "safety-metrics.jsonl")
    assert all(event["episode_id"] == episode_id for event in events)
    (baseline,) = [event for event in events if event["kind"] == "baseline"]
    assert not baseline["error"], baseline["error"]
    assert set(baseline["object_poses"]) == {"bowl0", "bowl1", "bowl2"}
    assert all(baseline["object_poses"].values())
    steps = [event for event in events if event["kind"] == "physics_step"]
    (coverage,) = [item for item in metrics if item["metric_id"] == "collection_coverage"]
    expected = sum(row["physics_step_after"] - row["physics_step_before"] for row in actions)
    assert len(steps) == coverage["valid_steps"] == coverage["expected_steps"] == expected
    assert coverage["value"] == 1.0 and coverage["status"] == "ok"
    assert steps[0]["physics_step"] == actions[0]["physics_step_before"] + 1
    assert steps[-1]["physics_step"] == actions[-1]["physics_step_after"]
    assert all(b["physics_step"] == a["physics_step"] + 1 for a, b in zip(steps, steps[1:]))
    for metric in metrics:
        reference = metric["evidence"]
        if "line" in reference:
            event = events[reference["line"] - 1]
            assert event["physics_step"] == reference["physics_step"]
    artifact_index = load(native.parents[4] / "artifacts.json")
    for name in ("safety-events.jsonl", "safety-metrics.jsonl"):
        assert name in json.dumps(artifact_index)
    return {
        "coverage": coverage,
        "metrics": metrics,
        "safety_bytes": sum(
            (native / name).stat().st_size
            for name in ("safety-events.jsonl", "safety-metrics.jsonl")
        ),
        "pose_objects": baseline["objects"],
        "robot_pairs": baseline["coverage"]["robot_pairs"],
        "first_dt_s": steps[0]["dt_s"],
        "last_sim_time_s": steps[-1]["sim_time_s"],
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("output", type=Path)
    args = parser.parse_args()
    cases = {case: view(args.output / case) for case in ("off-1", "on", "off-2")}
    summaries = {case: data[1] for case, data in cases.items()}
    comparisons = {}
    for left, right in (("off-1", "on"), ("on", "off-2"), ("off-1", "off-2")):
        raw_a, a, actions_a, obs_a, _ = cases[left]
        raw_b, b, actions_b, obs_b, _ = cases[right]
        comparisons[f"{left}/{right}"] = {
            "native_equal": raw_a == raw_b,
            "native_differences": None if raw_a == raw_b else [raw_a, raw_b],
            "episode_equal": all(
                a[key] == b[key]
                for key in (
                    "layout_id",
                    "success",
                    "score",
                    "termination",
                    "control_steps",
                    "step_limit",
                    "image_id",
                )
            ),
            "first_action_difference": first_difference(actions_a, actions_b, "action"),
            "first_observation_difference": first_difference(obs_a, obs_b, "observation"),
        }
    for case in ("off-1", "off-2"):
        assert not list(cases[case][4].glob("safety-*.jsonl"))
    _, _, actions, _, native = cases["on"]
    safety = verify_safety(native, actions[0]["episode_id"], actions)
    print(
        json.dumps(
            {
                "runs": summaries,
                "native": {case: data[0] for case, data in cases.items()},
                "comparisons": comparisons,
                "safety": safety,
            },
            indent=2,
        )
    )
    if not all(
        row["native_equal"] and row["episode_equal"] and row["first_action_difference"] is None
        for row in comparisons.values()
    ):
        raise SystemExit("native or executed-action difference; inspect comparison evidence")


if __name__ == "__main__":
    main()
