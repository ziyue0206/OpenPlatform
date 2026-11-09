#!/usr/bin/env bash
set -euo pipefail
W="${V07_WORK:-/mnt/openplatform-v05/work/v07}"
V="${V05_WORK:-$HOME/openplatform-v05}"
RD="$V/src/RoboDojo"
export PYTHONPATH="$W/pure"
export OMNI_KIT_ACCEPT_EULA=YES
export VK_ICD_FILENAMES=/etc/vulkan/icd.d/nvidia_icd.json
export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
export PATH="$V/tools/conda-forge/bin:$PATH"
cd "$W"
mkdir -p evidence
{
  git -C "$RD" rev-parse HEAD
  git -C "$RD" submodule status
  cat "$RD/.openplatform-assets-revision"
  sha256sum "$RD/Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_0.json"
  nvidia-smi
  "$V/envs/RoboDojo/bin/python" -m pip freeze
} >evidence/environment.txt
sha256sum wheels/*.whl >evidence/wheels-sha256.txt
op=/mnt/openplatform-v05/work/v06/op/bin
V05_WORK="$V" "$op/python" docs/integrations/robodojo-v07/run-acceptance.py "$W" "$op/openplatform"
nvidia-smi >evidence/gpu-after.txt
