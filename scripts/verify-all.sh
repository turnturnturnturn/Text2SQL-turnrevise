#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

echo "[verify] Open-source release boundary"
"$ROOT/scripts/oss-preflight.sh"

if [[ -x "$ROOT/agent-service/.venv/bin/python" ]]; then
  PYTHON="$ROOT/agent-service/.venv/bin/python"
else
  PYTHON="${PYTHON:-python3}"
fi

echo "[verify] Python tests"
(
  cd "$ROOT/agent-service"
  "$PYTHON" -m pytest
)

echo "[verify] Gold-memory retrieval suite"
"$PYTHON" "$ROOT/scripts/evaluate_memory.py" --check

echo "[verify] Grounding v2 linking suite"
"$PYTHON" "$ROOT/scripts/evaluate_grounding.py" --check

echo "[verify] Context Compiler v2 and clarification suite"
"$PYTHON" "$ROOT/scripts/evaluate_context.py" --check

echo "[verify] Phase D immutable 160-case release gate"
"$PYTHON" "$ROOT/scripts/evaluate_release.py" --check

echo "[verify] Java tests"
if [[ -x "$ROOT/business-service/mvnw" ]]; then
  (cd "$ROOT/business-service" && ./mvnw test)
elif command -v mvn >/dev/null 2>&1; then
  (cd "$ROOT/business-service" && mvn test)
else
  echo "[verify] Maven is not on PATH; building the Docker test stage (mvn package runs tests)"
  docker build --target build -t enterprise-db-copilot-business-test "$ROOT/business-service"
fi

if [[ "${RUN_E2E:-0}" != "1" ]]; then
  echo "[verify] E2E skipped (set RUN_E2E=1 to enable)"
  exit 0
fi

: "${DATABASE_URL:?DATABASE_URL is required when RUN_E2E=1}"
: "${EVAL_PASSWORD:?EVAL_PASSWORD is required when RUN_E2E=1}"

echo "[verify] Live Text2SQL, safety, audit and optional Harness evaluation"
EVAL_ARGS=(
  --database-url "$DATABASE_URL"
  --live-agent
)
if [[ -n "${HARNESS_INPUT:-}" ]]; then
  EVAL_ARGS+=(--harness-input "$HARNESS_INPUT")
fi
"$PYTHON" "$ROOT/scripts/evaluate_retrieval.py" "${EVAL_ARGS[@]}"
