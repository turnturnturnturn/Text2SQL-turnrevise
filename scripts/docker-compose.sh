#!/usr/bin/env bash
set -euo pipefail

if command -v docker >/dev/null 2>&1; then
  exec docker compose "$@"
fi

DOCKER_BIN_DIR="/Applications/Docker.app/Contents/Resources/bin"
if [[ -x "$DOCKER_BIN_DIR/docker" ]]; then
  export PATH="$DOCKER_BIN_DIR:$PATH"
  exec docker compose "$@"
fi

echo "Docker CLI not found. Install and start Docker Desktop first." >&2
exit 1
