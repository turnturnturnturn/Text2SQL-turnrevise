#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

forbidden_paths='(^|/)(\.runtime|\.venv|\.venv-mlx|models|modelscope)(/|$)|(^|/)(prepare-local-qwen|start-local-qwen)\.sh$'
if git ls-files | rg -n "$forbidden_paths"; then
  echo "[oss-preflight] tracked local model/runtime artifact found" >&2
  exit 1
fi

if git grep -n -I -E 'BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|AKIA[0-9A-Z]{16}|sk-[A-Za-z0-9]{20,}' -- . ':!docs/superpowers/**'; then
  echo "[oss-preflight] high-confidence secret pattern found" >&2
  exit 1
fi

echo "[oss-preflight] tracked-file boundary passed"
