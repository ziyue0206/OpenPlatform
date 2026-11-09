#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
name="$1"
tag="$2"
case "$name" in
  demo_policy) policy_env=demo-policy; ckpt=demo; gpu=0 ;;
  Pi_0) policy_env="$W/pi0"; ckpt=RoboDojo-sim-arx_x5-joint-0; gpu=1 ;;
  *) exit 2 ;;
esac
out="$W/evidence/$tag"
mkdir "$out"
export OPENPLATFORM_RD_MODE=official OPENPLATFORM_RD_EVIDENCE="$out"
export PYTHONPATH="$W/pure/openplatform/benchmarks/robodojo/hooks:$W/pure"
export ROBODOJO_RUN_ID="v06-$tag-$(date -u +%Y%m%dT%H%M%S)-$$"
eval "$(conda shell.bash hook)"
conda activate RoboDojo
cd "$RD"
cmd=(bash scripts/robodojo.sh eval --policy-dir "XPolicyLab/policy/$name"
  --task stack_bowls --ckpt "$ckpt" --policy-env "$policy_env" --eval-env RoboDojo
  --env-cfg arx_x5 --action-type joint --seed 0 --policy-gpu "$gpu" --env-gpu 0 --eval-num 1)
printf '%q ' "${cmd[@]}" >"$out/command.txt"
printf '\n' >>"$out/command.txt"
env | sort | sed -n '/^ROBODOJO_\|^EVAL_ENV_TYPE=\|^VK_\|^OPENPLATFORM_RD_\|^OPENPI_DATA_HOME=\|^XLA_/p' >"$out/options.txt"
nvidia-smi >"$out/gpu.txt"
git rev-parse HEAD >"$out/robodojo.txt"
git submodule status >>"$out/robodojo.txt"
cp .openplatform-assets-revision "$out/assets-revision.txt"
sha256sum Assets/Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_[0-9]*.json Assets/Robots/x5/curobo.yml >"$out/input-sha256.txt"
python -m pip freeze >"$out/simulator-pip.txt"
start="$(date +%s)"
rc=0
timeout -s TERM -k 60s 50m "${cmd[@]}" >"$out/smoke.log" 2>&1 || rc=$?
echo "$rc" >"$out/exit-code.txt"
echo "$(( $(date +%s)-start ))" >"$out/elapsed-seconds.txt"
native="$RD/eval_result/RoboDojo/stack_bowls/$name/arx_x5/0_ckpt_name=$ckpt,action_type=joint/$ROBODOJO_RUN_ID"
for f in "$native/_result.json" "$native"/episode_*.mp4; do
  if [[ -f "$f" ]]; then cp "$f" "$out/"; fi
done
(( rc == 0 )) || exit "$rc"
grep -q '"kind": "episode_result"' "$out/events.jsonl"
python "$W/pure/openplatform/benchmarks/robodojo/verify.py" "$out" >"$out/verified.json"
