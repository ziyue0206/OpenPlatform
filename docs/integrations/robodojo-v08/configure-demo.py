"""Copy the reference adapter and fill in the local RoboDojo paths."""

import argparse
from pathlib import Path
from shutil import copyfile

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path)
    parser.add_argument("conda_envs", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--gpu", type=int, default=0)
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[3]
    example = repo / "examples/submissions/robodojo-demo"
    config = yaml.safe_load((example / "submission.yaml").read_text(encoding="utf-8"))
    checkout = args.checkout.resolve(strict=True)
    envs = args.conda_envs.resolve(strict=True)
    config["model"]["source"]["path"] = str(checkout)
    config["model"]["runtime"]["python"] = str(envs / "demo-policy/bin/python")
    config["evals"][0]["parameters"].update(
        checkout=str(checkout), simulator_python=str(envs / "RoboDojo/bin/python")
    )
    config["resources"]["environment_gpu_ids"] = [args.gpu]
    args.output.mkdir()
    copyfile(example / "policy.py", args.output / "policy.py")
    (args.output / "submission.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
