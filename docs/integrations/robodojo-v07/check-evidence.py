"""Verify recorded frame joins, runtime failures and paired collection cost."""

import json
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

CAMERAS = {"cam_head", "cam_left_wrist", "cam_right_wrist"}


def read_json(path):
    return json.loads(path.read_text())


def attempt(evidence, mode, name):
    [path] = (evidence / mode / "runs").glob(f"*/evals/{name}/attempts/0001")
    return path


def events(native):
    with (native / "events.jsonl").open() as stream:
        return [json.loads(line) for line in stream if line.endswith("\n")]


def verify_join(native):
    rows = events(native)
    counts = Counter(e["kind"] for e in rows)
    assert counts["executed_action"] == 800 and counts["video_frame"] == 801
    calls = {r["seq"]: r for r in rows if r["kind"] == "policy_call"}
    chunks = {r["chunk"]: r for r in rows if r["kind"] == "chunk"}
    frames = {r["control_step"]: r for r in rows if r["kind"] == "video_frame"}
    assert set(frames) == set(range(801))
    assert len(chunks) == 267 and len(calls) == 1069
    assert all(len(chunk["actions"]) == 3 for chunk in chunks.values())
    [result] = [r for r in rows if r["kind"] == "episode_result"]
    assert result["termination"] == "step_limit" and result["control_step"] == 800
    [index] = read_json(native / "episode-index.json")["episodes"]
    assert index["status"] == "completed" and index["episode_id"] == result["episode_id"]
    assert set(index["cameras"]) == CAMERAS
    for camera, stream in index["cameras"].items():
        assert int(stream["nb_read_frames"]) == 801 and (native / stream["file"]).is_file()
        assert [frames[i]["frames"][camera] for i in range(801)] == list(range(801))
    assert frames[800]["terminal"] and not any(frames[i]["terminal"] for i in range(800))
    assert "left_arm_joint_state" in frames[0]["state"]
    actions = [r for r in rows if r["kind"] == "executed_action"]
    for step, action in enumerate(actions):
        assert action["episode_id"] == result["episode_id"]
        assert action["control_step"] == step and action["completed_control_step"] == step + 1
        chunk, index = divmod(step, 3)
        chunk += 1
        assert (action["chunk"], action["index"]) == (chunk, index)
        assert chunks[chunk]["actions"][index] == action["action"]
        assert action["policy_call_id"] == chunks[chunk]["policy_call_id"]
        assert calls[action["policy_call_id"]]["method"] == "get_action"
        assert calls[action["observation_call_id"]]["control_step"] == step
        assert frames[step]["physics_step"] == action["physics_step_before"]
        assert frames[step + 1]["physics_step"] == action["physics_step_after"]
        assert action["physics_step_after"] > action["physics_step_before"]
        assert all(value == 0.0 for joint in action["action"].values() for value in joint["values"])
    assert not (native / "observations").exists()
    first, last = actions[0], actions[-1]
    return {
        "counts": dict(counts),
        "per_camera_frames": 801,
        "executed_tail_indices": [a["index"] for a in actions if a["chunk"] == 267],
        "physics_steps": [first["physics_step_before"], last["physics_step_after"]],
        "physics_steps_per_action": dict(
            Counter(a["physics_step_after"] - a["physics_step_before"] for a in actions)
        ),
        "native_action_elapsed_median_ms": statistics.median(a["elapsed_ns"] for a in actions)
        / 1e6,
    }


def perf_row(folder):
    state = read_json(folder / "state.json")
    native = folder / "native"
    benchmark = (folder / "logs/benchmark.stdout.log").read_text()
    policy = (folder / "logs/policy.stdout.log").read_text()
    assert state["status"] == "succeeded"
    assert [int(s) for s in re.findall(r"env0 step: \x1b\[92m(\d+) / 800", benchmark)] == list(
        range(1, 801)
    )
    assert policy.count("Generated action") == 267
    assert policy.count("Received observation") == 800
    assert policy.count("successfully reset") == 2
    rows = events(native)
    [start] = [r for r in rows if r["kind"] == "episode_start"]
    [result] = [r for r in rows if r["kind"] == "episode_result"]
    verified = read_json(native / "verified.json")
    assert set(verified["cameras"]) == CAMERAS
    assert all(int(camera["nb_read_frames"]) == 801 for camera in verified["cameras"].values())
    return {
        "benchmark_seconds": state["processes"]["benchmark"]["duration_seconds"],
        "episode_seconds": (result["monotonic_ns"] - start["monotonic_ns"]) / 1e9,
        "trace_bytes": (native / "events.jsonl").stat().st_size,
        "native_bytes": sum(p.stat().st_size for p in native.rglob("*") if p.is_file()),
        "control_steps_from_native_log": 800,
        "policy_chunks_from_model_log": 267,
        "native_result": read_json(native / "_result.json"),
    }


def check_fault(evidence, case, expected_status, episode_status, kind=None):
    folder = attempt(evidence, "on", case)
    state = read_json(folder / "state.json")
    native = folder / "native"
    index = read_json(native / "episode-index.json")
    [episode] = index["episodes"]
    assert state["status"] == expected_status and episode["status"] == episode_status
    if kind:
        assert state["error"]["kind"] == kind
    assert index["native_scored_episodes"] == "unavailable"
    assert not (native / "_result.json").exists() and not (folder / "metrics.json").exists()
    assert episode["cameras"] == "unavailable"
    steps = sum(r["kind"] == "executed_action" for r in events(native))
    assert steps >= 6
    return {
        "status": state["status"],
        "steps": steps,
        "processes": state["processes"],
        "error": state.get("error"),
        "episode_status": episode["status"],
    }


def verify(evidence):
    off_folder = attempt(evidence, "off", "off")
    on_folder = attempt(evidence, "on", "on")
    off, on = perf_row(off_folder), perf_row(on_folder)
    assert off["native_result"] == on["native_result"]
    assert off["native_result"]["eval_time"] == 1
    assert off["native_result"]["details"]["0"]["layout_id"] == 0
    assert {e["kind"] for e in events(off_folder / "native")}.isdisjoint(
        {"executed_action", "policy_call", "chunk", "video_frame"}
    )
    assert not (off_folder / "native/observations").exists()
    faults = {
        "model": check_fault(evidence, "model-error", "failed", "errored", "policy"),
        "simulator": check_fault(evidence, "sim-interrupt", "failed", "incomplete", "environment"),
        "cancel": check_fault(evidence, "cancel", "canceled", "canceled"),
    }
    assert faults["simulator"]["processes"]["benchmark"]["returncode"] == 137
    assert faults["model"]["steps"] == 6
    assert read_json(evidence / "off/cli-exit.json")["exit_code"] == 0
    assert read_json(evidence / "on/cli-exit.json")["exit_code"] == 130
    assert read_json(evidence / "on/status.json")["evals"]["unstarted"]["started"] is False
    delta = on["benchmark_seconds"] - off["benchmark_seconds"]
    return {
        "joins": verify_join(on_folder / "native"),
        "collection_cost": {
            "off": off,
            "on": on,
            "delta_seconds": delta,
            "delta_percent": delta / off["benchmark_seconds"] * 100,
            "delta_episode_seconds": on["episode_seconds"] - off["episode_seconds"],
            "delta_trace_bytes": on["trace_bytes"] - off["trace_bytes"],
        },
        "faults": faults,
        "after_cancel_started": False,
    }


if __name__ == "__main__":
    print(json.dumps(verify(Path(sys.argv[1])), indent=2))
