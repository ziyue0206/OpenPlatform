"""Pair the two completed upstream result sets by actual layout and repetition."""

import argparse
import hashlib
import json
from collections import Counter, defaultdict
from itertools import zip_longest
from pathlib import Path


def read_jsonl(path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]


def episodes(directory):
    raw = json.loads((directory / "_result.json").read_text(encoding="utf-8"))
    events = read_jsonl(directory / "events.jsonl")
    groups = defaultdict(list)
    for event in events:
        if event["layout_id"] is not None:
            groups[event.get("episode_id", (event["restart"], event["episode"]))].append(event)
    counts = Counter()
    completed = {}
    excluded = []
    for rows in groups.values():
        results = [row["result"] for row in rows if row["kind"] == "episode_result"]
        layout = rows[0]["layout_id"]
        if not results:
            excluded.append({"layout_id": layout, "last_event": rows[-1]})
            continue
        [result] = results
        key = layout, counts[layout]
        counts[layout] += 1
        completed[key] = result, rows
    if len(completed) != raw["eval_time"] or list(raw["details"].values()) != [
        result for result, _ in completed.values()
    ]:
        raise ValueError(f"trace episode count or scores disagree with {directory}/_result.json")
    return raw, completed, excluded


def first_field(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        for key in sorted(a.keys() | b.keys()):
            here = f"{path}/{key}"
            if key not in a or key not in b:
                return here, a.get(key, "unavailable"), b.get(key, "unavailable")
            difference = first_field(a[key], b[key], here)
            if difference:
                return difference
        return None
    if isinstance(a, list) and isinstance(b, list):
        if len(a) != len(b):
            return f"{path}/length", len(a), len(b)
        for i, (left, right) in enumerate(zip(a, b, strict=True)):
            difference = first_field(left, right, f"{path}/{i}")
            if difference:
                return difference
        return None
    return (path, a, b) if type(a) is not type(b) or a != b else None


def first_event(a, b, *, kinds=None):
    telemetry = {
        "seq",
        "episode",
        "restart",
        "episode_id",
        "monotonic_ns",
        "elapsed_ns",
        "policy_call_id",
        "observation_call_id",
        "completed_control_step",
        "physics_step_before",
        "physics_step_after",
        "native_result_index",
    }

    def relevant(rows):
        return [
            {k: v for k, v in row.items() if k not in telemetry}
            for row in rows
            if row["kind"] not in {"video_frame", "observation_images"}
            if kinds is None or row["kind"] in kinds
        ]

    for i, (left, right) in enumerate(zip_longest(relevant(a), relevant(b))):
        difference = first_field(left, right)
        if difference:
            field, official, bridge = difference
            return {
                "index": i,
                "field": field,
                "official": official,
                "openplatform": bridge,
                "official_step": left and left["control_step"],
                "openplatform_step": right and right["control_step"],
            }
    return None


def compare(official, bridge, layouts, conditions):
    a_raw, a, a_excluded = episodes(official)
    b_raw, b, b_excluded = episodes(bridge)
    rows = []
    for key in sorted(a.keys() | b.keys()):
        layout, repeat = key
        left, right = a.get(key), b.get(key)
        layout_file = layouts / f"stack_bowls_{layout}.json"
        row = {
            "key": ["stack_bowls", 0, layout, repeat],
            "layout_sha256": hashlib.sha256(layout_file.read_bytes()).hexdigest(),
            "official": left and left[0],
            "openplatform": right and right[0],
        }
        if left and right:
            row.update(
                comparison_status=conditions["comparison_status"],
                first_difference=first_event(left[1], right[1]),
                first_chunk_difference=first_event(left[1], right[1], kinds={"chunk"}),
                first_executed_action_difference=first_event(
                    left[1],
                    right[1],
                    kinds={"executed_action"},
                ),
                counts={
                    name: dict(Counter(event["kind"] for event in side[1]))
                    for name, side in (("official", left), ("openplatform", right))
                },
            )
        else:
            row["comparison_status"] = "unavailable"
        rows.append(row)
    return {
        "execution_status": {"official": "succeeded", "openplatform": "succeeded"},
        "conditions": conditions,
        "requested": {"task": "stack_bowls", "seed": 0, "effective_episodes": 1},
        "coverage": {"official": a_raw["eval_time"], "openplatform": b_raw["eval_time"]},
        "excluded": {"official": a_excluded, "openplatform": b_excluded},
        "episodes": rows,
        "action_boundary": "take_action input for calls that returned",
        "physics_step_and_video_frame_join": "unavailable",
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    for name in ("official", "openplatform", "layouts", "conditions"):
        parser.add_argument(name, type=Path)
    args = parser.parse_args()
    print(
        json.dumps(
            compare(
                args.official,
                args.openplatform,
                args.layouts,
                json.loads(args.conditions.read_text(encoding="utf-8")),
            ),
            indent=2,
            ensure_ascii=False,
        )
    )
