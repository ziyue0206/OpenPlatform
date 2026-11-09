#!/usr/bin/env bash
# Build the pinned upstream image with the official, checksum-verified CUDA wheel.
set -euo pipefail
checkout="$(cd "${1:?provide the fixed RoboDojo checkout}" && pwd -P)"
image="${2:?provide the local image tag}"
wheel="$(realpath "${3:?provide the PyTorch 2.7.0 CUDA 12.8 wheel}")"
out="${4:?provide a new absolute evidence directory}"
[[ "$out" == /* && "${OMNI_KIT_ACCEPT_EULA:-}" == YES ]] || exit 2
revision="$(git -C "$checkout" rev-parse HEAD)"
case "$revision" in
  f57e1633ffed59761bc8e568cfe7530c4b33381c|ee67a1468510da7624a089164402359f2afc72c8) ;;
  *) echo "Unexpected RoboDojo revision: $revision" >&2; exit 2 ;;
esac
git -C "$checkout" diff --quiet --ignore-submodules=untracked HEAD --
if git -C "$checkout" submodule status | grep -q '^[+U-]'; then
  echo "RoboDojo submodules must match the checkout." >&2
  exit 2
fi
name=torch-2.7.0+cu128-cp311-cp311-manylinux_2_28_x86_64.whl
[[ "${wheel##*/}" == "$name" ]]
printf 'fd6419a487d2b051726f4de75198ecd8c1e7f90c8f5edc45ffffd9d4260777cf  %s\n' \
  "$checkout/Dockerfile" | sha256sum -c -
printf 'c4bbc0b4be60319ba1cefc90be9557b317f0b3c261eeceb96ca6e0343eec56bf  %s\n' \
  "$wheel" | sha256sum -c -
mkdir "$out"
context="$(mktemp -d "${wheel%/*}/.v05-wheel-context.XXXXXX")"
ln "$wheel" "$context/$name"
python3 - "$checkout/Dockerfile" "$out/Dockerfile" "$name" <<'PY'
import sys
from pathlib import Path

source, target = map(Path, sys.argv[1:3])
name = sys.argv[3]
body = source.read_text()
original = 'RUN python -m pip install "numpy==1.26.0" "typing_extensions==4.12.2" "filelock==3.13.1"'
if body.count(original) != 1 or body.count("pip install torch==2.7.0 ") != 1:
    raise ValueError("unexpected Dockerfile dependency layer")
body = body.replace(
    original,
    original.replace("RUN ", "RUN --mount=type=bind,from=v05wheel,target=/v05-wheel,ro ", 1),
)
body = body.replace("pip install torch==2.7.0 ", f'pip install "/v05-wheel/{name}" ')
target.write_text(body)
PY
rc=0
diff -u "$checkout/Dockerfile" "$out/Dockerfile" >"$out/dockerfile.diff" || rc=$?
[[ "$rc" == 1 ]]
git -C "$checkout" submodule status >"$out/submodules.txt"
printf '%s\n' "$revision" >"$out/robodojo-revision.txt"
cmd=(docker build --progress=plain --build-context "v05wheel=$context"
  -t "$image" -f "$out/Dockerfile" "$checkout")
printf '%q ' "${cmd[@]}" >"$out/command.txt"
printf '\n' >>"$out/command.txt"
start="$(date +%s)"
rc=0
"${cmd[@]}" >"$out/build.log" 2>&1 || rc=$?
printf '%s\n' "$rc" >"$out/exit-code.txt"
echo "$(( $(date +%s) - start ))" >"$out/elapsed-seconds.txt"
(( rc == 0 )) || exit "$rc"
docker image inspect "$image" >"$out/image.json"
