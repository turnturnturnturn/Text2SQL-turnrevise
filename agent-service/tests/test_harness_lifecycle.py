import pytest
from vanna.core.tool import ToolResult

from app.harness.context import bind_request_context, reset_request_context
from app.harness.lifecycle import HarnessLifecycleHook
from app.harness.models import HarnessBudget, HarnessBudgetExceeded, RunRecord, RunStatus
from app.harness.store import InMemoryRunStore


async def _running_store(run_id: str) -> InMemoryRunStore:
    store = InMemoryRunStore()
    await store.create(RunRecord(run_id, "u1", "c1", "a" * 64))
    await store.transition(run_id, RunStatus.CONTEXT_READY)
    await store.transition(run_id, RunStatus.MODEL_RUNNING)
    return store


@pytest.mark.asyncio
async def test_tool_hook_tracks_state_and_count():
    tokens = bind_request_context("r1", "query")
    try:
        store = await _running_store("r1")
        hook = HarnessLifecycleHook(store, HarnessBudget(max_tool_calls=2))
        await hook.before_tool(object(), object())
        assert (await store.get("r1")).status == RunStatus.TOOL_RUNNING
        await hook.after_tool(ToolResult(success=True, result_for_llm="ok"))
        record = await store.get("r1")
        assert record.status == RunStatus.MODEL_RUNNING
        assert record.tool_call_count == 1
    finally:
        reset_request_context(tokens)


@pytest.mark.asyncio
async def test_tool_hook_enforces_read_retry_budget():
    tokens = bind_request_context("r2", "query")
    try:
        store = await _running_store("r2")
        hook = HarnessLifecycleHook(store, HarnessBudget(max_read_retries=0))
        await hook.before_tool(object(), object())
        with pytest.raises(HarnessBudgetExceeded, match="read retry"):
            await hook.after_tool(
                ToolResult(
                    success=False,
                    result_for_llm="bad sql",
                    metadata={"error_type": "semantic_validation"},
                )
            )
    finally:
        reset_request_context(tokens)
