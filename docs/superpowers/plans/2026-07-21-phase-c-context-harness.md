# Phase C Context Compiler v2 and Harness v2 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build provenance-complete context compilation, trustworthy memory selection, source-backed clarification child runs, and safe Harness v2 checkpoints behind a reversible three-state flag.

**Architecture:** Add focused `context_v2` modules and extend the existing Harness/store protocols without replacing Vanna's SSE/tool loop. Persist only redacted manifests, structured state, clarification cards, and artifact hashes; enforce mode fails closed while default shadow mode observes without changing current answers.

**Tech Stack:** Python 3.12, dataclasses, FastAPI, psycopg3, pytest, PostgreSQL migrations, existing Vanna Agent integration.

## Global Constraints

- `CONTEXT_HARNESS_V2_MODE=off|shadow|enforce`; default is `shadow`.
- Existing SSE, JWT roles, approval workflow, SQL Guard, 5-second timeout, and 200-row maximum remain compatible.
- Safety/tool policy and current request are mandatory context partitions and cannot be pruned.
- Every included context item has stable source id and source hash; raw result rows, PII, JWT, and secrets are forbidden.
- Clarification choices never become long-term memory automatically.
- Only read-only query runs can produce recovery children; business writes and approvals are never recovered or replayed.

---

### Task 1: Deterministic Context Compiler and Manifest

**Files:**
- Create: `agent-service/app/context_v2/__init__.py`
- Create: `agent-service/app/context_v2/models.py`
- Create: `agent-service/app/context_v2/compiler.py`
- Test: `agent-service/tests/test_context_compiler_v2.py`

**Interfaces:**
- Produces: `ContextItem`, `ContextPartition`, `ContextManifest`, `CompiledContext`, `ContextCompiler.compile(run_id, items, total_token_budget, mode)`.
- Consumes: no application globals; deterministic token estimator injection is optional.

- [ ] **Step 1: Write failing partition/provenance/budget tests**

```python
def test_compiler_never_prunes_mandatory_layers_and_records_optional_pruning():
    compiled = ContextCompiler(token_estimator=len).compile(
        run_id="r1", total_token_budget=90, mode="enforce", items=[
            item("safety", ContextPartition.SAFETY, "safe", mandatory=True),
            item("request", ContextPartition.REQUEST, "question", mandatory=True),
            item("old", ContextPartition.CONVERSATION, "x" * 100, priority=1),
        ],
    )
    assert [item.item_id for item in compiled.manifest.included_items] == ["safety", "request"]
    assert compiled.manifest.pruned_items[0].reason == "partition_budget_exceeded"
    assert compiled.manifest.provenance_coverage == 1.0
```

- [ ] **Step 2: Run the test and verify failure because `app.context_v2` does not exist**

Run: `cd agent-service && python -m pytest tests/test_context_compiler_v2.py -q`

- [ ] **Step 3: Implement immutable models, partition order, provenance validation, token budgets, and redacted serialization**

Mandatory provenance failure raises `ContextCompilationError` in enforce mode. Optional missing-provenance items are pruned with `missing_provenance`; shadow records overflow/missing provenance without authorizing lower-layer instruction changes.

- [ ] **Step 4: Run focused tests**

Run: `cd agent-service && python -m pytest tests/test_context_compiler_v2.py -q`
Expected: all tests pass.

- [ ] **Step 5: Commit**

```bash
git add agent-service/app/context_v2 agent-service/tests/test_context_compiler_v2.py
git commit -m "feat(context): add provenance-aware context compiler"
```

### Task 2: Structured Conversation State and Trustworthy Memory

**Files:**
- Create: `agent-service/app/context_v2/conversation.py`
- Modify: `agent-service/app/state/models.py`
- Modify: `agent-service/app/state/memory_service.py`
- Modify: `agent-service/app/state/context.py`
- Modify: `agent-service/app/state/repositories.py`
- Test: `agent-service/tests/test_conversation_state.py`
- Test: `agent-service/tests/test_memory_context.py`

**Interfaces:**
- Produces: `ConversationState`, `ConversationStateCompactor.compact(messages)`, `MemoryValidity`, `MemoryService.find_conflicts(...)`.
- Consumes: `ContextItem` for compiler-ready state and memory entries.

- [ ] **Step 1: Write failing tests for provenance-preserving compaction and invalid/conflicting memory exclusion**

```python
@pytest.mark.asyncio
async def test_invalid_confirmed_memory_is_never_recalled():
    service = MemoryService(InMemoryStateRepository())
    memory = await service.create_candidate("u1", "GMV includes DRAFT", validity=MemoryValidity.INVALID)
    await service.confirm("u1", memory.id)
    assert await service.search_confirmed("u1", "GMV DRAFT") == []
```

The compactor test asserts source message ids, version increment, tool-output hash replacement, current-message precedence, and fallback on extractor failure.

- [ ] **Step 2: Run focused tests and verify expected failures**

Run: `cd agent-service && python -m pytest tests/test_conversation_state.py tests/test_memory_context.py -q`

