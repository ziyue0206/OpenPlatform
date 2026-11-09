"""Run the two GPU collection modes and inject faults into owned processes."""

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import yaml

GPU_CASES = ("on", "model-error", "sim-interrupt", "cancel", "unstarted")


def config(work, evidence, enabled):
    spec = yaml.safe_load((work / "examples/submissions/robodojo-demo/submission.yaml").read_text())
    v05 = Path(os.environ.get("V05_WORK", Path.home() / "openplatform-v05")).resolve()
    checkout = v05 / "src/RoboDojo"
    spec["model"]["source"]["path"] = str(checkout)
    spec["model"]["runtime"]["python"] = str(v05 / "envs/demo-policy/bin/python")
    spec["adapter"]["source"]["path"] = str(Path(__file__).resolve().parent)
    spec["adapter"]["entrypoint"] = "probe_policy:ProbePolicy"
    spec["collection"] = {
        "videos": True,
        "trace": enabled,
        "policy_calls": enabled,
        "observations": False,
    }
    (base,) = spec["evals"]
    base["parameters"].update(
        checkout=str(checkout), simulator_python=str(v05 / "envs/RoboDojo/bin/python")
    )
    spec["evals"] = [{**base, "id": key} for key in (GPU_CASES if enabled else ("off",))]
    if enabled:
        spec["evals"][1]["adapter_config"] = {"fail_at": 3}
    file = evidence / "submission.yaml"
    file.write_text(yaml.safe_dump(spec, sort_keys=False))
    return file


def find_attempt(evidence, case):
    return next((evidence / "runs").glob(f"*/evals/{case}/attempts/0001"), None)


def wait_for_actions(process, evidence, case, steps):
    deadline = time.monotonic() + 420
    while process.poll() is None and time.monotonic() < deadline:
        attempt = find_attempt(evidence, case)
        events = attempt / "native/events.jsonl" if attempt else None
        if events and events.is_file():
            with events.open() as stream:
                actions = sum(
                    json.loads(line)["kind"] == "executed_action"
                    for line in stream
                    if line.endswith("\n")
                )
            if actions >= steps:
                return attempt, actions
        time.sleep(0.2)
    raise RuntimeError(f"{case} did not execute {steps} actions")


def simulator_pid(attempt):
    launch = json.loads((attempt / "native/launch.json").read_text())
    url = launch["argv"][launch["argv"].index("--policy_server_url") + 1].encode()
    run_id = ("ROBODOJO_RUN_ID=" + launch["env"]["ROBODOJO_RUN_ID"]).encode()
    pids = []
    for process in Path("/proc").glob("[0-9]*"):
        try:
            argv = (process / "cmdline").read_bytes().split(b"\0")
            if b"src/eval_client/main.py" in argv and url in argv:
                if run_id in (process / "environ").read_bytes().split(b"\0"):
                    pids.append(int(process.name))
        except (FileNotFoundError, ProcessLookupError, PermissionError):
            continue
    [pid] = pids
    return pid


def inject(process, evidence):
    with (evidence / "injections.jsonl").open("w", buffering=1) as log:
        for case, signum in (("sim-interrupt", signal.SIGKILL), ("cancel", signal.SIGINT)):
            attempt, actions = wait_for_actions(process, evidence, case, 6)
            pid = simulator_pid(attempt) if case == "sim-interrupt" else process.pid
            os.kill(pid, signum)
            log.write(
                json.dumps(
                    {"case": case, "pid": pid, "signal": signum.name, "observed_actions": actions}
                )
                + "\n"
            )


def run(work, cli, enabled):
    mode = "on" if enabled else "off"
    evidence = work / "evidence" / mode
    evidence.mkdir(parents=True)
    submission = config(work, evidence, enabled)
    argv = [str(cli), "run", str(submission), "--output-dir", str(evidence / "runs")]
    (evidence / "command.json").write_text(json.dumps(argv, indent=2) + "\n")
    started = time.monotonic()
    with (
        (evidence / "cli.stdout.json").open("w") as stdout,
        (evidence / "cli.stderr.log").open("w") as stderr,
    ):
        process = subprocess.Popen(argv, stdout=stdout, stderr=stderr)
        try:
            if enabled:
                inject(process, evidence)
            rc = process.wait(timeout=600)
        finally:
            if process.poll() is None:
                process.send_signal(signal.SIGINT)
                process.wait(timeout=60)
    (evidence / "cli-exit.json").write_text(
        json.dumps({"exit_code": rc, "wall_seconds": time.monotonic() - started}, indent=2) + "\n"
    )
    if rc != (130 if enabled else 0):
        raise RuntimeError(f"unexpected {mode} CLI exit: {rc}")
    run_dir = json.loads((evidence / "cli.stdout.json").read_text())["run_dir"]
    with (evidence / "status.json").open("w") as status:
        subprocess.run([str(cli), "status", run_dir], stdout=status, check=True)


if __name__ == "__main__":
    work, cli = map(Path, sys.argv[1:])
    run(work, cli, False)
    run(work, cli, True)
