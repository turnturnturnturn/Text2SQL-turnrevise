from __future__ import annotations

from typing import Any

from vanna.core.lifecycle import LifecycleHook
from vanna.core.tool import ToolResult

from app.harness.context import get_run_id
from app.harness.models import (
    HarnessBudget,
    HarnessBudgetExceeded,
    HarnessPausedForClarification,
    RunStatus,
    TERMINAL_STATUSES,
)
from app.harness.store import RunStore
from app.rollout.policy import effective_mode


READ_RETRY_ERRORS = frozenset({"semantic_validation", "sql_policy", "database"})


class HarnessLifecycleHook(LifecycleHook):
    """Connect Vanna's tool callbacks to the durable Harness budget and state."""

    def __init__(self, store: RunStore, budget: HarnessBudget, *, mode: str = "shadow"):
        self.store = store
        self.budget = budget
        self.mode = mode

    async def before_tool(self, tool: Any, context: Any) -> None:
        del tool, context
        run_id = get_run_id()
        if run_id is None:
            return
        record = await self.store.get(run_id)
        if record is not None and record.status == RunStatus.NEEDS_CLARIFICATION:
            raise HarnessPausedForClarification("run is waiting for clarification")
        if record is not None and record.status in TERMINAL_STATUSES:
            raise RuntimeError(f"run is already terminal: {record.status}")
        calls = await self.store.increment_tool_calls(run_id)
        if calls > self.budget.max_tool_calls:
            raise HarnessBudgetExceeded("tool call budget exceeded")
        record = await self.store.get(run_id)
        if record is not None and record.status in {
            RunStatus.MODEL_RUNNING,
            RunStatus.GENERATING,
        }:
            await self.store.transition(run_id, RunStatus.TOOL_RUNNING)

    async def after_tool(self, result: ToolResult) -> ToolResult | None:
        run_id = get_run_id()
        if run_id is None:
            return None
        error_type = result.metadata.get("error_type")
        if not result.success and error_type in READ_RETRY_ERRORS:
            retries = await self.store.increment_retries(run_id)
            if retries > self.budget.max_read_retries:
                raise HarnessBudgetExceeded("read retry budget exceeded")
        record = await self.store.get(run_id)
        if record is not None and record.status == RunStatus.TOOL_RUNNING:
            await self.store.transition(
                run_id,
                RunStatus.GENERATING
                if effective_mode("context_harness", self.mode) == "enforce"
                else RunStatus.MODEL_RUNNING,
            )
        return None
