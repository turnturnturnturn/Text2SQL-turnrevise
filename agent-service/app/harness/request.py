from __future__ import annotations

import asyncio
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
    RunRecord,
    RunStatus,
)
from app.harness.store import InMemoryRunStore, RunStore


T = TypeVar("T")


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


class RequestHarness(Generic[T]):
    """Deterministic lifecycle and timeout boundary around an Agent stream."""

    def __init__(
        self,
        store: RunStore | None = None,
        *,
        budget: HarnessBudget | None = None,
    ) -> None:
        self.store = store or InMemoryRunStore()
        self.budget = budget or HarnessBudget()

    async def execute_stream(
        self,
        *,
        instruction: str,
        user_id: str,
        conversation_id: str | None,
        operation: Callable[[HarnessRun], AsyncIterator[T]],
    ) -> AsyncIterator[T]:
        run_id = str(uuid.uuid4())
        tokens = bind_request_context(run_id, instruction)
        instruction_hash = get_instruction_hash()
        assert instruction_hash is not None
        record = RunRecord(
            run_id=run_id,
            user_id=user_id,
            conversation_id=conversation_id,
            instruction_hash=instruction_hash,
        )
        run = HarnessRun(run_id, self.store, self.budget)
        try:
            await self.store.create(record)
            async with asyncio.timeout(self.budget.timeout_seconds):
                await self.store.transition(run_id, RunStatus.CONTEXT_READY)
                await self.store.transition(run_id, RunStatus.MODEL_RUNNING)
                async for item in operation(run):
                    yield item
                current = await self.store.get(run_id)
                if current is not None and current.status != RunStatus.VERIFYING:
                    await self.store.transition(run_id, RunStatus.VERIFYING)
                await self.store.transition(run_id, RunStatus.COMPLETED)
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
        if record is not None and record.status not in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            await self.store.transition(
                run_id, RunStatus.FAILED, failure_type=failure_type
            )

    async def _finish_cancelled(self, run_id: str) -> None:
        record = await self.store.get(run_id)
        if record is not None and record.status not in {
            RunStatus.COMPLETED,
            RunStatus.FAILED,
            RunStatus.CANCELLED,
        }:
            await self.store.transition(
                run_id, RunStatus.CANCELLED, failure_type="cancelled"
            )
