# Phase D Experience, Observability, Replay and Rollout Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deliver the Phase D evidence experience, clarification resume, redacted observability, deterministic rollout, and immutable 160-case release gate without changing the existing default answer path.

**Architecture:** PostgreSQL remains the durable coordination boundary. The agent service owns redacted run evidence, trace, resume claims, rollout decisions, and evaluation metadata; the business service remains authoritative for SQL audit text. Request-local correlation and rollout modes use context variables so concurrent requests cannot overwrite each other, while REST endpoints lazily expose owner/admin-safe DTOs.

**Tech Stack:** Python 3.12, FastAPI, Vanna 2, psycopg 3, PostgreSQL, Java 21/Spring Boot, vanilla browser JavaScript, pytest, JUnit, Prometheus text exposition, optional OpenTelemetry OTLP.

## Global Constraints

- New capabilities use `off|shadow|enforce` and default to `shadow`.
- Preserve SSE wire shape, JWT authentication, approvals, read-only SQL, 5-second timeout, and 200-row maximum.
- Never persist or export prompts, result rows, PII, secrets, JWTs, memory bodies, tool arguments, or SQL outside the business audit authority.
- `copilot_readonly` receives no access to Phase D state; `copilot_agent_state` is the only database role granted access.
- Resume is restricted to owner-bound `READ_QUERY` children; writes and approvals fail closed.
- PostgreSQL is the only supported production dialect; tenant defaults to `default`.
- Do not merge this branch automatically.

---

### Task 1: Phase D data foundation and correlated run identity

**Files:**
- Create: `database/migrations/006_phase_d_observability_rollout.sql`
- Create: `database/init/006_apply_phase_d_observability_rollout.sh`
- Create: `agent-service/app/runtime/correlation.py`
- Create: `agent-service/app/runtime/chat_handler.py`
- Modify: `agent-service/app/config.py`
- Modify: `agent-service/app/main.py`
- Modify: `agent-service/app/harness/request.py`
- Test: `agent-service/tests/test_phase_d_migration.py`
- Test: `agent-service/tests/test_correlated_chat_handler.py`

**Interfaces:**
- Produces `bind_run_identity(run_id, correlation_id)` and `current_run_id()` request context accessors.
- Produces the seven Phase D tables and indexes with repeatable `IF NOT EXISTS` DDL.

- [ ] Write migration contract tests that assert every table, constraint, role grant, readonly revoke, token-hash-only column, and idempotent DDL clause.
- [ ] Run the tests and confirm failure because migration 006 is absent.
- [ ] Add the migration and init wrapper, then run the focused tests to green.
- [ ] Write handler tests proving one server UUID is used by SSE and Harness and client IDs cannot replace durable identity.
- [ ] Run the handler tests and confirm the missing correlation implementation failure.
- [ ] Implement correlation context and `CorrelatedChatHandler`, wire it in `main.py`, and make `RequestHarness` consume the bound identity.
- [ ] Run all Python tests and commit `feat: add phase d data and run correlation`.

### Task 2: Stable EvidenceView and lazy Evidence Drawer

**Files:**
- Create: `agent-service/app/evidence/models.py`
- Create: `agent-service/app/evidence/service.py`
- Modify: `agent-service/app/state/api.py`
- Modify: `agent-service/app/business_client.py`
- Modify: `agent-service/app/ui.py`
- Modify: `business-service/src/main/java/com/example/copilot/api/InternalAuditController.java`
- Modify: `business-service/src/main/java/com/example/copilot/repository/AuditEventRepository.java`
- Create: `business-service/src/test/java/com/example/copilot/api/InternalAuditControllerTest.java`
- Test: `agent-service/tests/test_evidence_view.py`
- Test: `agent-service/tests/test_evidence_drawer_contract.py`

**Interfaces:**
- Produces `EvidenceService.get_view(run_id, principal) -> EvidenceView` and service-token-only `GET /internal/audit/runs/{runId}`.
- `EvidenceView.sql.visible` is false for non-admin; only admin may trigger the authority lookup.

- [ ] Write failing owner/admin/cross-user, missing-version, empty-plan, and sensitive-key contract tests.
- [ ] Implement the stable redacted DTO from run, QueryPlan, grounding snapshot, and context manifest only.
- [ ] Write and pass Java tests for service-token-only run audit lookup.
- [ ] Add the Python business client lookup and prove non-admin paths never invoke it.
- [ ] Write failing HTML contract tests for `artifact-opened`, prevent-default, JWT fetch, close/Escape, loading, denied, empty, and narrow-screen states.
- [ ] Implement the Artifact component emission and right-side drawer without changing stream chunks.
- [ ] Run Python and Java focused suites and commit `feat: add evidence view and drawer`.

### Task 3: Clarification child resume

**Files:**
- Modify: `agent-service/app/harness/clarification.py`
- Modify: `agent-service/app/harness/store.py`
- Modify: `agent-service/app/harness/request.py`
- Modify: `agent-service/app/state/api.py`
- Modify: `agent-service/app/context_v2/conversation.py`
- Test: `agent-service/tests/test_clarification_resume.py`

**Interfaces:**
- Produces `ClarificationResolution(child_run_id, resume_token, expires_at, selection)`.
- Produces atomic `claim_resume(child_id, user_id, token_hash)` with HTTP mapping: replay 409, expired 410, malformed/tampered 400, cross-user 404.

