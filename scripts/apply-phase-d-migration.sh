#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  source "$ROOT/.env"
  set +a
fi

"$ROOT/scripts/docker-compose.sh" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_ADMIN_USER:-copilot_admin}" \
  -d enterprise_copilot -f /migrations/006_phase_d_observability_rollout.sql