- [ ] **Step 3: Implement structured extractive state and memory validity/conflict rules**

Existing memories default to `ACTIVE`. Expired, `INVALID`, `CONFLICTED`, and `SUPERSEDED` records are excluded before ranking. Confirming a newer record with the same non-empty `conflict_key` supersedes older active records; events record both ids.

- [ ] **Step 4: Run focused and regression tests**

Run: `cd agent-service && python -m pytest tests/test_conversation_state.py tests/test_memory_context.py tests/test_persistent_state.py -q`

- [ ] **Step 5: Commit**

```bash
git add agent-service/app/context_v2/conversation.py agent-service/app/state agent-service/tests/test_conversation_state.py agent-service/tests/test_memory_context.py
git commit -m "feat(memory): add structured state and validity governance"
```

### Task 3: Harness v2 States, Budgets, Artifacts, Checkpoints, and Cancellation

**Files:**
- Modify: `agent-service/app/harness/models.py`
- Modify: `agent-service/app/harness/store.py`
- Modify: `agent-service/app/harness/request.py`
- Modify: `agent-service/app/harness/lifecycle.py`
- Modify: `agent-service/app/harness/__init__.py`
- Modify: `agent-service/app/state/run_store.py`
- Test: `agent-service/tests/test_harness.py`
- Test: `agent-service/tests/test_harness_lifecycle.py`

**Interfaces:**
- Produces: expanded `RunStatus`, `RunArtifact`, `RunCheckpoint`, `OperationKind`, `HarnessRun.checkpoint(...)`, `RunStore.cancel(...)`, and child-aware `RunRecord`.
- Consumes: redacted context/artifact hashes only.

- [ ] **Step 1: Write failing tests for v2 transitions, immutable checkpoints, idempotent cancellation, restart closure, and write recovery rejection**

```python
@pytest.mark.asyncio
async def test_business_write_checkpoint_cannot_create_recovery_child():
    parent = RunRecord("p", "u", None, "a" * 64, operation_kind=OperationKind.BUSINESS_WRITE)
    await store.create(parent)
    await store.fail_incomplete_runs()
    with pytest.raises(UnsafeRecoveryError):
        await store.create_recovery_child("p", "u", "new instruction")
```

- [ ] **Step 2: Run Harness tests and verify the new expectations fail**

Run: `cd agent-service && python -m pytest tests/test_harness.py tests/test_harness_lifecycle.py -q`

- [ ] **Step 3: Extend models/protocols and both memory/PostgreSQL stores**

Preserve old helper behavior by mapping `CONTEXT_READY`, `MODEL_RUNNING`, `TOOL_RUNNING`, and `VERIFYING` through valid v2 transitions. Checkpoints store artifact type, content hash, storage ref, safe-to-resume flag, and creation time.

- [ ] **Step 4: Run focused tests**

Run: `cd agent-service && python -m pytest tests/test_harness.py tests/test_harness_lifecycle.py tests/test_persistent_state.py -q`

- [ ] **Step 5: Commit**

```bash
git add agent-service/app/harness agent-service/app/state/run_store.py agent-service/tests/test_harness.py agent-service/tests/test_harness_lifecycle.py agent-service/tests/test_persistent_state.py
git commit -m "feat(harness): add v2 lifecycle and safe checkpoints"
```

### Task 4: Uncertainty Gate and Clarification Child Runs

**Files:**
- Create: `agent-service/app/harness/uncertainty.py`
- Create: `agent-service/app/harness/clarification.py`
- Modify: `agent-service/app/state/api.py`
- Modify: `agent-service/app/workflow.py`
- Test: `agent-service/tests/test_clarification.py`
- Test: `agent-service/tests/test_state_api.py`

**Interfaces:**
- Produces: `UncertaintyGate.evaluate(signals)`, `ClarificationCard`, `ClarificationService.create(...)`, `ClarificationService.answer(...)`.
- Consumes: source-backed ambiguity options and `RunStore.create_child(...)`.

- [ ] **Step 1: Write failing gate, one-time use, ownership, tamper, expiry, and no-memory-side-effect tests**

```python
@pytest.mark.asyncio
async def test_answer_creates_one_child_with_new_hash_and_does_not_create_memory():
    card = await service.create(parent_run_id="p", user_id="u", ambiguity=ambiguity())
    child = await service.answer("p", "u", card.options[0].option_id)
    assert child.parent_run_id == "p"
    assert child.instruction_hash != parent.instruction_hash
    with pytest.raises(ClarificationAlreadyAnswered):
        await service.answer("p", "u", card.options[0].option_id)
    assert repository.memories == {}
```

- [ ] **Step 2: Run clarification tests and verify failure**

Run: `cd agent-service && python -m pytest tests/test_clarification.py tests/test_state_api.py -q`

- [ ] **Step 3: Implement deterministic gate/cards/service and authenticated endpoints**

