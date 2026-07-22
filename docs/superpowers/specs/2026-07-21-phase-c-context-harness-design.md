# Phase C Context Compiler v2 and Harness v2 Design

## Status

- Date: 2026-07-21
- Decision: approved by delegated user authority to self-review and confirm plans
- Baseline: `deaa529` (`108` Python tests passing)
- Scope source: `docs/plans/active/2026-07-vnext-grounding-harness-context-prd.md`, phase C

## Goal

Extend the phase B grounding evidence chain with a budgeted, provenance-complete context compiler and a resumable read-only Harness. When grounding contains a material ambiguity, the system must ask one minimal question, accept a source-backed choice, and continue in a child run without weakening SQL, approval, JWT, audit, or data-retention controls.

## Alternatives Considered

### 1. Replace the current Harness and prompt assembly in one cutover

This produces the cleanest final code but combines persistence, API, model context, state-machine, and recovery risk. A defect would affect every request and rollback would require a code deployment.

### 2. Additive v2 modules behind `off|shadow|enforce` (selected)

New compiler, conversation state, clarification, checkpoint, and child-run components have explicit interfaces. `shadow` compiles and records v2 artifacts while preserving current answers; `enforce` activates fail-closed ambiguity and lifecycle behavior. Rollback is a configuration change.

### 3. Deliver Context Compiler only and defer Harness behavior

This is smaller, but it cannot meet the phase C clarification and non-terminal closure criteria. It also leaves context artifacts disconnected from run checkpoints.

## Release Contract

`CONTEXT_HARNESS_V2_MODE=off|shadow|enforce`, default `shadow`.

- `off`: current conversation filter, memory enhancer, and Harness behavior remain authoritative.
- `shadow`: v2 manifests, structured state, ambiguity decisions, and checkpoints are persisted, but existing model answers are not blocked.
- `enforce`: unresolved material ambiguity transitions to `NEEDS_CLARIFICATION`; only an authorized clarification child run may continue.

The existing `GROUNDING_V2_MODE` remains independent. Context v2 consumes the most recent grounding snapshot when present and degrades to a provenance-tagged empty grounding partition when absent.

## Architecture

### Context Compiler

`app/context_v2` owns immutable models and deterministic compilation:

- `ContextItem`: stable id, partition, content, source id/hash, trust, priority, token estimate, mandatory flag, and optional expiry.
- `ContextManifest`: policy version, run id, total budget, per-partition budget/usage, included items, pruned items, and pruning reasons.
- `CompiledContext`: ordered prompt sections plus its manifest.
- `ContextCompiler`: validates provenance, applies irreversible partition order, rejects lower-layer instruction escalation, estimates tokens deterministically, and prunes optional items by priority and trust.

Mandatory safety/tool and current-request partitions cannot be pruned. Every included item has source provenance. If mandatory items exceed the global budget, enforce mode fails closed; shadow mode records the overflow and preserves the old path.

### Structured Conversation State

`ConversationState` is a derived, versioned artifact, never a replacement for raw messages. It contains confirmed constraints, open questions, decisions, rejected options, result references, source message ids, summary version, compiler/model id, and creation time.

The initial compactor is deterministic and extractive. It removes raw tool output and keeps only hashes/artifact references. Failed compaction returns no state, so the current six-turn filter remains available. State versions are append-only and a previous version can be selected for replay.

### Memory Trust

User confirmation and factual eligibility are separated. A memory has a `validity` state:

- `ACTIVE`: eligible if confirmed, unexpired, and non-conflicting.
- `CONFLICTED`: never enters model context; produces an ambiguity signal.
- `SUPERSEDED`: retained for audit but not recalled.
- `INVALID`: known incorrect and never recalled.

Existing confirmed memories default to `ACTIVE` for backward compatibility. Evaluation fixtures mark known wrong memories `INVALID`. A shared `conflict_key` allows a newer confirmed memory to supersede an older one deterministically. Source hash and validity metadata are retained in prompt provenance. Candidate, rejected, expired, invalid, conflicted, or superseded memories never enter enforce-mode context.

### Uncertainty Gate and Clarification

The gate consumes grounding ambiguities, candidate margins, join status, evidence conflicts, and memory conflicts. It returns `LOW`, `MEDIUM`, `HIGH`, or `BLOCKED` plus deterministic reasons.

An enforce-mode `HIGH` result creates one clarification card containing exactly one question and two or three source-backed options. The parent run transitions to `NEEDS_CLARIFICATION`, which is a closed waiting state. `POST /api/runs/{id}/clarify` validates run ownership, option id, expiry, and one-time use, then creates a new child run with a new instruction hash and `parent_run_id`. The selected option is request evidence only and is never stored as long-term memory.

### Harness v2 and Checkpoints

The state machine adds `CONTEXT_BUILDING`, `LINKING`, `NEEDS_CLARIFICATION`, `PLANNING`, `GENERATING`, `VALIDATING`, and `EXECUTING`. Compatibility helper methods map the old tool loop to valid v2 transitions.

Each phase boundary writes an immutable checkpoint containing artifact references and hashes, never raw rows or secrets. Startup closes stale non-terminal parents as `FAILED/PROCESS_RESTART`. Only read-only query runs with a safe checkpoint may create a recovery child. Business actions and approval runs are explicitly non-recoverable and never replayed.

Cancellation is ownership checked and idempotent. Terminal runs remain immutable.

## Persistence and APIs

Migration `005_context_harness_v2.sql` adds idempotent tables/columns for:

- `context_manifests`
- `conversation_states`
- `run_artifacts`
- `clarification_requests`
- parent/child and operation-kind fields on `agent_runs`
- validity/source/conflict metadata on `memories`

APIs:

- `GET /api/runs/{id}/context-manifest`: owner or admin; content is redacted, admin receives pruning metadata.
- `POST /api/runs/{id}/clarify`: owner only; creates one read-only child run.
- `POST /api/runs/{id}/cancel`: owner or admin; terminal-safe and idempotent.

The SSE wire format and current endpoints remain unchanged.

## Failure Handling

- Missing provenance: exclude optional item; fail closed for mandatory item in enforce mode.
- Token overflow: prune optional low-priority items; never prune safety or current request.
- Compaction failure: fall back to recent six turns.
- Ambiguity without two valid source-backed options: `BLOCKED`, not a fabricated question.
- Reused, expired, cross-user, or tampered clarification: reject without child creation.
- Persistence failure in enforce mode: no SQL execution; shadow mode records a local diagnostic and preserves current behavior.
- Recovery request for a write-capable run: reject and retain the failed parent.

## Testing and Acceptance

- Context provenance coverage: 100% for included items.
- Critical constraint recall after compaction: at least 95% on the fixed suite.
- Clarification-required recognition: at least 85%; unnecessary clarification at most 15%.
- Known incorrect, conflicted, expired, candidate, rejected, superseded, and cross-user memory exposure: 0.
- Stale non-terminal closure after restart: 100%.
- Business write recovery/replay: 0.
- Existing Python, Java, grounding, memory, JWT, SQL, approval, and SSE behavior must not regress.

## Scope Boundaries

Phase C does not add the evidence drawer, semantic-asset write APIs, OpenTelemetry export, multi-tenant catalog administration, arbitrary model-generated summaries, or automatic business-write recovery. Those remain phase D or later.

