"""Run off/on/off in the same fixed, single-episode RoboDojo setup."""

import argparse
import json
import subprocess
import time
from pathlib import Path

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("cli", type=Path)
    parser.add_argument("demo", type=Path, help="Configured Docker or Conda demo directory")
    parser.add_argument("output", type=Path)
    parser.add_argument(
        "--cases", nargs="+", choices=("off-1", "on", "off-2"), default=("off-1", "on", "off-2")
    )
    parser.add_argument("--control-steps", type=int, choices=range(1, 800), metavar="N")
    parser.add_argument("--timeout", type=float, metavar="SECONDS")
    parser.add_argument("--no-videos", action="store_true")
    args = parser.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    for case in args.cases:
        folder = args.output / case
        folder.mkdir()
        model = yaml.safe_load((args.demo / "submission.yaml").read_text())
        model["adapter"]["source"]["path"] = str(args.demo.resolve())
        model["collection"] = {
            "videos": not args.no_videos,
            "trace": True,
            "policy_calls": True,
            "observations": False,
            "safety": case == "on",
        }
        entry = model["evals"][0]
        if entry["recipe"] not in {"conda", "docker"} or entry["preset"] != "smoke":
            parser.error("demo must use the RoboDojo Conda or Docker smoke preset")
        if args.control_steps is not None:
            entry["parameters"]["smoke_control_steps"] = args.control_steps
        if args.timeout is not None:
            entry["parameters"]["timeout"] = args.timeout
        submission = folder / "submission.yaml"
        submission.write_text(yaml.safe_dump(model, sort_keys=False))
        command = [
            str(args.cli.resolve()),
            "run",
            str(submission.resolve()),
            "--output-dir",
            str((folder / "runs").resolve()),
        ]
        started = time.monotonic()
        with (folder / "cli.json").open("w") as stdout, (folder / "cli.stderr").open("w") as stderr:
            code = subprocess.run(command, stdout=stdout, stderr=stderr).returncode
        (folder / "execution.json").write_text(
            json.dumps(
                {"argv": command, "returncode": code, "wall_seconds": time.monotonic() - started}
            )
            + "\n"
        )
        if code:
            raise SystemExit(code)


if __name__ == "__main__":
    main()
