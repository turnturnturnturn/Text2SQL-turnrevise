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
    reset_request_context,
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
    ) -> None:
        if mode not in {"off", "shadow", "enforce"}:
            raise ValueError("mode must be off, shadow, or enforce")
        self.store = store or InMemoryRunStore()
        self.budget = budget or HarnessBudget()
        self.mode = mode

    async def execute_stream(
        self,
        *,
        instruction: str,
        user_id: str,
        conversation_id: str | None,
        operation: Callable[[HarnessRun], AsyncIterator[T]],
        operation_kind: OperationKind | None = None,
    ) -> AsyncIterator[T]:
        run_id = str(uuid.uuid4())
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
            async with asyncio.timeout(self.budget.timeout_seconds):
                if self.mode == "enforce":
                    for stage in (
                        RunStatus.CONTEXT_BUILDING,
                        RunStatus.LINKING,
                        RunStatus.PLANNING,
                        RunStatus.GENERATING,
                    ):
                        await self.store.transition(run_id, stage)
                else:
                    await self.store.transition(run_id, RunStatus.CONTEXT_READY)
                    await self.store.transition(run_id, RunStatus.MODEL_RUNNING)
                async for item in operation(run):
                    yield item
                current = await self.store.get(run_id)
                if (
                    current is not None
                    and current.status == RunStatus.NEEDS_CLARIFICATION
                ):
                    return
                if self.mode == "enforce" and current is not None:
                    if current.status == RunStatus.GENERATING:
                        await self.store.transition(run_id, RunStatus.VALIDATING)
                        await self.store.transition(run_id, RunStatus.EXECUTING)
                        await self.store.transition(run_id, RunStatus.VERIFYING)
                elif current is not None and current.status != RunStatus.VERIFYING:
                    await self.store.transition(run_id, RunStatus.VERIFYING)
                await self.store.transition(run_id, RunStatus.COMPLETED)
        except HarnessPausedForClarification:
            current = await self.store.get(run_id)
            if current is None or current.status != RunStatus.NEEDS_CLARIFICATION:
                raise
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

    async def _finish_failed(self, run_id: str, failure_type: str) -> None:
        record = await self.store.get(run_id)
        if record is not None and record.status not in TERMINAL_STATUSES:
            await self.store.transition(
                run_id, RunStatus.FAILED, failure_type=failure_type
            )

    async def _finish_cancelled(self, run_id: str) -> None:
        record = await self.store.get(run_id)
        if record is not None and record.status not in TERMINAL_STATUSES:
            await self.store.transition(
                run_id, RunStatus.CANCELLED, failure_type="cancelled"
            )
