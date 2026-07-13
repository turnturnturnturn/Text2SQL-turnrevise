#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="$ROOT_DIR/.runtime/mlx-venv/bin/python"
MODEL="$ROOT_DIR/.runtime/models/qwen3-4b-4bit"
PORT="${MLX_PORT:-8081}"
export HF_HUB_CACHE="${HF_HUB_CACHE:-$ROOT_DIR/.runtime/huggingface-cache}"

if [[ ! -x "$PYTHON" || ! -f "$MODEL/config.json" ]]; then
  echo "Local MLX model is not prepared. Run scripts/prepare-local-qwen.sh first." >&2
  exit 1
fi

mkdir -p "$HF_HUB_CACHE"

echo "Starting Qwen3-4B MLX server on 0.0.0.0:$PORT"
echo "This development endpoint has no authentication; stop it on untrusted networks."
exec "$ROOT_DIR/.runtime/mlx-venv/bin/mlx_lm.server" \
  --model "$MODEL" \
  --host 0.0.0.0 \
  --port "$PORT" \
  --chat-template-args '{"enable_thinking":false}'