- [ ] Write failing tests for valid claim, concurrent claim, replay, expiry, forgery, cross-user, missing conversation, instruction mismatch, fresh budget, and write/approval rejection.
- [ ] Extend the existing clarify transaction to create a five-minute token, persist only SHA-256, and return the plaintext once.
- [ ] Recover the original user message from the owning conversation by matching the parent instruction hash; treat the selection as low-authority evidence.
- [ ] Implement `POST /api/runs/{child}/resume` as the existing SSE media type and atomically transition the child to `CONTEXT_BUILDING` before execution.
- [ ] Run resume concurrency and full Python tests; commit `feat: add one-time clarification resume`.

### Task 4: Redacted trace, Vanna observability, and metrics

**Files:**
- Create: `agent-service/app/observability/models.py`
- Create: `agent-service/app/observability/trace.py`
- Create: `agent-service/app/observability/vanna_provider.py`
- Create: `agent-service/app/observability/metrics.py`
- Modify: `agent-service/app/harness/lifecycle.py`
- Modify: `agent-service/app/state/api.py`
- Modify: `agent-service/app/main.py`
- Test: `agent-service/tests/test_trace_contract.py`
- Test: `agent-service/tests/test_metrics_contract.py`

**Interfaces:**
- Produces ordered `TraceService.append(run_id, event_type, attributes)` with a fixed attribute allowlist.
- Produces low-cardinality `GET /metrics` and owner/admin `GET /api/runs/{id}/trace`.

- [ ] Write failing allowlist, ordering, concurrent sequence, retention, sampling, exporter-failure, attribution, and high-cardinality label tests.
- [ ] Implement local trace persistence first and enforce fail-closed only when mode is enforce and local persistence fails.
- [ ] Add the Vanna provider adapter and optional OTLP export without content capture.
- [ ] Instrument lifecycle stages and terminal closure with `db_copilot.*` attributes and schema/instrumentation versions.
- [ ] Implement Prometheus text metrics without user/run/prompt/SQL labels.
- [ ] Run observability and full Python suites; commit `feat: add redacted traces and metrics`.

### Task 5: Deterministic rollout and automatic downgrade

**Files:**
- Create: `agent-service/app/rollout/models.py`
- Create: `agent-service/app/rollout/policy.py`
- Create: `agent-service/app/rollout/monitor.py`
- Modify: `agent-service/app/state/api.py`
- Modify: `agent-service/app/main.py`
- Test: `agent-service/tests/test_rollout_policy.py`
- Test: `agent-service/tests/test_rollout_monitor.py`

**Interfaces:**
- Produces `RolloutPolicyService.decide(user, tenant, role, route, risk)` using SHA-256 `policy_version:user_id mod 100`.
- Produces admin-only list/rollback APIs and an advisory-lock monitor that only appends policy versions.

- [ ] Write failing precedence, stable-bucket, version, effective-mode, persistence, and admin-auth tests.
- [ ] Implement immutable decision calculation and request-local effective modes for Drawer, OTel, resume, Grounding, and Context Harness.
- [ ] Write failing safety, error-rate, P95, provenance, closure, and multi-worker lock tests.
- [ ] Implement 60-second monitoring with one advisory-lock holder and scope-limited shadow downgrade versions.
- [ ] Verify disabling rollout affects the next request and historical runs remain unchanged.
- [ ] Run rollout and full Python suites; commit `feat: add deterministic rollout controls`.

### Task 6: Immutable release gate, operations docs, and verification

**Files:**
- Create: `evaluation/release_v1.json`
- Create: `scripts/evaluate_release.py`
- Create: `scripts/drill-phase-d-rollback.sh`
- Modify: `scripts/verify-all.sh`
- Create: `docs/architecture/phase-d-experience-observability.md`
- Create: `docs/operations/phase-d-dashboard-alerts.md`
- Create: `docs/operations/phase-d-release-rollback.md`
- Create: `docs/operations/phase-d-data-retention.md`
- Test: `agent-service/tests/test_release_evaluation.py`

**Interfaces:**
- Produces an exactly 160-unique-case immutable manifest and an evaluator that separates `oracle`, `offline`, and nullable `live_model` metrics.

- [ ] Write failing manifest tests for exact counts (60/30/45/15/10), uniqueness, frozen hashes, and no deletion of specialty negatives.
- [ ] Build the immutable manifest from existing suites plus focused interaction/complex cases.
- [ ] Write failing evaluator tests for case output, stage attribution, config freezing, stable-release comparison, and off/shadow/enforce ablation.
- [ ] Implement the evaluator and persist only redacted hashes/results; make live-model metrics `null` when unavailable.
- [ ] Add migration, redaction, resume, rollout, and manifest checks to `verify-all.sh`; keep live E2E behind `RUN_E2E=1`.
- [ ] Add architecture, dashboard, retention, internal-to-GA, and rollback drill documents/scripts.
- [ ] Apply migration twice and compare schema objects/privileges/status dictionaries.
- [ ] Run all Python tests, all Java tests, specialty evaluations, release evaluation, shell syntax checks, and UI browser checks.
- [ ] Commit `feat: complete phase d release gate`, archive the active Phase D plan after all gates pass, and do not merge.

## Acceptance Commands

```bash
PYTHON=/Users/turn/Documents/供应链建模/enterprise-db-copilot/agent-service/.venv/bin/python ./scripts/verify-all.sh
PYTHON=/Users/turn/Documents/供应链建模/enterprise-db-copilot/agent-service/.venv/bin/python ./scripts/evaluate_release.py --check
bash -n database/init/006_apply_phase_d_observability_rollout.sh scripts/drill-phase-d-rollback.sh scripts/verify-all.sh
git diff --check
```
