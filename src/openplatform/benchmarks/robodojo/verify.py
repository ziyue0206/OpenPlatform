"""Verify native episode coverage and decodable camera streams."""

import json
import subprocess
import sys
from pathlib import Path
from tempfile import NamedTemporaryFile

DOCKER_RECEIPT = ".openplatform-docker-smoke.json"


def _verify_cameras(evidence: Path, index: int) -> dict:
    cameras = {}
    for camera in ("cam_head", "cam_left_wrist", "cam_right_wrist"):
        paths = sorted(evidence.glob(f"episode_{index:07d}_{camera}_*.mp4"))
        if len(paths) != 1 or not paths[0].stat().st_size:
            raise ValueError(f"expected one nonempty video for {camera}")
        probe = subprocess.run(
            [
                "ffprobe",
                "-v",
                "error",
                "-select_streams",
                "v:0",
                "-count_frames",
                "-show_entries",
                "stream=width,height,nb_read_frames",
                "-of",
                "json",
                str(paths[0]),
            ],
            check=True,
            capture_output=True,
            text=True,
            timeout=120,
        )
        [stream] = json.loads(probe.stdout)["streams"]
        if not all(int(stream[key]) > 0 for key in ("width", "height", "nb_read_frames")):
            raise ValueError(f"{camera} has no readable frames")
        cameras[camera] = {"file": paths[0].name, **stream}
    return cameras


def verify(evidence: Path, *, videos=True, expected_episodes=1) -> dict:
    result = json.loads((evidence / "_result.json").read_text(encoding="utf-8"))
    indices = [str(index) for index in range(expected_episodes)]
    if result["eval_time"] != expected_episodes or set(result["details"]) != set(indices):
        raise ValueError(
            f"expected {expected_episodes} effective episodes, got {result['eval_time']}"
        )
    if not 0 <= result["success_rate"] <= 1 or not 0 <= result["score"] <= 100:
        raise ValueError("native aggregate is out of range")
    layouts = [result["details"][index]["layout_id"] for index in indices]
    if any(type(layout) is not int or layout < 0 for layout in layouts):
        raise ValueError("expected a nonnegative actual layout ID")
    if len(set(layouts)) != expected_episodes:
        raise ValueError("native result repeats a layout")
    episodes = {
        index: {
            "layout_id": result["details"][index]["layout_id"],
            "cameras": _verify_cameras(evidence, int(index)) if videos else {},
        }
        for index in indices
    }
    if expected_episodes == 1:
        return {"effective_episodes": 1, **episodes["0"]}
    return {"effective_episodes": expected_episodes, "layout_ids": layouts, "episodes": episodes}


def record_docker_image(evidence: Path, checkout: Path, result: dict) -> None:
    [image] = json.loads((evidence / "image.json").read_text(encoding="utf-8"))
    receipt = {
        "image_id": image["Id"],
        "robodojo_revision": (evidence / "robodojo.txt")
        .read_text(encoding="utf-8")
        .splitlines()[0],
        "asset_revision": (evidence / "assets-revision.txt").read_text(encoding="utf-8").strip(),
        "layout_id": result["layout_id"],
        "effective_episodes": result["effective_episodes"],
    }
    with NamedTemporaryFile(
        mode="w", encoding="utf-8", dir=checkout, prefix=".openplatform-docker-", delete=False
    ) as stream:
        staged = Path(stream.name)
        try:
            json.dump(receipt, stream, indent=2)
            stream.write("\n")
            stream.close()
            staged.replace(checkout / DOCKER_RECEIPT)
        finally:
            staged.unlink(missing_ok=True)


if __name__ == "__main__":
    evidence = Path(sys.argv[1])
    result = verify(evidence)
    if len(sys.argv) == 3:
        record_docker_image(evidence, Path(sys.argv[2]), result)
    print(json.dumps(result, indent=2))
