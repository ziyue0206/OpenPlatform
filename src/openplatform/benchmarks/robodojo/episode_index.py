"""Join native results and finalized files to the per-episode event stream."""

import json
from collections import defaultdict
from pathlib import Path

UNAVAILABLE = "unavailable"


def read_events(path: Path):
    if path.is_file():
        with path.open(encoding="utf-8") as stream:
            # A killed writer may leave its last row incomplete.
            return [json.loads(line) for line in stream if line.endswith("\n")]
    return []


def write_episode_index(directory, events, raw, process_status, verified=None):
    groups = defaultdict(list)
    for event in events:
        if event["episode_id"] is not None:
            groups[event["episode_id"]].append(event)

    episodes = []
    for episode_id, rows in groups.items():
        result = next((row for row in rows if row["kind"] == "episode_result"), None)
        status = "canceled" if process_status == "canceled" else "incomplete"
        native_index = UNAVAILABLE
        if result is not None:
            status = "completed"
            native_index = result["native_result_index"]
        elif any(row["kind"] == "episode_excluded" for row in rows):
            status = "excluded"
        elif any(row["kind"] in {"policy_error", "eval_error", "reset_error"} for row in rows):
            status = "errored"
        cameras = UNAVAILABLE
        if result is not None and verified is not None:
            episode_files = verified.get("episodes", {"0": verified})[str(native_index)]
            cameras = episode_files["cameras"] or UNAVAILABLE
        episodes.append(
            {
                "episode_id": episode_id,
                "status": status,
                "layout_id": rows[0]["layout_id"],
                "native_result_index": native_index,
                "events": {
                    "file": "events.jsonl",
                    "first_seq": rows[0]["seq"],
                    "last_seq": rows[-1]["seq"],
                },
                "cameras": cameras,
            }
        )
    report = {
        "native_scored_episodes": raw.get("eval_time", UNAVAILABLE),
        "benchmark_process_status": process_status,
        "frame_join": "Join on episode_id and control_step. Use completed_control_step for the post-action frame.",
        "applied_joint_targets_after_clipping": UNAVAILABLE,
        "episodes": episodes,
    }
    (directory / "episode-index.json").write_text(
        json.dumps(report, indent=2) + "\n", encoding="utf-8"
    )
