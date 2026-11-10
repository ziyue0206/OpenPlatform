"""Copy the reference adapter and fill in the local RoboDojo paths."""

import argparse
from pathlib import Path
from shutil import copyfile

import yaml


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("checkout", type=Path)
    parser.add_argument(
        "environment", type=Path, help="Conda env directory or Docker policy Python"
    )
    parser.add_argument("output", type=Path)
    parser.add_argument("--gpu", type=int, default=0)
    parser.add_argument("--recipe", choices=("conda", "docker"), default="conda")
    args = parser.parse_args()

    repo = Path(__file__).resolve().parents[3]
    example = repo / "examples/submissions/robodojo-demo"
    config = yaml.safe_load((example / "submission.yaml").read_text(encoding="utf-8"))
    checkout = args.checkout.resolve(strict=True)
    environment = args.environment.absolute()
    config["model"]["source"]["path"] = str(checkout)
    eval_config = config["evals"][0]
    eval_config["recipe"] = args.recipe
    eval_config["parameters"]["checkout"] = str(checkout)
    if args.recipe == "docker":
        config["model"]["runtime"]["python"] = str(environment)
        del eval_config["parameters"]["simulator_python"]
    else:
        config["model"]["runtime"]["python"] = str(environment / "demo-policy/bin/python")
        eval_config["parameters"]["simulator_python"] = str(environment / "RoboDojo/bin/python")
    config["resources"]["environment_gpu_ids"] = [args.gpu]
    args.output.mkdir()
    copyfile(example / "policy.py", args.output / "policy.py")
    (args.output / "submission.yaml").write_text(
        yaml.safe_dump(config, sort_keys=False), encoding="utf-8"
    )


if __name__ == "__main__":
    main()
