#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

if [[ -f "$ROOT/.env" ]]; then
  set -a
  # Local developer secrets are needed only as psql variables; the file is gitignored.
  source "$ROOT/.env"
  set +a
fi

"$ROOT/scripts/docker-compose.sh" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_ADMIN_USER:-copilot_admin}" \
  -d enterprise_copilot -f /migrations/001_hybrid_retrieval_knowledge.sql

"$ROOT/scripts/docker-compose.sh" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_ADMIN_USER:-copilot_admin}" \
  -d enterprise_copilot \
  --set="agent_state_password=${AGENT_STATE_PASSWORD:-copilot_agent_state_dev}" \
  -f /migrations/002_agent_state.sql
