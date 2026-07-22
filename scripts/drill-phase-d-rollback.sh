#!/usr/bin/env bash
set -euo pipefail

if [[ "${EXECUTE_ROLLBACK:-0}" != "1" ]]; then
  echo "DRY RUN: POST /api/ops/rollouts/{policy_id}/rollback creates a new shadow policy version."
  echo "Set EXECUTE_ROLLBACK=1, API_BASE_URL, ADMIN_JWT and POLICY_ID to run the drill."
  exit 0
fi

: "${API_BASE_URL:?API_BASE_URL is required}"
: "${ADMIN_JWT:?ADMIN_JWT is required}"
: "${POLICY_ID:?POLICY_ID is required}"

curl --fail-with-body --silent --show-error \
  -X POST "${API_BASE_URL%/}/api/ops/rollouts/${POLICY_ID}/rollback" \
  -H "Authorization: Bearer ${ADMIN_JWT}" \
  -H "Content-Type: application/json" \
  --data '{"reason":"phase-d rollback drill"}'
echo
