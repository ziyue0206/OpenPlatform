# Source from a clean shell on the V05 acceptance host.
export W="${V06_WORK:-/mnt/openplatform-v05/work/v06}"
export V="${V05_WORK:-$HOME/openplatform-v05}"
export RD="$V/src/RoboDojo"
export PATH="$V/tools/conda-forge/bin:$V/tools/vla/bin:$PATH"
export CONDA_ENVS_PATH="$V/envs" CONDA_PKGS_DIRS="$V/tools/conda-pkgs"
export OMNI_KIT_ACCEPT_EULA=YES
export VK_ICD_FILENAMES=/etc/vulkan/icd.d/nvidia_icd.json
export VK_DRIVER_FILES="$VK_ICD_FILENAMES"
export OPENPI_DATA_HOME="$W/openpi-cache"
export XLA_PYTHON_CLIENT_MEM_FRACTION=0.3
export EVAL_ENV_TYPE=sim
