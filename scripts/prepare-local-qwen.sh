#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
SOURCE_MODEL="${QWEN_SOURCE_MODEL:-$HOME/.cache/modelscope/models/Qwen--Qwen3-4B/snapshots/master}"
RUNTIME_DIR="$ROOT_DIR/.runtime"
VENV_DIR="$RUNTIME_DIR/mlx-venv"
TARGET_MODEL="$RUNTIME_DIR/models/qwen3-4b-4bit"
BASE_PYTHON="${PYTHON_BASE:-$ROOT_DIR/agent-service/.venv/bin/python}"

if [[ ! -f "$SOURCE_MODEL/config.json" ]]; then
  echo "Qwen3 source model not found at: $SOURCE_MODEL" >&2
  exit 1
fi

if [[ ! -x "$VENV_DIR/bin/python" ]]; then
  "$BASE_PYTHON" -m venv "$VENV_DIR"
fi

if ! "$VENV_DIR/bin/python" -c \
  'from importlib.metadata import version; assert version("mlx-lm") == "0.31.3"' \
  >/dev/null 2>&1; then
  "$VENV_DIR/bin/python" -m pip install --disable-pip-version-check "mlx-lm==0.31.3"
fi

if [[ -f "$TARGET_MODEL/config.json" && -f "$TARGET_MODEL/model.safetensors" ]]; then
  echo "Converted model already exists: $TARGET_MODEL"
  exit 0
fi

mkdir -p "$(dirname "$TARGET_MODEL")"
"$VENV_DIR/bin/mlx_lm.convert" \
  --hf-path "$SOURCE_MODEL" \
  --mlx-path "$TARGET_MODEL" \
  --quantize \
  --q-bits 4 \
  --q-group-size 64

echo "Converted model ready: $TARGET_MODEL"
