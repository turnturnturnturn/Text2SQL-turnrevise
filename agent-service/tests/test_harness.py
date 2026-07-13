from __future__ import annotations

import asyncio
from datetime import timedelta

import pytest

from app.harness import (
    HarnessBudget,
    HarnessBudgetExceeded,
    InMemoryRunStore,
    InvalidRunTransition,
    RequestHarness,
    RunRecord,
    RunStatus,
    get_instruction_hash,
    get_instruction_text,
    get_run_id,
)
from app.harness.models import utc_now


async def collect(stream):
    return [item async for item in stream]


@pytest.mark.asyncio
async def test_successful_stream_records_lifecycle_and_request_context():
    store = InMemoryRunStore()
    harness = RequestHarness(store)
    observed = {}

    async def operation(run):
        observed["run_id"] = get_run_id()
        observed["hash"] = get_instruction_hash()
        observed["text"] = get_instruction_text()
        assert run.run_id == observed["run_id"]
        yield "ok"

    result = await collect(
        harness.execute_stream(
            instruction="查询本月销售额",
            user_id="analyst-1",
            conversation_id="conversation-1",
            operation=operation,
        )
    )

    assert result == ["ok"]
    record = await store.get(observed["run_id"])
    assert record is not None
    assert record.status == RunStatus.COMPLETED
    assert record.instruction_hash == observed["hash"]
    assert observed["text"] == "查询本月销售额"
    assert [step.status for step in await store.list_steps(record.run_id)] == [
        RunStatus.RECEIVED,
        RunStatus.CONTEXT_READY,
        RunStatus.MODEL_RUNNING,
        RunStatus.VERIFYING,
        RunStatus.COMPLETED,
    ]
    assert get_run_id() is None
    assert get_instruction_text() is None


@pytest.mark.asyncio
async def test_tool_stage_can_return_to_model_stage():
    store = InMemoryRunStore()
    harness = RequestHarness(store)
    observed_run_id = None

    async def operation(run):
        nonlocal observed_run_id
        observed_run_id = run.run_id
        await run.mark_tool_running()
        await run.mark_model_running()
        yield 1

    assert await collect(
        harness.execute_stream(
            instruction="query",
            user_id="user",
            conversation_id=None,
            operation=operation,
        )
    ) == [1]
    steps = await store.list_steps(observed_run_id)
    assert [step.status for step in steps][3:5] == [
        RunStatus.TOOL_RUNNING,
        RunStatus.MODEL_RUNNING,
    ]


@pytest.mark.asyncio
async def test_invalid_terminal_transition_is_rejected():
    store = InMemoryRunStore()
    record = RunRecord("run-1", "user", None, "a" * 64)
    await store.create(record)
    await store.transition("run-1", RunStatus.FAILED, failure_type="test")

    with pytest.raises(InvalidRunTransition):
        await store.transition("run-1", RunStatus.COMPLETED)


@pytest.mark.asyncio
async def test_operation_exception_marks_run_failed():
    store = InMemoryRunStore()
    harness = RequestHarness(store)
    observed_run_id = None

    async def operation(run):
        nonlocal observed_run_id
        observed_run_id = run.run_id
        yield "partial"
        raise RuntimeError("model failed")

    stream = harness.execute_stream(
        instruction="query",
        user_id="user",
        conversation_id=None,
        operation=operation,
    )
    with pytest.raises(RuntimeError, match="model failed"):
        await collect(stream)

    record = await store.get(observed_run_id)
    assert record is not None
    assert record.status == RunStatus.FAILED
    assert record.failure_type == "RuntimeError"


@pytest.mark.asyncio
async def test_timeout_marks_run_failed():
    store = InMemoryRunStore()
    harness = RequestHarness(store, budget=HarnessBudget(timeout_seconds=0.01))
    observed_run_id = None

    async def operation(run):
        nonlocal observed_run_id
        observed_run_id = run.run_id
        await asyncio.sleep(0.05)
        yield "late"

    with pytest.raises(TimeoutError):
        await collect(
            harness.execute_stream(
                instruction="query",
                user_id="user",
                conversation_id=None,
                operation=operation,
            )
        )

    record = await store.get(observed_run_id)
    assert record is not None
    assert record.status == RunStatus.FAILED
    assert record.failure_type == "timeout"


@pytest.mark.asyncio
async def test_stale_run_recovery_only_fails_incomplete_runs():
    store = InMemoryRunStore()
    stale = RunRecord("stale", "user", None, "a" * 64)
    complete = RunRecord("complete", "user", None, "b" * 64)
    fresh = RunRecord("fresh", "user", None, "c" * 64)
    stale.updated_at = utc_now() - timedelta(hours=1)
    await store.create(stale)
    await store.create(complete)
    await store.transition("complete", RunStatus.FAILED, failure_type="existing")
    await store.create(fresh)

    count = await store.fail_incomplete_runs(before=utc_now() - timedelta(minutes=5))

    assert count == 1
    assert (await store.get("stale")).failure_type == "process_restarted"
    assert (await store.get("complete")).failure_type == "existing"
    assert (await store.get("fresh")).status == RunStatus.RECEIVED


@pytest.mark.asyncio
async def test_tool_and_retry_budgets_fail_closed():
    store = InMemoryRunStore()
    harness = RequestHarness(
        store,
        budget=HarnessBudget(max_tool_calls=1, max_read_retries=0),
    )
    observed_run_id = None

    async def operation(run):
        nonlocal observed_run_id
        observed_run_id = run.run_id
        await run.mark_tool_running()
        await run.mark_model_running()
        await run.mark_tool_running()
        yield "unreachable"

    with pytest.raises(HarnessBudgetExceeded, match="tool call"):
        await collect(
            harness.execute_stream(
                instruction="query",
                user_id="user",
                conversation_id=None,
                operation=operation,
            )
        )

    record = await store.get(observed_run_id)
    assert record is not None
    assert record.status == RunStatus.FAILED
    assert record.tool_call_count == 2
    assert record.failure_type == "HarnessBudgetExceeded"


@pytest.mark.asyncio
async def test_closed_stream_is_marked_cancelled():
    store = InMemoryRunStore()
    harness = RequestHarness(store)
    observed_run_id = None

    async def operation(run):
        nonlocal observed_run_id
        observed_run_id = run.run_id
        yield "first"
        yield "second"

    stream = harness.execute_stream(
        instruction="query",
        user_id="user",
        conversation_id=None,
        operation=operation,
    )
    assert await anext(stream) == "first"
    await stream.aclose()

    record = await store.get(observed_run_id)
    assert record is not None
    assert record.status == RunStatus.CANCELLED
