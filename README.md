# OpenPlatform

OpenPlatform runs embodied AI benchmarks locally. You supply a policy and an adapter, and OpenPlatform launches the benchmark, connects it to the policy, and saves native scores, logs, and rollout evidence. Each benchmark uses its own observation and action protocol, termination rules, and scoring logic.

The v0.2.1 preview includes RoboDojo-Sim on Linux with an NVIDIA RTX GPU and a lightweight CPU fixture for checking the installation. Docker is the default RoboDojo recipe. A [Conda recipe](docs/integrations/robodojo-v08/README.md) is also available. See the [v0.2.1 release notes](docs/releases/v0.2.1.md) for the verified scope.

## Get the source

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and make Python 3.11 available. The preview was tested with Python 3.11 and uv 0.13.7.

```bash
git clone https://github.com/ziyue0206/OpenPlatform.git
cd OpenPlatform
```

If you downloaded a source archive, extract it and run the following commands from the directory containing `pyproject.toml`.

## Run a CPU check

This fixture starts a policy in a separate Python environment and runs two deterministic episodes. It needs no GPU, simulator, or model weights.

```bash
uv sync --locked --no-dev --no-editable --python 3.11
UV_PROJECT_ENVIRONMENT=examples/submissions/dummy/.venv \
  uv sync --locked --package openplatform-sdk --no-dev --no-editable --python 3.11
.venv/bin/openplatform list
.venv/bin/openplatform run examples/submissions/dummy/submission.yaml \
  --output-dir runs/cpu
```

Expect a run status of `succeeded` and a `success_rate` of `0.5`, with one successful episode and one failed episode. The CLI prints the `run_dir` to use when inspecting results.

## Run RoboDojo with Docker

The current integration runs `stack_bowls` with ARX X5, joint control, seed 0, standard layouts, and one simulator environment. The simulator runs in Docker, while the CLI and policy run on the host. Build the pinned simulator image locally from the upstream RoboDojo checkout.

### 1. Check the host and prepare RoboDojo

Use a Linux x86-64 host with an NVIDIA RTX GPU, a working NVIDIA driver, Docker Engine, NVIDIA Container Toolkit, Git, Bash, Python 3.11, and FFmpeg with `ffprobe`. Check that `nvidia-smi` and `docker info` work as your user. Plan for about 150 GiB across the working directory and Docker storage for assets, images, build caches, and results.

