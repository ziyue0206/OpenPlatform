#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
cd "$W"
export UV_CACHE_DIR="$W/uv-cache" UV_HTTP_TIMEOUT=120
uv venv --python "$V/envs/RoboDojo/bin/python" "$W/op"
uv pip install --python "$W/op/bin/python" wheels/*.whl
"$W/op/bin/openplatform" list
# Pi_0's official shell launcher parses YAML using the conda base interpreter.
"$V/tools/conda-forge/bin/python" -c 'import yaml' ||
  uv pip install --python "$V/tools/conda-forge/bin/python" 'pyyaml==6.0.3'
