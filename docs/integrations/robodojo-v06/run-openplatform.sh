#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
name="$1"
tag="$2"
out="$W/evidence/$tag"
mkdir "$out"
export PYTHONPATH="$W/pure"
cmd=("$W/op/bin/openplatform" eval "$W/examples/submissions/$name"
  --bench robodojo --preset smoke --output-dir "$out/runs")
printf '%q ' "${cmd[@]}" >"$out/command.txt"
printf '\n' >>"$out/command.txt"
env | sort | sed -n '/^EVAL_ENV_TYPE=\|^VK_\|^OPENPI_DATA_HOME=\|^XLA_/p' >"$out/options.txt"
nvidia-smi >"$out/gpu.txt"
git -C "$RD" rev-parse HEAD >"$out/robodojo.txt"
git -C "$RD" submodule status >>"$out/robodojo.txt"
cp "$RD/.openplatform-assets-revision" "$out/assets-revision.txt"
(
  cd "$RD"
  sha256sum Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_[0-9]*.json Assets/Robots/x5/curobo.yml
) >"$out/input-sha256.txt"
"$V/envs/RoboDojo/bin/python" -m pip freeze >"$out/simulator-pip.txt"
start="$(date +%s)"
rc=0
"${cmd[@]}" >"$out/run.json" 2>"$out/run.stderr.log" || rc=$?
echo "$rc" >"$out/exit-code.txt"
echo "$(( $(date +%s)-start ))" >"$out/elapsed-seconds.txt"
exit "$rc"
