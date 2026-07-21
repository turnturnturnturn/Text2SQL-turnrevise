from __future__ import annotations

import asyncio
import re
import uuid
from collections.abc import AsyncIterator, Callable
from dataclasses import dataclass
from typing import Generic, TypeVar

from app.harness.context import (
    bind_request_context,
    get_instruction_hash,
    reset_instruction_hash,
    reset_request_context,
    set_instruction_hash,
)
from app.harness.models import (
    HarnessBudget,
    HarnessBudgetExceeded,
    HarnessPausedForClarification,
    OperationKind,
    RunRecord,
    RunStatus,
    TERMINAL_STATUSES,
)
from app.harness.store import InMemoryRunStore, RunStore
from app.runtime.correlation import current_run_id


T = TypeVar("T")


_APPROVAL_COMMAND = re.compile(r"^/(?:confirm-action|cancel-action)\b")
_WRITE_COMMAND = re.compile(r"^/(?:confirm-memory|reject-memory)\b")
_WRITE_INTENT = re.compile(
    r"(?:创建|新建|新增|修改|更新|取消|删除|作废).{0,12}(?:订单|草稿|状态|记忆)"
    r"|(?:订单|草稿|状态|记忆).{0,12}(?:创建|新建|新增|修改|更新|取消|删除|作废)"
    r"|\b(?:create|update|cancel|delete)\b.{0,20}\b(?:order|draft|status|memory)\b",
    re.IGNORECASE,
)


def classify_operation_kind(instruction: str) -> OperationKind:
    """Conservative pre-run classification for recovery and checkpoint policy."""
    stripped = instruction.strip()
    if _APPROVAL_COMMAND.search(stripped):
        return OperationKind.APPROVAL
    if _WRITE_COMMAND.search(stripped) or _WRITE_INTENT.search(stripped):
        return OperationKind.BUSINESS_WRITE
    return OperationKind.READ_QUERY


@dataclass(frozen=True)
class HarnessRun:
    run_id: str
    store: RunStore
    budget: HarnessBudget

    async def mark_tool_running(self) -> None:
        tool_calls = await self.store.increment_tool_calls(self.run_id)
        if tool_calls > self.budget.max_tool_calls:
            raise HarnessBudgetExceeded("tool call budget exceeded")
        await self.store.transition(self.run_id, RunStatus.TOOL_RUNNING)

    async def mark_model_running(self) -> None:
        await self.store.transition(self.run_id, RunStatus.MODEL_RUNNING)

    async def mark_verifying(self) -> None:
        await self.store.transition(self.run_id, RunStatus.VERIFYING)

    async def record_read_retry(self) -> None:
        retries = await self.store.increment_retries(self.run_id)
        if retries > self.budget.max_read_retries:
            raise HarnessBudgetExceeded("read retry budget exceeded")

    async def checkpoint(
        self,
        stage: RunStatus,
        artifacts: list[dict],
        *,
        safe_to_resume: bool = True,
    ):
        return await self.store.add_checkpoint(
            self.run_id,
            stage=stage,
            artifacts=artifacts,
            safe_to_resume=safe_to_resume,
        )


