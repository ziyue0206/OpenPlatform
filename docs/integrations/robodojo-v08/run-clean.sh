#!/usr/bin/env bash
set -euo pipefail
OP="$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)"
W="${1:?absolute output directory with the isolated Conda prefixes}"
RD="${2:?absolute RoboDojo checkout}"
: "${CONDA_ENVS_PATH:?set CONDA_ENVS_PATH to the new RoboDojo and demo-policy parent}"
sim="$CONDA_ENVS_PATH/RoboDojo/bin/python"
policy="$CONDA_ENVS_PATH/demo-policy/bin/python"
E="$W/evidence"
mkdir -p "$E" "$W/wheels"
unset PYTHONPATH UV_PROJECT_ENVIRONMENT
export PYTHONNOUSERSITE=1
cd "$OP"
set -x
git rev-parse HEAD >"$E/openplatform-revision.txt"
git status --porcelain --untracked-files=no >"$E/openplatform-tracked-status.txt"
uv --version
uv sync --locked --no-dev --no-editable --python "$sim"
uv build --all-packages --wheel --out-dir "$W/wheels"
sha256sum "$W"/wheels/*.whl >"$E/wheel-sha256.txt"
for exe in "$sim" "$policy"; do
  "$exe" -m pip install --no-deps "$W/wheels"/openplatform_sdk-*.whl
done
cli="$OP/.venv/bin/openplatform"
"$OP/.venv/bin/python" "$OP/docs/integrations/robodojo-v08/configure-demo.py" \
  "$RD" "$CONDA_ENVS_PATH" "$W/demo" --gpu "${ROBODOJO_SIM_GPU:-0}"
# Invoke the installed entry points from outside either checkout.
cd "$E"
for exe in "$OP/.venv/bin/python" "$sim" "$policy"; do
  "$exe" -c 'import os, sys, openplatform_sdk, numpy, msgpack, websockets; from importlib.util import find_spec; assert "PYTHONPATH" not in os.environ; print(sys.prefix, openplatform_sdk.__file__, numpy.__version__, websockets.__version__, bool(find_spec("openplatform")))'
done >python-environments.txt
"$sim" -m pip freeze >simulator-pip.txt
"$policy" -m pip freeze >policy-pip.txt
conda list --prefix "$CONDA_ENVS_PATH/RoboDojo" --explicit >simulator-conda.txt
conda list --prefix "$CONDA_ENVS_PATH/demo-policy" --explicit >policy-conda.txt
nvidia-smi >gpu.txt
git -C "$RD" rev-parse HEAD >robodojo-revision.txt
git -C "$RD" submodule status >>robodojo-revision.txt
cp "$RD/.openplatform-assets-revision" assets-revision.txt
"$cli" --help >help.txt
"$cli" list >list.json
"$cli" validate "$W/demo/submission.yaml" --check-paths >validate.txt
negative=0
"$cli" doctor --bench robodojo --recipe conda --checkout "$W/missing-checkout" >doctor-missing.json || negative=$?
printf '%s\n' "$negative" >doctor-missing.exit
[[ "$negative" == 1 ]]
"$cli" doctor --bench robodojo --recipe conda --checkout "$RD" >doctor.json
"$cli" prepare --bench robodojo --recipe conda --checkout "$RD" >prepare-conda.json
"$cli" prepare --bench robodojo --recipe docker --checkout "$RD" >prepare-docker.json
for preset in smoke full; do
  started="$(date +%s)"
  rc=0
  "$cli" eval "$W/demo" --bench robodojo --preset "$preset" --output-dir "$W/runs/$preset" \
    >"$preset.json" 2>"$preset.stderr.log" || rc=$?
  echo "$rc" >"$preset.exit"
  echo "$(( $(date +%s) - started ))" >"$preset.seconds"
  [[ "$rc" == 0 ]] || exit "$rc"
  run_dir="$(python3 -c 'import json,sys; print(json.load(open(sys.argv[1]))["run_dir"])' "$preset.json")"
  "$cli" status "$run_dir" >"$preset-status.json"
done