From the OpenPlatform source directory, run these commands in the same Bash session. Set `OMNI_KIT_ACCEPT_EULA=YES` after reviewing and accepting the [NVIDIA Isaac Sim 5.1.0 terms](https://docs.isaacsim.omniverse.nvidia.com/5.1.0/common/licenses-isaac-sim.html).

```bash
export OP="$(pwd -P)"
export W="$HOME/openplatform-work"
export RD="$W/src/RoboDojo"
mkdir -p "$W/src"

uv venv --python 3.11 "$W/tools"
uv pip install --python "$W/tools/bin/python" 'huggingface_hub==1.3.7' 'click>=8.1,<9'
export PATH="$W/tools/bin:$PATH"
export OMNI_KIT_ACCEPT_EULA=YES

recipe="$OP/src/openplatform/benchmarks/robodojo/recipe.sh"
bash "$recipe" source "$RD"
bash "$recipe" assets "$RD"
bash "$recipe" docker-build "$RD"
```

`source` pins RoboDojo and its submodules. `assets` downloads the fixed asset revision and sets up paths for this checkout. `docker-build` builds `robodojo:v00-f57e163`. You can reuse a trusted local image built from the same pin. See the [Docker setup guide](docs/integrations/robodojo-docker.md) for cache locations and build details.

### 2. Install OpenPlatform and run the reference policy

```bash
cd "$OP"
uv sync --locked --no-dev --no-editable --python 3.11
UV_PROJECT_ENVIRONMENT="$W/policy" \
  uv sync --locked --package openplatform-sdk --no-dev --no-editable --python 3.11
uv pip install --python "$W/policy/bin/python" -e "$RD/XPolicyLab"

"$OP/.venv/bin/python" docs/integrations/robodojo-v08/configure-demo.py \
  "$RD" "$W/policy/bin/python" "$W/demo" --recipe docker --gpu 0

cli="$OP/.venv/bin/openplatform"
"$cli" validate "$W/demo/submission.yaml" --check-paths
"$cli" doctor --bench robodojo --recipe docker --checkout "$RD"
"$cli" prepare --bench robodojo --recipe docker --checkout "$RD"
"$cli" eval "$W/demo" --bench robodojo --recipe docker --preset smoke \
  --output-dir "$W/runs/smoke"
```

`configure-demo.py` creates a new directory and selects host GPU 0 for simulation. Choose another GPU with `--gpu` or change `resources.environment_gpu_ids` in the generated YAML. If you already have a generated submission, edit it in place and start at `validate`.

`smoke` requests one episode. The bundled reference policy needs no checkpoint and serves as an integration check. A task failure can still produce a successful CLI run with a valid score. When ready to use the native 25-episode quota, select `--preset full` and use a separate output directory. Docker acceptance covered the one-episode smoke and five completed episodes from a multi-layout run. The full 25-episode run remains to be verified.

### 3. Enable the safety probe (optional)

Add or update the top-level `collection` block in `$W/demo/submission.yaml`, then rerun the smoke command. Video, action tracing, and policy call logging can be configured independently. This example turns them off to keep the output small.

```yaml
collection:
  safety: true
  videos: false
  trace: false
  policy_calls: false
```

For a shorter check, add `smoke_control_steps: 24` to the existing `evals[0].parameters` mapping. Keep the other parameters and use `--preset smoke`. Remove the setting to restore the native control budget or before switching to `full`. Isaac Sim startup and scene reset still run.

The probe is off by default and available with both Docker and Conda. When enabled, it adds `safety-events.jsonl` for per-physics-step evidence and `safety-metrics.jsonl` for per-episode contact forces, self-collision, object displacement, and coverage. Missing signals are marked explicitly. See the [safety probe guide](docs/integrations/robodojo-safety/README.md) for the schema, backend limits, and short RTX A/B results.

## Inspect a run

From the OpenPlatform source directory, replace `/path/to/run_dir` with the path printed by the CLI.

```bash
.venv/bin/openplatform status /path/to/run_dir
```

| Path relative to `run_dir` | Contents |
| --- | --- |
| `input_config.yaml` and `resolved_config.yaml` | Input and resolved run configuration |
| `artifacts.json` | Artifact paths, sizes, and SHA-256 digests |
| `evals/<eval_id>/attempts/0001/metrics.json` | Collected benchmark metrics |
| `evals/<eval_id>/attempts/0001/logs/` | Benchmark and policy process logs |
| `evals/<eval_id>/attempts/0001/native/` | Native results and optional traces, videos, and safety JSONL |

For RoboDojo, the native `_result.json` contains the original episode results and aggregate score. The `eval` command uses `robodojo` as the eval ID. The `run` command uses each ID declared in the submission.

## Evaluate your own policy

Start from the [generated demo template](examples/submissions/robodojo-demo/submission.yaml) and the [reference adapter](examples/submissions/robodojo-demo/policy.py). Generate a local Docker submission as above, then replace the model and adapter settings in that submission.

1. Install your model dependencies and the SDK in the policy environment. From the OpenPlatform source directory, run `/path/to/policy/bin/python -m pip install ./sdk`.
2. Set `model.source.path` to your model directory, `model.runtime.python` to the policy interpreter, and `model.checkpoint` to a path relative to the model directory. Use `null` for a weightless policy.
3. Set `adapter.source.path` and `adapter.entrypoint` to your adapter directory and `module:Class`. Implement `reset`, `update_obs`, and `get_action` using the benchmark's expected observation and action format.
4. Keep `recipe: docker` and the absolute `evals[].parameters.checkout` for the pinned RoboDojo checkout. Validate with `--check-paths`, then run `openplatform eval <model-dir> --bench robodojo --recipe docker --preset smoke`. Use `openplatform run <submission.yaml>` to run the evals declared in a YAML file.

Read the [submission schema](docs/submission.md) and [adapter interfaces](docs/interfaces.md) for path resolution, runtime options, and policy methods. For development, see the [architecture](docs/architecture.md) and [benchmark plugin guide](docs/benchmark-integration.md).
