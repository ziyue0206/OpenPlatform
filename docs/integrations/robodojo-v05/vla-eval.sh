#!/usr/bin/env bash
# Run after the Conda and upstream Docker smokes have been accepted.
set -euo pipefail
recipe="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
stage="${1:?usage: vla-eval.sh as-shipped|matched UPSTREAM_CHECKOUT EVIDENCE_PARENT}"
vla="$(cd "${2:?provide fixed vla-eval checkout}" && pwd -P)"
out="${3:?provide a new absolute evidence directory}"
[[ "$out" == /* && "${OMNI_KIT_ACCEPT_EULA:-}" == YES ]] || exit 2
export ROBODOJO_ASSETS="${ROBODOJO_ASSETS:?provide the prepared absolute Assets path}"
[[ "$ROBODOJO_ASSETS" == /* && -d "$ROBODOJO_ASSETS" ]] || exit 2
[[ "$(git -C "$vla" rev-parse HEAD)" == 6cc3e1bd9fc4bed26fb0a3aabd342c5fed11ca09 ]]
mkdir "$out"
export HARNESS_VERSION=0.7.0
nvidia-smi >"$out/gpu.txt"
git -C "$vla" rev-parse HEAD >"$out/vla-eval-revision.txt"
python -m pip freeze >"$out/host-pip.txt"
printf '%s\n' "$ROBODOJO_ASSETS" >"$out/assets-path.txt"
cp "${ROBODOJO_ASSETS%/Assets}/.openplatform-assets-revision" "$out/assets-revision.txt"
cd "$vla"

case "$stage" in
  as-shipped)
    # Build the upstream published recipe locally per its licensing instructions.
    source="${VLA_SHIPPED_ROBODOJO:?provide the separate ee67a14 checkout}"
    [[ "$(git -C "$source" rev-parse HEAD)" == ee67a1468510da7624a089164402359f2afc72c8 ]]
    git -C "$source" submodule status >"$out/robodojo-submodules.txt"
    if grep -q '^[+U-]' "$out/robodojo-submodules.txt"; then exit 2; fi
    if [[ -n "${VLA_SHIPPED_VERIFIED_BASE_ID:-}" ]]; then
      [[ "$(docker image inspect --format '{{.Id}}' robodojo:v05-ee67a14)" == "$VLA_SHIPPED_VERIFIED_BASE_ID" ]]
      printf '%s\n' "$VLA_SHIPPED_VERIFIED_BASE_ID" >"$out/reused-base-id.txt"
    else
      docker build -t robodojo:v05-ee67a14 "$source" >"$out/base-build.log" 2>&1
    fi
    docker image inspect robodojo:v05-ee67a14 >"$out/base-image.json"
    bash docker/build.sh robodojo --base-image robodojo:v05-ee67a14 \
      --accept-license robodojo >"$out/build.log" 2>&1
    docker image inspect ghcr.io/allenai/vla-evaluation-harness/robodojo:latest >"$out/image.json"
    config=configs/benchmarks/robodojo/smoke_test.yaml
    cp "$config" "$out/config.yaml"
    cmd=(vla-eval test -c "$config")
    ;;
  matched)
    base=robodojo:v00-f57e163
    expected_id="${V05_VERIFIED_IMAGE_ID:?provide the accepted upstream Docker image ID}"
    [[ "$(docker image inspect --format '{{.Id}}' "$base")" == "$expected_id" ]]
    docker image inspect "$base" >"$out/base-image.json"
    bash docker/build.sh robodojo --base-image "$base" --tag v05-f57e163 \
      --accept-license robodojo >"$out/build.log" 2>&1
    image=ghcr.io/allenai/vla-evaluation-harness/robodojo:v05-f57e163
    docker image inspect "$image" >"$out/image.json"
    docker run --rm --entrypoint conda "$image" run -n RoboDojo python -m pip freeze \
      >"$out/image-pip.txt"
    cp "$recipe/matched.yaml" "$out/config.yaml"
    cp "$recipe/demo_server.yaml" "$out/server.yaml"
    if curl -fsS --max-time 2 http://127.0.0.1:8000/config >/dev/null 2>&1; then
      echo "A model server is already listening on 127.0.0.1:8000" >&2
      exit 1
    fi
    (
      cd "$recipe"
      exec setsid vla-eval serve -c demo_server.yaml
    ) >"$out/server.log" 2>&1 &
    server_pid=$!
    trap 'kill -TERM -- "-$server_pid" 2>/dev/null || true; wait "$server_pid" 2>/dev/null || true' EXIT
    for ((attempt = 0; attempt < 30; attempt++)); do
      kill -0 "$server_pid"
      if curl -fsS --max-time 2 http://127.0.0.1:8000/config \
        -o "$out/server-config.json" 2>/dev/null; then break; fi
      sleep 1
    done
    (( attempt < 30 ))
    cmd=(vla-eval run -c "$out/config.yaml" --output-dir "$out/results")
    ;;
  *) echo "Unknown vla-eval stage: $stage" >&2; exit 2 ;;
esac

printf '%q ' "${cmd[@]}" >"$out/command.txt"
printf '\n' >>"$out/command.txt"
start="$(date +%s)"
rc=0
"${cmd[@]}" >"$out/run.log" 2>&1 || rc=$?
printf '%s\n' "$rc" >"$out/exit-code.txt"
echo "$(( $(date +%s) - start ))" >"$out/elapsed-seconds.txt"
(( rc == 0 )) || exit "$rc"
if [[ "$stage" == matched ]]; then
  if [[ -d "$out/results" ]]; then
    docker run --rm --entrypoint chown -v "$out/results:/results" "$image" \
      -R "$(id -u):$(id -g)" /results
  fi
  dbs=("$out"/results/recording-*.sqlite)
  [[ ${#dbs[@]} -eq 1 && -s "${dbs[0]}" ]]
  vla-eval export "${dbs[0]}" -o "$out/export" >"$out/export.log" 2>&1
  python - "${dbs[0]}" "$ROBODOJO_ASSETS" >"$out/verified.json" <<'PY'
import hashlib
import json
import sqlite3
import sys
from pathlib import Path

database, assets = map(Path, sys.argv[1:])
with sqlite3.connect(database.as_uri() + "?mode=ro", uri=True) as conn:
    [(task, failure, metrics)] = conn.execute(
        "SELECT task_name, failure_reason, metrics FROM episode_results"
    )
if task != "stack_bowls" or failure:
    raise ValueError(f"unexpected episode result for {task}: {failure}")
metrics = json.loads(metrics)
layout = metrics["layout_id"]
if type(layout) is not int or layout < 0:
    raise ValueError(f"invalid layout_id: {layout}")
layout_file = assets / f"Eval_Layout/RoboDojo/arx_x5/0/stack_bowls_{layout}.json"
print(json.dumps({
    "effective_episodes": 1,
    "layout_id": layout,
    "layout_sha256": hashlib.sha256(layout_file.read_bytes()).hexdigest(),
    "metrics": metrics,
}, indent=2))
PY
fi