Cards contain one question and two or three options, each with stable evidence id/source hash. Fewer than two valid options returns `BLOCKED`. The answer endpoint accepts only an existing option id and derives the child instruction from the parent instruction hash plus selected evidence, without persisting raw parent text.

- [ ] **Step 4: Run focused tests**

Run: `cd agent-service && python -m pytest tests/test_clarification.py tests/test_state_api.py tests/test_action_workflow.py -q`

- [ ] **Step 5: Commit**

```bash
git add agent-service/app/harness/uncertainty.py agent-service/app/harness/clarification.py agent-service/app/state/api.py agent-service/app/workflow.py agent-service/tests/test_clarification.py agent-service/tests/test_state_api.py
git commit -m "feat(harness): add source-backed clarification child runs"
```

### Task 5: Persistence Migration, Feature Flag, and Application Integration

**Files:**
- Create: `database/migrations/005_context_harness_v2.sql`
- Create: `database/init/005_apply_context_harness_v2.sh`
- Create: `scripts/apply-context-migration.sh`
- Modify: `database/init/001_schema.sql`
- Modify: `agent-service/app/config.py`
- Modify: `agent-service/app/main.py`
- Modify: `agent-service/app/state/api.py`
- Modify: `compose.yaml`
- Test: `agent-service/tests/test_config.py`
- Test: `agent-service/tests/test_state_api.py`

**Interfaces:**
- Produces: idempotent v2 schema and runtime wiring for `off|shadow|enforce`.
- Consumes: compiler, conversation state, clarification service, and extended PostgreSQL stores.

- [ ] **Step 1: Write failing flag/default/router integration tests**

Tests assert default `shadow`, invalid values fail fast, off mode does not require v2 persistence, cross-user context manifest access returns 404/403, and redacted manifest output contains no item content.

- [ ] **Step 2: Run integration tests and verify failure**

Run: `cd agent-service && python -m pytest tests/test_config.py tests/test_state_api.py -q`

- [ ] **Step 3: Add idempotent SQL and wire v2 services**

Migration grants only required `agent_state` privileges. `main.py` runs shadow compilation without blocking current output and uses enforce decisions only for read-only query runs. Startup closes stale runs and never automatically executes recovery children.

- [ ] **Step 4: Run migration static checks and integration tests**

Run: `rg -n "IF NOT EXISTS|ADD COLUMN IF NOT EXISTS|GRANT" database/migrations/005_context_harness_v2.sql`

Run: `cd agent-service && python -m pytest tests/test_config.py tests/test_state_api.py tests/test_persistent_state.py -q`

- [ ] **Step 5: Commit**

```bash
git add database agent-service/app/config.py agent-service/app/main.py agent-service/app/state/api.py compose.yaml scripts/apply-context-migration.sh agent-service/tests
git commit -m "feat: integrate phase c persistence and rollout flag"
```

### Task 6: Fixed Context/Clarification Evaluation and Delivery Documentation

**Files:**
- Create: `evaluation/context_cases.json`
- Create: `scripts/evaluate_context.py`
- Modify: `evaluation/memory_cases.json`
- Modify: `agent-service/app/evaluation.py`
- Modify: `scripts/evaluate_memory.py`
- Modify: `scripts/verify-all.sh`
- Modify: `docs/architecture/HARNESS_AND_MEMORY.md`
- Modify: `README.md`
- Move: `docs/plans/active/2026-07-phase-c-context-harness.md` to `docs/plans/completed/2026-07-phase-c-context-harness.md`
- Test: `agent-service/tests/test_evaluation.py`

**Interfaces:**
- Produces: deterministic context/clarification metrics with null for missing denominators and a completed phase receipt.
- Consumes: compiler, gate, and governed memory retrieval.

- [ ] **Step 1: Write failing evaluator tests**

Tests cover provenance coverage, critical constraint recall, required/unnecessary clarification, invalid-memory exposure, restart closure, and zero write recovery. A missing denominator must return `None`, never synthetic 0% or 100%.

- [ ] **Step 2: Run evaluator tests and verify failure**

Run: `cd agent-service && python -m pytest tests/test_evaluation.py -q`

- [ ] **Step 3: Implement at least 15 long-context and 10 clarification fixed cases, update memory wrong fixtures to `INVALID`, and add evaluator to `verify-all.sh`**

- [ ] **Step 4: Run focused evaluation gates**

Run: `./scripts/evaluate_context.py --check`

Run: `./scripts/evaluate_memory.py --check`

- [ ] **Step 5: Run full verification and inspect the complete output**

Run: `./scripts/verify-all.sh`

Expected: all Python and Java tests pass; context, memory, and grounding gates meet their declared thresholds. `RUN_E2E=1` remains separately reported if local model/database credentials are unavailable.

- [ ] **Step 6: Fill the completed delivery receipt with the actual branch, commit SHA, exact test counts, metrics, migration execution status, and remaining risks**

- [ ] **Step 7: Commit**

```bash
git add evaluation scripts agent-service/app/evaluation.py agent-service/tests/test_evaluation.py docs README.md
git commit -m "docs: verify and record phase c delivery"
```

