#!/usr/bin/env bash
set -euo pipefail
recipe_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
step="${1:?usage: recipe.sh source|install|policy|assets|docker-build|conda-smoke|docker-smoke CHECKOUT [NEW_EVIDENCE_DIR]}"
checkout="${2:?provide an absolute checkout path}"
[[ "$checkout" == /* ]] || { echo "checkout must be absolute" >&2; exit 2; }

# Read only the fixed values shipped beside this script.
pin() {
  python3 - "$recipe_dir/versions.json" "$@" <<'PY'
import json
import sys

with open(sys.argv[1], encoding="utf-8") as lock:
    value = json.load(lock)
for key in sys.argv[2:]:
    value = value[key]
print(value)
PY
}

check_source() {
  if [[ "$(git rev-parse HEAD)" != "$(pin robodojo revision)" ]] ||
     ! git diff --quiet --ignore-submodules=untracked HEAD --; then
    echo "RoboDojo checkout is at the wrong revision or has tracked changes." >&2
    return 1
  fi
  for sub in XPolicyLab third_party/IsaacLab third_party/curobo; do
    expected="$(pin robodojo submodules "$sub")"
    actual="$(git submodule status -- "$sub")"
    [[ "$actual" == " $expected $sub" || "$actual" == " $expected $sub "* ]] || {
      printf 'Unexpected submodule status: %s\n' "$actual" >&2
      return 1
    }
    if ! git -C "$sub" diff --quiet HEAD --; then
      printf 'Submodule has tracked changes: %s\n' "$sub" >&2
      return 1
    fi
  done
}

ensure_env() {
  local name="$1" exists
  exists="$(conda env list --json | python3 -c '
import json, sys
from pathlib import Path
print(any(Path(path).name == sys.argv[1] for path in json.load(sys.stdin)["envs"]))
' "$name")"
  if [[ "$exists" == True ]]; then
    conda run -n "$name" python -c 'import sys; sys.exit(sys.version_info[:2] != (3, 11))'
  else
    conda create -y -n "$name" python=3.11 pip
  fi
}

docker_command() {
  if docker info >/dev/null 2>&1; then
    docker "$@"
  else
    local command
    printf -v command '%q ' docker "$@"
    sg docker -c "$command"
  fi
}

if [[ "$step" == source ]]; then
  if [[ ! -d "$checkout/.git" ]]; then
    git clone --no-checkout "$(pin robodojo url)" "$checkout"
  fi
  cd "$checkout"
  git checkout --detach "$(pin robodojo revision)"
  git submodule update --init --depth 1 --jobs 3
  check_source
  git submodule status
  exit
fi

cd "$checkout"
check_source
case "$step" in
  docker-build)
    [[ "${OMNI_KIT_ACCEPT_EULA:-}" == YES ]] || {
      echo 'Accept NVIDIA Isaac Sim terms and set OMNI_KIT_ACCEPT_EULA=YES before building.' >&2
      exit 2
    }
    docker_command build --tag "$(pin image)" "$PWD"
    docker_command image inspect --format '{{.Id}}' "$(pin image)"
    ;;
  install)
    [[ "${OMNI_KIT_ACCEPT_EULA:-}" == YES ]] || {
      echo 'Accept NVIDIA Isaac Sim terms and set OMNI_KIT_ACCEPT_EULA=YES before installing.' >&2
      exit 2
    }
    eval "$(conda shell.bash hook)"
    ensure_env RoboDojo
    conda activate RoboDojo
    export PIP_NO_CACHE_DIR=1 ISAACLAB_RL_FRAMEWORK=none
    export CUDA_HOME="${CUDA_HOME:-/usr/local/cuda}"
    export PATH="$CUDA_HOME/bin:$PATH"
    python -m pip install -r scripts/requirements.txt
    python -m pip install opencv-python-headless==4.11.0.86 pillow matplotlib scipy==1.15.3 scikit-learn numpy==1.26.0
    bash scripts/install.sh --from isaacsim
    check_source
    bash "$recipe_dir/recipe.sh" policy "$checkout"
    ;;
  policy)
    ensure_env demo-policy
    PIP_NO_CACHE_DIR=1 conda run --no-capture-output -n demo-policy \
      bash XPolicyLab/policy/demo_policy/install.sh
    ;;
  assets)
    revision="$(pin assets revision)"
    hf download "$(pin assets dataset)" --repo-type dataset --revision "$revision" \
      --include "$(pin assets include)" --local-dir "$PWD"
    python3 utils/update_embodiment_config_path.py
    test -s Assets/Robots/x5/curobo.yml
    compgen -G 'Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_[0-9]*.json' >/dev/null
    marker="$(mktemp "$PWD/.openplatform-assets-revision.XXXXXX")"
    printf '%s\n' "$revision" >"$marker"
    mv "$marker" .openplatform-assets-revision
    ;;
  conda-smoke|docker-smoke)
    [[ "${OMNI_KIT_ACCEPT_EULA:-}" == YES ]] || {
      echo 'Accept NVIDIA Isaac Sim terms and set OMNI_KIT_ACCEPT_EULA=YES before running.' >&2
      exit 2
    }
    evidence="${3:?provide a new absolute evidence directory}"
    [[ "$evidence" == /* ]] || exit 2
    mkdir "$evidence"
    test "$(cat .openplatform-assets-revision)" = "$(pin assets revision)"
    test -s Assets/Robots/x5/curobo.yml
    nvidia-smi >"$evidence/gpu.txt"
    git rev-parse HEAD >"$evidence/robodojo.txt"
    git submodule status >>"$evidence/robodojo.txt"
    cp .openplatform-assets-revision "$evidence/assets-revision.txt"
    sha256sum Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_[0-9]*.json \
      Assets/Robots/x5/curobo.yml >"$evidence/input-sha256.txt"
    if [[ "$step" == conda-smoke ]]; then
      eval "$(conda shell.bash hook)"
      conda activate RoboDojo
      for env_name in RoboDojo demo-policy; do
        conda list -n "$env_name" --explicit >"$evidence/$env_name-conda.txt"
        conda run -n "$env_name" python -m pip freeze >"$evidence/$env_name-pip.txt"
      done
      run_id="v05-conda-$(date -u +%Y%m%dT%H%M%SZ)-$$"
      rc=0
      bash scripts/robodojo.sh doctor --task stack_bowls --env-cfg arx_x5 \
        --policy-dir XPolicyLab/policy/demo_policy --policy-env demo-policy \
        >"$evidence/doctor.log" 2>&1 || rc=$?
      echo "$rc" >"$evidence/doctor-exit-code.txt"
      (( rc == 0 )) || exit "$rc"
      cmd=(bash scripts/robodojo.sh eval --policy-dir XPolicyLab/policy/demo_policy
        --task stack_bowls --ckpt demo --policy-env demo-policy --eval-env RoboDojo
        --env-cfg arx_x5 --action-type joint --seed 0 --policy-gpu 0 --env-gpu 0 --eval-num 1)
      export ROBODOJO_RUN_ID="$run_id" EVAL_ENV_TYPE=sim
      result="$PWD/eval_result/RoboDojo/stack_bowls/demo_policy/arx_x5/0_ckpt_name=demo,action_type=joint/$run_id/_result.json"
    else
      export ROBODOJO_IMAGE="$(pin image)" ROBODOJO_CONDA_ENV=demo-policy
      export ROBODOJO_SMOKE_TASK=stack_bowls ROBODOJO_SMOKE_ACTION_TYPE=joint
      export ROBODOJO_SMOKE_ENV_CFG=arx_x5 ROBODOJO_SMOKE_EVAL_NUM=1
      export ROBODOJO_SKIP_BUILD="${ROBODOJO_SKIP_BUILD:-0}"
      if [[ -n "${ROBODOJO_EXPECTED_IMAGE_ID:-}" ]]; then
        [[ "$ROBODOJO_SKIP_BUILD" == 1 ]]
        if [[ "$(docker_command image inspect --format '{{.Id}}' "$ROBODOJO_IMAGE")" != "$ROBODOJO_EXPECTED_IMAGE_ID" ]]; then
          echo "The Docker tag no longer identifies the expected smoke-tested image." >&2
          exit 2
        fi
      fi
      status=docker/smoke_logs/status_latest.txt
      previous_status="$(readlink "$status" 2>/dev/null || true)"
      {
        printf 'network=host\nipc=host\ngpus=all\n'
        printf '%s\n' "$PWD/Assets:/workspace/RoboDojo/Assets:ro"
        if [[ "$PWD" != /workspace/RoboDojo ]]; then
          printf '%s\n' "$PWD/Assets:$PWD/Assets:ro"
        fi
        printf '%s\n' "$PWD/eval_result:/workspace/RoboDojo/eval_result"
        for cache in .local/share/ov .cache/{ov,warp,nvidia,pip} .nv .nvidia-omniverse; do
          if [[ -d "$HOME/$cache" ]]; then printf '%s\n' "$HOME/$cache:/root/$cache"; fi
        done
      } >"$evidence/mounts.txt"
      cmd=(bash docker/smoke_docker.sh run)
    fi
    printf '%q ' "${cmd[@]}" >"$evidence/command.txt"
    printf '\n' >>"$evidence/command.txt"
    env | sort | sed -n '/^ROBODOJO_\|^EVAL_ENV_TYPE=\|^VK_ICD_FILENAMES=\|^VK_DRIVER_FILES=/p' \
      >"$evidence/options.txt"
    start="$(date +%s)"
    rc=0
    "${cmd[@]}" >"$evidence/smoke.log" 2>&1 || rc=$?
    printf '%s\n' "$rc" >"$evidence/exit-code.txt"
    echo "$(( $(date +%s) - start ))" >"$evidence/elapsed-seconds.txt"
    if [[ "$step" == docker-smoke ]]; then
      # Select only a status file from this attempt, even when the script fails early.
      if [[ -f "$status" && "$(readlink "$status")" != "$previous_status" ]]; then
        run="$(sed -n 's/^run_id=//p' "$status" | tail -1)"
        cp "$status" "$evidence/status.txt"
        for part in run build server eval; do
          log="docker/smoke_logs/${part}_$run.log"
          if [[ -f "$log" ]]; then cp "$log" "$evidence/$part.log"; fi
        done
        result="$(sed -n 's/^result_json=//p' "$status" | tail -1)"
      fi
      docker_command image inspect "$ROBODOJO_IMAGE" \
        >"$evidence/image.json" 2>"$evidence/image-error.log" || true
    fi
    if [[ -n "${result:-}" && -f "$result" ]]; then
      cp "$result" "$evidence/_result.json"
      for video in "${result%/*}"/episode_*.mp4; do
        if [[ -s "$video" ]]; then cp "$video" "$evidence/"; fi
      done
    fi
    (( rc == 0 )) || exit "$rc"
    verify_args=("$evidence")
    if [[ "$step" == docker-smoke ]]; then
      docker_command run --rm --entrypoint conda "$ROBODOJO_IMAGE" list -n RoboDojo --explicit \
        >"$evidence/RoboDojo-conda.txt"
      docker_command run --rm --entrypoint conda "$ROBODOJO_IMAGE" run -n RoboDojo \
        python -m pip freeze >"$evidence/RoboDojo-pip.txt"
      conda run -n demo-policy python -m pip freeze >"$evidence/demo-policy-pip.txt"
      verify_args+=("$PWD")
    fi
    python3 "$recipe_dir/verify.py" "${verify_args[@]}" >"$evidence/verified.json"
    ;;
  *) echo "Unknown recipe step: $step" >&2; exit 2 ;;
esac
