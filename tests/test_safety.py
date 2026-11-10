import json

import pytest

from openplatform.benchmarks.robodojo.safety import contact_pairs
from openplatform.safety import EVENTS, METRICS, SafetyEvents, summarize_safety


def test_contact_manifolds_deduplicate_mirrored_colliders_and_project_impulse():
    points = [((1, 0, 0), (0.2, 1, 0)), ((1, 0, 0), (0.1, 0, 0))]
    duplicate = [((-1, 0, 0), (-0.3, -1, 0))]
    different_collider = [((0, 1, 0), (2, 0.4, 0))]
    assert contact_pairs(
        [
            ("right", "left", "r", "l", duplicate),
            ("left", "right", "l", "r", points),
            ("left", "right", "l2", "r", different_collider),
        ],
        0.01,
    ) == [(("left", "right"), pytest.approx(50))]


def rows(path):
    return [json.loads(line) for line in path.read_text().splitlines()]


def test_safety_aggregate_preserves_valid_zero_missing_and_episode_baselines(tmp_path):
    events = SafetyEvents(tmp_path)

    def write(kind, episode_id, **fields):
        events.write(kind, episode_id=episode_id, env_id=0, **fields)

    def pose(x):
        return {"position_m": [x, 0, 0], "quaternion_xyzw": [0, 0, 0, 1]}

    objects = {"bowl0": "target"}

    def start(episode_id, x):
        write("episode_open", episode_id, objects=objects)
        write(
            "baseline",
            episode_id,
            physics_step=100,
            objects=objects,
            object_poses={"bowl0": pose(x)},
        )

    def step(episode_id, index, x, contacts, collision):
        write(
            "physics_step",
            episode_id,
            physics_step=index,
            dt_s=0.004,
            contacts=contacts,
            self_collision=collision,
            object_poses={"bowl0": pose(x)},
        )

    pair = {
        "bodies": [{"id": "left"}, {"id": "right"}],
        "robot_scene": False,
        "normal_force_n": 5.5,
    }
    scene_pair = {
        "bodies": [{"id": "left"}, {"id": "table"}],
        "robot_scene": True,
        "normal_force_n": 1.2,
    }
    start("first", 1)
    step("first", 101, 1.25, [pair, scene_pair], [pair])
    step("first", 102, 1.5, [pair], [pair])
    write("episode_end", "first", physics_step=102, status="complete")
    start("second", 100)
    step("second", 101, 100, [], [])
    write("episode_end", "second", physics_step=101, status="complete")
    start("partial", 4)
    step("partial", 101, 4, None, None)
    events.flush()
    with (tmp_path / EVENTS).open("a") as stream:
        stream.write('{"incomplete":')
    summarize_safety(tmp_path)
    summary = {(row["episode_id"], row["metric_id"]): row for row in rows(tmp_path / METRICS)}
    peak = summary["first", "peak_normal_contact_force"]
    assert peak["value"] == 5.5
    assert peak["valid_steps"] == peak["expected_steps"] == 2
    assert peak["evidence"]["physics_step"] == 101
    source = (tmp_path / EVENTS).read_text().splitlines()[peak["evidence"]["line"] - 1]
    assert json.loads(source)["contacts"][0]["bodies"] == peak["evidence"]["pair"]
    assert summary["first", "self_collision_occurred"]["evidence"]["physics_step"] == 101
    assert summary["first", "peak_robot_scene_normal_contact_force"]["value"] == 1.2
    assert summary["first", "max_baseline_displacement:bowl0"]["value"] == 0.5
    assert summary["second", "max_baseline_displacement:bowl0"]["value"] == 0.0
    assert summary["second", "self_collision_occurred"]["value"] is False
    assert summary["second", "peak_normal_contact_force"]["value"] == 0.0
    assert summary["second", "max_bystander_baseline_displacement"]["status"] == "not_applicable"
    missing = summary["partial", "peak_normal_contact_force"]
    assert missing["status"] == "unavailable" and missing["value"] is None
    assert missing["expected_steps_status"] == "lower_bound"
    assert summary["partial", "collection_coverage"]["status"] == "partial"


def test_sleeping_or_unresolved_support_cannot_be_aggregated_as_zero_force(tmp_path):
    events = SafetyEvents(tmp_path)
    common = {"episode_id": "resting", "env_id": 0}
    events.write("baseline", **common, physics_step=5, objects={}, object_poses={})
    events.write(
        "physics_step",
        **common,
        physics_step=6,
        dt_s=0.004,
        contacts=[],
        contact_status="partial",
        self_collision=[],
        object_poses={},
    )
    events.write("episode_end", **common, physics_step=6, status="complete")
    summarize_safety(tmp_path)
    metrics = {row["metric_id"]: row for row in rows(tmp_path / METRICS)}
    force = metrics["peak_normal_contact_force"]
    assert force["value"] is None and force["status"] == "unavailable"
    assert force["valid_steps"] == 0 and force["recorded_steps"] == force["expected_steps"] == 1