class RequestHarness(Generic[T]):
    """Deterministic lifecycle and timeout boundary around an Agent stream."""

    def __init__(
        self,
        store: RunStore | None = None,
        *,
        budget: HarnessBudget | None = None,
        mode: str = "shadow",
        trace_service=None,
    ) -> None:
        if mode not in {"off", "shadow", "enforce"}:
            raise ValueError("mode must be off, shadow, or enforce")
        self.store = store or InMemoryRunStore()
        self.budget = budget or HarnessBudget()
        self.mode = mode
        self.trace_service = trace_service

    async def _trace(self, record: RunRecord, event_type: str, status: str) -> None:
        if self.trace_service is None:
            return
        await self.trace_service.append(record.run_id, event_type, {
            "correlation_id": record.correlation_id,
            "tenant_id": "default",
            "route": "chat",
            "status": status,
            "harness_mode": self.mode,
        })

    async def execute_stream(
        self,
        *,
        instruction: str,
        user_id: str,
        conversation_id: str | None,
        operation: Callable[[HarnessRun], AsyncIterator[T]],
        operation_kind: OperationKind | None = None,
    ) -> AsyncIterator[T]:
        run_id = current_run_id() or str(uuid.uuid4())
        tokens = bind_request_context(
            run_id, instruction, conversation_id=conversation_id
        )
        instruction_hash = get_instruction_hash()
        assert instruction_hash is not None
        record = RunRecord(
            run_id=run_id,
            user_id=user_id,
            conversation_id=conversation_id,
            instruction_hash=instruction_hash,
            operation_kind=operation_kind or classify_operation_kind(instruction),
        )
        run = HarnessRun(run_id, self.store, self.budget)
        try:
            await self.store.create(record)
            await self._trace(record, "request_received", RunStatus.RECEIVED.value)
            async with asyncio.timeout(self.budget.timeout_seconds):
                if self.mode == "enforce":
                    for stage, event_type in (
                        (RunStatus.CONTEXT_BUILDING, "context_compiled"),
                        (RunStatus.LINKING, "schema_linked"),
                        (RunStatus.PLANNING, "plan_validated"),
                        (RunStatus.GENERATING, "sql_generated"),
                    ):
                        await self.store.transition(run_id, stage)
                        await self._trace(record, event_type, stage.value)
                else:
                    await self.store.transition(run_id, RunStatus.CONTEXT_READY)
                    await self._trace(record, "context_compiled", RunStatus.CONTEXT_READY.value)
                    await self.store.transition(run_id, RunStatus.MODEL_RUNNING)
                async for item in operation(run):
                    yield item
                current = await self.store.get(run_id)
                if (
                    current is not None
                    and current.status == RunStatus.NEEDS_CLARIFICATION
                ):
                    await self._trace(record, "clarification_requested", RunStatus.NEEDS_CLARIFICATION.value)
                    return
                if self.mode == "enforce" and current is not None:
                    if current.status == RunStatus.GENERATING:
                        await self.store.transition(run_id, RunStatus.VALIDATING)
                        await self._trace(record, "guard_decided", RunStatus.VALIDATING.value)
                        await self.store.transition(run_id, RunStatus.EXECUTING)
                        await self._trace(record, "sql_executed", RunStatus.EXECUTING.value)
                        await self.store.transition(run_id, RunStatus.VERIFYING)
                elif current is not None and current.status != RunStatus.VERIFYING:
                    await self.store.transition(run_id, RunStatus.VERIFYING)
                await self.store.transition(run_id, RunStatus.COMPLETED)
                await self._trace(record, "result_verified", RunStatus.VERIFYING.value)
                await self._trace(record, "run_completed", RunStatus.COMPLETED.value)
        except HarnessPausedForClarification:
            current = await self.store.get(run_id)
            if current is None or current.status != RunStatus.NEEDS_CLARIFICATION:
                raise
            await self._trace(record, "clarification_requested", RunStatus.NEEDS_CLARIFICATION.value)
            return
        except TimeoutError:
            await self._finish_failed(run_id, "timeout")
            raise
        except asyncio.CancelledError:
            await self._finish_cancelled(run_id)
            raise
        except GeneratorExit:
            await self._finish_cancelled(run_id)
            raise
        except Exception as exc:
            await self._finish_failed(run_id, type(exc).__name__)
            raise
        finally:
            reset_request_context(tokens)

    async def fail_incomplete_runs(self) -> int:
        """Mark streams interrupted by an earlier process lifetime as failed."""
        return await self.store.fail_incomplete_runs()

    async def execute_resumed_stream(
        self,
        *,
        child: RunRecord,
        instruction: str,
        operation: Callable[[HarnessRun], AsyncIterator[T]],
    ) -> AsyncIterator[T]:
        """Continue an atomically claimed read child with a fresh request budget."""
        if child.operation_kind != OperationKind.READ_QUERY:
            raise ValueError("resume only supports READ_QUERY")
        if child.status != RunStatus.CONTEXT_BUILDING:
            raise ValueError("resume child must be atomically claimed first")
        tokens = bind_request_context(
            child.run_id, instruction, conversation_id=child.conversation_id
        )
        hash_token = set_instruction_hash(child.instruction_hash)
        run = HarnessRun(child.run_id, self.store, self.budget)
        try:
            async with asyncio.timeout(self.budget.timeout_seconds):
                for stage in (RunStatus.LINKING, RunStatus.PLANNING, RunStatus.GENERATING):
                    await self.store.transition(child.run_id, stage)
                async for item in operation(run):
                    yield item
                current = await self.store.get(child.run_id)
                if current is not None and current.status == RunStatus.NEEDS_CLARIFICATION:
                    return
                current = await self.store.get(child.run_id)
                if current is not None and current.status == RunStatus.GENERATING:
                    await self.store.transition(child.run_id, RunStatus.VALIDATING)
                    await self.store.transition(child.run_id, RunStatus.EXECUTING)
                    await self.store.transition(child.run_id, RunStatus.VERIFYING)
                elif current is not None and current.status != RunStatus.VERIFYING:
                    await self.store.transition(child.run_id, RunStatus.VERIFYING)
                await self.store.transition(child.run_id, RunStatus.COMPLETED)
        except TimeoutError:
            await self._finish_failed(child.run_id, "timeout")
            raise
        except (asyncio.CancelledError, GeneratorExit):
            await self._finish_cancelled(child.run_id)
            raise
        except Exception as exc:
            await self._finish_failed(child.run_id, type(exc).__name__)
            raise
        finally:
            reset_instruction_hash(hash_token)
            reset_request_context(tokens)

    async def _finish_failed(self, run_id: str, failure_type: str) -> None:
        record = await self.store.get(run_id)
        if record is not None and record.status not in TERMINAL_STATUSES:
            await self.store.transition(
                run_id, RunStatus.FAILED, failure_type=failure_type
            )
            await self._trace(record, "run_failed", RunStatus.FAILED.value)

    async def _finish_cancelled(self, run_id: str) -> None:
        record = await self.store.get(run_id)
        if record is not None and record.status not in TERMINAL_STATUSES:
            await self.store.transition(
                run_id, RunStatus.CANCELLED, failure_type="cancelled"
            )
            await self._trace(record, "run_cancelled", RunStatus.CANCELLED.value)
