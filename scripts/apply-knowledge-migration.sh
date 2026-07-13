#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

"$ROOT/scripts/docker-compose.sh" exec -T postgres \
  psql -v ON_ERROR_STOP=1 -U "${POSTGRES_ADMIN_USER:-copilot_admin}" \
  -d enterprise_copilot -f /migrations/001_hybrid_retrieval_knowledge.sql
