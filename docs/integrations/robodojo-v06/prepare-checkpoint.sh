#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
name=RoboDojo-sim-arx_x5-joint-0
root="$W/checkpoints/ckpt/RoboDojo/Pi_0/$name"
test -s "$root/60000/assets/arx_x5_sim/norm_stats.json"
test -d "$root/60000/params"
parent="$RD/XPolicyLab/policy/Pi_0/checkpoints"
mkdir -p "$parent"
link="$parent/$name"
if [[ -e "$link" || -L "$link" ]]; then
  [[ "$(realpath "$link")" == "$(realpath "$root")" ]]
else
  ln -s "$root" "$link"
fi
mkdir -p "$W/evidence/pi0-provenance"
out="$W/evidence/pi0-provenance"
printf '%s\n' 786cc2c41d2b85c0bc3d4f83ecf224bd5e81fe33 >"$out/checkpoint-revision.txt"
realpath "$link/60000" >"$out/checkpoint-path.txt"
cd "$root/60000"
find params assets/arx_x5_sim -type f -print0 | sort -z | xargs -0 sha256sum >"$out/checkpoint-sha256.txt"
cp assets/arx_x5_sim/norm_stats.json "$out/norm_stats.json"
cp "$RD/XPolicyLab/policy/Pi_0/deploy.yml" "$out/deploy.yml"
sha256sum "$RD/XPolicyLab/policy/Pi_0/openpi/uv.lock" "$OPENPI_DATA_HOME/big_vision/paligemma_tokenizer.model" \
  >"$out/policy-input-sha256.txt"
"$W/pi0/.venv/bin/python" -c 'import jax, numpy, pydantic, yaml; print(jax.__version__, numpy.__version__, pydantic.__version__, yaml.__version__)' >"$out/versions.txt"
uv pip freeze --python "$W/pi0/.venv/bin/python" >"$out/policy-pip.txt"
