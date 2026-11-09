#!/usr/bin/env bash
set -euo pipefail
source "$(dirname "${BASH_SOURCE[0]}")/common.sh"
mkdir -p "$OPENPI_DATA_HOME/big_vision"
target="$OPENPI_DATA_HOME/big_vision/paligemma_tokenizer.model"
if [[ ! -s "$target" ]]; then
  curl -fsSL --retry 4 --max-time 120 \
    https://storage.googleapis.com/big_vision/paligemma_tokenizer.model -o "$target.part"
  mv "$target.part" "$target"
fi
sha256sum "$target" >"$W/evidence/tokenizer-sha256.txt"
