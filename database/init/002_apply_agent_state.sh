#!/usr/bin/env bash
set -euo pipefail

psql -v ON_ERROR_STOP=1 \
  --username "$POSTGRES_USER" \
  --dbname "$POSTGRES_DB" \
  --set="agent_state_password=${AGENT_STATE_PASSWORD:-copilot_agent_state_dev}" \
  --file=/migrations/002_agent_state.sql
